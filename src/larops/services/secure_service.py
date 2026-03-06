from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command


class SecureServiceError(RuntimeError):
    pass


_SSH_ALLOWED_ROOT_LOGIN_MODES = {"no", "prohibit-password", "yes"}
_SSH_KNOWN_STATES = {"active", "activating", "reloading", "inactive", "failed", "deactivating"}
_SSH_KNOWN_ENABLED = {"enabled", "disabled", "static", "indirect", "generated", "masked"}
_NGINX_SECURITY_PROFILES = {
    "baseline": {
        "login_rate": "5r/m",
        "api_rate": "60r/m",
        "login_burst": 10,
        "api_burst": 120,
        "extra_block_paths": [],
    },
    "strict": {
        "login_rate": "3r/m",
        "api_rate": "30r/m",
        "login_burst": 5,
        "api_burst": 60,
        "extra_block_paths": [
            "/adminer.php",
            "/backup",
            "/backup/",
            "/backups",
            "/backups/",
            "/dump.sql",
            "/dump.sql.gz",
            "/server-info",
            "/server-status",
            "/storage/framework/cache/",
            "/storage/logs/",
            "/vendor/",
        ],
    },
    "api-heavy": {
        "login_rate": "5r/m",
        "api_rate": "180r/m",
        "login_burst": 10,
        "api_burst": 300,
        "extra_block_paths": [],
    },
}
_DEFAULT_LOGIN_ROUTE_KEYS = ["=/login", "~^/password/", "~^/two-factor"]
_DEFAULT_API_ROUTE_KEYS = ["~^/api/"]


def _systemctl_text(args: list[str]) -> str:
    try:
        completed = run_command(["systemctl", *args], check=False)
    except FileNotFoundError:
        return ""
    return (completed.stdout or completed.stderr or "").strip()


def _service_exists(service: str) -> bool:
    active = _systemctl_text(["is-active", service])
    enabled = _systemctl_text(["is-enabled", service])
    return active in _SSH_KNOWN_STATES or enabled in _SSH_KNOWN_ENABLED


def _resolve_reload_service(preferred: str | None, candidates: list[str]) -> str:
    if preferred:
        return preferred
    for candidate in candidates:
        if _service_exists(candidate):
            return candidate
    raise SecureServiceError(f"No known systemd service found for candidates: {', '.join(candidates)}")


def _validate_rate(value: str, *, label: str) -> str:
    cleaned = value.strip()
    if not re.fullmatch(r"\d+[rR]/[smhd]", cleaned):
        raise SecureServiceError(f"{label} must look like 5r/m, 60r/m, or 100r/s.")
    return cleaned.lower()


def _normalize_ssh_principal_list(values: list[str], *, label: str) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        cleaned = raw.strip()
        if not cleaned:
            continue
        if any(character.isspace() for character in cleaned):
            raise SecureServiceError(f"{label} entries cannot contain whitespace: {raw!r}")
        normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _validate_max_startups(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if re.fullmatch(r"\d+", cleaned):
        return cleaned
    if re.fullmatch(r"\d+:\d+:\d+", cleaned):
        return cleaned
    raise SecureServiceError("--max-startups must be an integer or a start:rate:full triplet like 10:30:60.")


def _normalize_block_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in paths:
        cleaned = raw.strip()
        if not cleaned:
            continue
        if not cleaned.startswith("/"):
            raise SecureServiceError(f"Block paths must start with '/': {raw!r}")
        if any(character.isspace() for character in cleaned):
            raise SecureServiceError(f"Block paths cannot contain whitespace: {raw!r}")
        normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def resolve_nginx_security_profile(
    *,
    profile: str,
    login_rate: str | None,
    api_rate: str | None,
    login_burst: int | None,
    api_burst: int | None,
    extra_block_paths: list[str],
) -> dict[str, Any]:
    normalized_profile = profile.strip().lower()
    if normalized_profile not in _NGINX_SECURITY_PROFILES:
        allowed = ", ".join(sorted(_NGINX_SECURITY_PROFILES))
        raise SecureServiceError(f"Unsupported --profile: {profile}. Allowed: {allowed}.")

    defaults = _NGINX_SECURITY_PROFILES[normalized_profile]
    resolved_login_rate = _validate_rate(login_rate or str(defaults["login_rate"]), label="--login-rate")
    resolved_api_rate = _validate_rate(api_rate or str(defaults["api_rate"]), label="--api-rate")
    resolved_login_burst = int(login_burst if login_burst is not None else defaults["login_burst"])
    resolved_api_burst = int(api_burst if api_burst is not None else defaults["api_burst"])
    if resolved_login_burst < 1:
        raise SecureServiceError("--login-burst must be >= 1.")
    if resolved_api_burst < 1:
        raise SecureServiceError("--api-burst must be >= 1.")
    resolved_block_paths = _normalize_block_paths(
        [*defaults.get("extra_block_paths", []), *_normalize_block_paths(extra_block_paths)]
    )
    return {
        "profile": normalized_profile,
        "login_rate": resolved_login_rate,
        "api_rate": resolved_api_rate,
        "login_burst": resolved_login_burst,
        "api_burst": resolved_api_burst,
        "extra_block_paths": resolved_block_paths,
    }


def _snapshot_file(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _restore_file(path: Path, previous: str | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(previous, encoding="utf-8")


def render_sshd_drop_in(
    *,
    port: int | None,
    root_login_mode: str,
    ssh_key_only: bool,
    max_auth_tries: int,
    login_grace_time_seconds: int,
    client_alive_interval_seconds: int,
    client_alive_count_max: int,
    allow_tcp_forwarding: bool,
    allow_agent_forwarding: bool,
    x11_forwarding: bool,
    allow_users: list[str],
    allow_groups: list[str],
    max_startups: str | None,
) -> str:
    lines = [
        "# Managed by LarOps",
        f"PermitRootLogin {root_login_mode}",
        f"MaxAuthTries {max_auth_tries}",
        f"LoginGraceTime {login_grace_time_seconds}",
        f"ClientAliveInterval {client_alive_interval_seconds}",
        f"ClientAliveCountMax {client_alive_count_max}",
        f"AllowTcpForwarding {'yes' if allow_tcp_forwarding else 'no'}",
        f"AllowAgentForwarding {'yes' if allow_agent_forwarding else 'no'}",
        f"X11Forwarding {'yes' if x11_forwarding else 'no'}",
        "PermitEmptyPasswords no",
    ]
    if allow_users:
        lines.append(f"AllowUsers {' '.join(allow_users)}")
    if allow_groups:
        lines.append(f"AllowGroups {' '.join(allow_groups)}")
    if max_startups is not None:
        lines.append(f"MaxStartups {max_startups}")
    if port is not None:
        lines.append(f"Port {port}")
    if ssh_key_only:
        lines.extend(
            [
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "ChallengeResponseAuthentication no",
                "PubkeyAuthentication yes",
            ]
        )
    return "\n".join(lines) + "\n"


def apply_secure_ssh(
    *,
    sshd_drop_in_file: Path,
    sshd_config_file: Path,
    sshd_bin: str,
    port: int | None,
    root_login_mode: str,
    ssh_key_only: bool,
    max_auth_tries: int,
    login_grace_time_seconds: int,
    client_alive_interval_seconds: int,
    client_alive_count_max: int,
    allow_tcp_forwarding: bool,
    allow_agent_forwarding: bool,
    x11_forwarding: bool,
    allow_users: list[str],
    allow_groups: list[str],
    max_startups: str | None,
    reload_service: str | None,
    reload_after_validate: bool,
) -> dict[str, Any]:
    normalized_root_login_mode = root_login_mode.strip().lower()
    if normalized_root_login_mode not in _SSH_ALLOWED_ROOT_LOGIN_MODES:
        allowed = ", ".join(sorted(_SSH_ALLOWED_ROOT_LOGIN_MODES))
        raise SecureServiceError(f"Unsupported --root-login-mode: {root_login_mode}. Allowed: {allowed}.")
    if port is not None and not (1 <= port <= 65535):
        raise SecureServiceError("--port must be between 1 and 65535.")
    if max_auth_tries < 1:
        raise SecureServiceError("--max-auth-tries must be >= 1.")
    if login_grace_time_seconds < 1:
        raise SecureServiceError("--login-grace-time must be >= 1.")
    if client_alive_interval_seconds < 0:
        raise SecureServiceError("--client-alive-interval must be >= 0.")
    if client_alive_count_max < 0:
        raise SecureServiceError("--client-alive-count-max must be >= 0.")
    normalized_allow_users = _normalize_ssh_principal_list(allow_users, label="--allow-user")
    normalized_allow_groups = _normalize_ssh_principal_list(allow_groups, label="--allow-group")
    normalized_max_startups = _validate_max_startups(max_startups)

    body = render_sshd_drop_in(
        port=port,
        root_login_mode=normalized_root_login_mode,
        ssh_key_only=ssh_key_only,
        max_auth_tries=max_auth_tries,
        login_grace_time_seconds=login_grace_time_seconds,
        client_alive_interval_seconds=client_alive_interval_seconds,
        client_alive_count_max=client_alive_count_max,
        allow_tcp_forwarding=allow_tcp_forwarding,
        allow_agent_forwarding=allow_agent_forwarding,
        x11_forwarding=x11_forwarding,
        allow_users=normalized_allow_users,
        allow_groups=normalized_allow_groups,
        max_startups=normalized_max_startups,
    )

    previous = _snapshot_file(sshd_drop_in_file)
    sshd_drop_in_file.parent.mkdir(parents=True, exist_ok=True)
    sshd_drop_in_file.write_text(body, encoding="utf-8")
    try:
        run_command([sshd_bin, "-t", "-f", str(sshd_config_file)], check=True)
        reloaded_service = None
        if reload_after_validate:
            reloaded_service = _resolve_reload_service(reload_service, ["ssh", "sshd"])
            run_command(["systemctl", "reload", reloaded_service], check=True)
    except (ShellCommandError, FileNotFoundError) as exc:
        _restore_file(sshd_drop_in_file, previous)
        raise SecureServiceError(str(exc)) from exc

    return {
        "sshd_drop_in_file": str(sshd_drop_in_file),
        "sshd_config_file": str(sshd_config_file),
        "port": port,
        "root_login_mode": normalized_root_login_mode,
        "ssh_key_only": ssh_key_only,
        "allow_users": normalized_allow_users,
        "allow_groups": normalized_allow_groups,
        "max_startups": normalized_max_startups,
        "reloaded_service": reloaded_service,
    }


def render_nginx_http_security_config(*, login_rate: str, api_rate: str, login_zone_name: str, api_zone_name: str) -> str:
    lines = [
        "# Managed by LarOps",
        "map $request_uri $larops_login_limit_key {",
        '    default "";',
    ]
    for key in _DEFAULT_LOGIN_ROUTE_KEYS:
        lines.append(f"    {key} $binary_remote_addr;")
    lines.extend(
        [
            "}",
            "",
            "map $request_uri $larops_api_limit_key {",
            '    default "";',
        ]
    )
    for key in _DEFAULT_API_ROUTE_KEYS:
        lines.append(f"    {key} $binary_remote_addr;")
    lines.extend(
        [
            "}",
            "",
            f"limit_req_zone $larops_login_limit_key zone={login_zone_name}:10m rate={login_rate};",
            f"limit_req_zone $larops_api_limit_key zone={api_zone_name}:10m rate={api_rate};",
            "",
        ]
    )
    return "\n".join(lines)


def render_nginx_server_security_snippet(
    *,
    login_zone_name: str,
    api_zone_name: str,
    login_burst: int,
    api_burst: int,
    extra_block_paths: list[str],
) -> str:
    lines = [
        "# Managed by LarOps",
        "location ~ /\\.(?!well-known) {",
        "    access_log off;",
        "    log_not_found off;",
        "    return 404;",
        "}",
        "",
        "location ~* ^/(?:\\.env(?:\\..*)?|\\.git|\\.svn|\\.hg|composer\\.(?:json|lock)|package(?:-lock)?\\.json|yarn\\.lock|pnpm-lock\\.yaml|artisan|phpunit\\.xml(?:\\.dist)?|\\.htaccess|\\.htpasswd) {",
        "    access_log off;",
        "    log_not_found off;",
        "    return 404;",
        "}",
        "",
        "location = /wp-login.php { return 404; }",
        "location = /xmlrpc.php { return 404; }",
        "location = /phpmyadmin { return 404; }",
        "location ^~ /phpmyadmin/ { return 404; }",
        "",
    ]
    for block_path in extra_block_paths:
        if block_path.endswith("/"):
            lines.append(f"location ^~ {block_path} {{ return 404; }}")
        else:
            lines.append(f"location = {block_path} {{ return 404; }}")
    if extra_block_paths:
        lines.append("")
    lines.extend(
        [
        f"limit_req zone={login_zone_name} burst={login_burst} nodelay;",
        f"limit_req zone={api_zone_name} burst={api_burst} nodelay;",
        "",
        ]
    )
    return "\n".join(lines)


def inject_nginx_server_include(*, server_config_file: Path, snippet_file: Path) -> bool:
    if not server_config_file.exists() or not server_config_file.is_file():
        raise SecureServiceError(f"Nginx server config file not found: {server_config_file}")
    body = server_config_file.read_text(encoding="utf-8")
    include_line = f"include {snippet_file};"
    if include_line in body:
        return False

    lines = body.splitlines()
    inside_server = False
    depth = 0
    server_start_depth = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        opens = line.count("{")
        closes = line.count("}")
        if not inside_server and re.match(r"^server\b.*\{$", stripped):
            inside_server = True
            server_start_depth = opens - closes
            depth = server_start_depth
            continue
        if inside_server:
            depth += opens - closes
            if depth == 0 and stripped == "}":
                lines.insert(index, f"    {include_line}")
                server_config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
    raise SecureServiceError(f"Could not find a top-level server block in {server_config_file}")


def apply_secure_nginx(
    *,
    http_config_file: Path,
    server_snippet_file: Path,
    server_config_file: Path | None,
    profile: str,
    login_rate: str | None,
    api_rate: str | None,
    login_burst: int | None,
    api_burst: int | None,
    extra_block_paths: list[str],
    nginx_bin: str,
    reload_service: str | None,
    reload_after_validate: bool,
) -> dict[str, Any]:
    profile_config = resolve_nginx_security_profile(
        profile=profile,
        login_rate=login_rate,
        api_rate=api_rate,
        login_burst=login_burst,
        api_burst=api_burst,
        extra_block_paths=extra_block_paths,
    )

    login_zone_name = "larops_login"
    api_zone_name = "larops_api"
    http_body = render_nginx_http_security_config(
        login_rate=str(profile_config["login_rate"]),
        api_rate=str(profile_config["api_rate"]),
        login_zone_name=login_zone_name,
        api_zone_name=api_zone_name,
    )
    server_body = render_nginx_server_security_snippet(
        login_zone_name=login_zone_name,
        api_zone_name=api_zone_name,
        login_burst=int(profile_config["login_burst"]),
        api_burst=int(profile_config["api_burst"]),
        extra_block_paths=list(profile_config["extra_block_paths"]),
    )

    snapshots = {
        http_config_file: _snapshot_file(http_config_file),
        server_snippet_file: _snapshot_file(server_snippet_file),
    }
    if server_config_file is not None:
        snapshots[server_config_file] = _snapshot_file(server_config_file)

    http_config_file.parent.mkdir(parents=True, exist_ok=True)
    server_snippet_file.parent.mkdir(parents=True, exist_ok=True)
    http_config_file.write_text(http_body, encoding="utf-8")
    server_snippet_file.write_text(server_body, encoding="utf-8")

    include_added = False
    try:
        if server_config_file is not None:
            include_added = inject_nginx_server_include(server_config_file=server_config_file, snippet_file=server_snippet_file)
        run_command([nginx_bin, "-t"], check=True)
        reloaded_service = None
        if reload_after_validate:
            reloaded_service = _resolve_reload_service(reload_service, ["nginx"])
            run_command(["systemctl", "reload", reloaded_service], check=True)
    except (ShellCommandError, FileNotFoundError, SecureServiceError) as exc:
        for path, previous in snapshots.items():
            _restore_file(path, previous)
        raise SecureServiceError(str(exc)) from exc

    return {
        "http_config_file": str(http_config_file),
        "server_snippet_file": str(server_snippet_file),
        "server_config_file": str(server_config_file) if server_config_file is not None else None,
        "server_include_added": include_added,
        "profile": str(profile_config["profile"]),
        "login_rate": str(profile_config["login_rate"]),
        "api_rate": str(profile_config["api_rate"]),
        "login_burst": int(profile_config["login_burst"]),
        "api_burst": int(profile_config["api_burst"]),
        "extra_block_paths": list(profile_config["extra_block_paths"]),
        "reloaded_service": reloaded_service,
    }
