from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.secure_service import resolve_nginx_hardening_paths
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux
from larops.services.stack_service import StackServiceError, detect_stack_platform
from larops.services.ssl_service import default_cert_file


class NginxSiteServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class NginxSitePaths:
    family: str
    server_config_file: Path
    enabled_site_file: Path
    activation_mode: str


@dataclass(slots=True)
class NginxSiteSnapshot:
    paths: NginxSitePaths
    server_config_body: str | None
    enabled_exists: bool
    enabled_is_symlink: bool
    enabled_target: str | None


def _platform_family() -> str:
    try:
        return detect_stack_platform().family
    except StackServiceError:
        return "debian"


def resolve_nginx_site_paths(domain: str) -> NginxSitePaths:
    family = _platform_family()
    if family == "el9":
        server_config = Path("/etc/nginx/conf.d") / f"{domain}.conf"
        return NginxSitePaths(
            family=family,
            server_config_file=server_config,
            enabled_site_file=server_config,
            activation_mode="direct",
        )

    server_config = Path("/etc/nginx/sites-available") / f"{domain}.conf"
    enabled_site = Path("/etc/nginx/sites-enabled") / f"{domain}.conf"
    return NginxSitePaths(
        family=family,
        server_config_file=server_config,
        enabled_site_file=enabled_site,
        activation_mode="symlink",
    )


def capture_nginx_site_snapshot(domain: str) -> NginxSiteSnapshot:
    paths = resolve_nginx_site_paths(domain)
    server_body = (
        paths.server_config_file.read_text(encoding="utf-8")
        if paths.server_config_file.exists()
        else None
    )
    enabled_exists = paths.enabled_site_file.exists() or paths.enabled_site_file.is_symlink()
    enabled_target: str | None = None
    enabled_is_symlink = paths.enabled_site_file.is_symlink()
    if enabled_is_symlink:
        try:
            enabled_target = str(paths.enabled_site_file.resolve(strict=False))
        except OSError:
            enabled_target = None
    return NginxSiteSnapshot(
        paths=paths,
        server_config_body=server_body,
        enabled_exists=enabled_exists,
        enabled_is_symlink=enabled_is_symlink,
        enabled_target=enabled_target,
    )


def _restore_snapshot(snapshot: NginxSiteSnapshot) -> None:
    paths = snapshot.paths
    if snapshot.server_config_body is None:
        paths.server_config_file.unlink(missing_ok=True)
    else:
        paths.server_config_file.parent.mkdir(parents=True, exist_ok=True)
        paths.server_config_file.write_text(snapshot.server_config_body, encoding="utf-8")

    if paths.activation_mode == "symlink":
        if not snapshot.enabled_exists:
            paths.enabled_site_file.unlink(missing_ok=True)
        elif snapshot.enabled_is_symlink and snapshot.enabled_target:
            paths.enabled_site_file.parent.mkdir(parents=True, exist_ok=True)
            paths.enabled_site_file.unlink(missing_ok=True)
            paths.enabled_site_file.symlink_to(snapshot.enabled_target)
        elif not snapshot.enabled_is_symlink and snapshot.server_config_body is not None:
            paths.enabled_site_file.parent.mkdir(parents=True, exist_ok=True)
            paths.enabled_site_file.write_text(snapshot.server_config_body, encoding="utf-8")


def restore_nginx_site_snapshot(snapshot: NginxSiteSnapshot, *, reload_after_restore: bool = True) -> None:
    _restore_snapshot(snapshot)
    if reload_after_restore:
        try:
            run_command(["nginx", "-t"], check=True)
            run_command(["systemctl", "reload", "nginx"], check=True)
        except (ShellCommandError, FileNotFoundError):
            return


def _resolve_document_root(current_path: Path) -> Path:
    public_path = current_path / "public"
    if public_path.exists():
        return public_path
    return current_path


def _php_fastcgi_pass(*, php_version: str, family: str) -> str:
    if family == "el9":
        return "unix:/run/php-fpm/www.sock"
    normalized = php_version.strip() or "8.3"
    return f"unix:/run/php/php{normalized}-fpm.sock"


def _security_include_line(family: str) -> str | None:
    resolved = resolve_nginx_hardening_paths(
        http_config_file=None,
        server_snippet_file=None,
        root_config_file=None,
    )
    snippet = resolved["server_snippet_file"]
    if not snippet.exists():
        return None
    return f"    include {snippet};"


def _php_location_block(fastcgi_pass: str) -> list[str]:
    return [
        "    location ~ \\.php$ {",
        "        try_files $uri =404;",
        "        include fastcgi_params;",
        "        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;",
        "        fastcgi_param DOCUMENT_ROOT $realpath_root;",
        f"        fastcgi_pass {fastcgi_pass};",
        "    }",
    ]


def render_nginx_site_config(
    *,
    domain: str,
    document_root: Path,
    fastcgi_pass: str,
    family: str,
    https_enabled: bool,
) -> str:
    include_line = _security_include_line(family)
    dotfile_block = [
        "    location ~ /\\.(?!well-known).* {",
        "        deny all;",
        "    }",
    ]
    challenge_block = [
        "    location /.well-known/acme-challenge/ {",
        "        root " + str(document_root) + ";",
        "        try_files $uri =404;",
        "    }",
    ]

    def _server_body(*, tls: bool) -> list[str]:
        lines = [
            "server {",
            "    listen 80;" if not tls else "    listen 443 ssl http2;",
            "    listen [::]:80;" if not tls else "    listen [::]:443 ssl http2;",
            f"    server_name {domain};",
            f"    root {document_root};",
            "    index index.php index.html;",
        ]
        if tls:
            cert_file = default_cert_file(domain)
            key_file = cert_file.parent / "privkey.pem"
            lines += [
                f"    ssl_certificate {cert_file};",
                f"    ssl_certificate_key {key_file};",
                "    ssl_protocols TLSv1.2 TLSv1.3;",
            ]
        if include_line:
            lines.append(include_line)
        lines += challenge_block
        if tls:
            lines += [
                "    location / {",
                "        try_files $uri $uri/ /index.php?$query_string;",
                "    }",
            ]
        else:
            lines += [
                "    location / {",
                "        try_files $uri $uri/ /index.php?$query_string;",
                "    }",
            ]
        lines += _php_location_block(fastcgi_pass)
        lines += dotfile_block
        lines.append("}")
        return lines

    lines = ["# Managed by LarOps"]
    if https_enabled:
        lines += [
            "server {",
            "    listen 80;",
            "    listen [::]:80;",
            f"    server_name {domain};",
        ]
        if include_line:
            lines.append(include_line)
        lines += challenge_block
        lines += [
            "    location / {",
            "        return 301 https://$host$request_uri;",
            "    }",
        ]
        lines += dotfile_block
        lines.append("}")
        lines += _server_body(tls=True)
    else:
        lines += _server_body(tls=False)
    lines.append("")
    return "\n".join(lines)


def _write_site_activation(paths: NginxSitePaths) -> None:
    paths.server_config_file.parent.mkdir(parents=True, exist_ok=True)
    if paths.activation_mode != "symlink":
        return
    paths.enabled_site_file.parent.mkdir(parents=True, exist_ok=True)
    if paths.enabled_site_file.exists() or paths.enabled_site_file.is_symlink():
        paths.enabled_site_file.unlink()
    paths.enabled_site_file.symlink_to(paths.server_config_file)


def _relabel_nginx_paths(paths: NginxSitePaths) -> dict[str, Any]:
    relabel_targets = [paths.server_config_file]
    try:
        return relabel_managed_paths_for_selinux(
            relabel_targets,
            run_command=run_command,
            which=shutil.which,
            roots=[Path("/etc/nginx")],
        )
    except SelinuxServiceError as exc:
        raise NginxSiteServiceError(str(exc)) from exc


def _validate_existing_config(paths: NginxSitePaths, *, force: bool) -> None:
    if not paths.server_config_file.exists():
        return
    body = paths.server_config_file.read_text(encoding="utf-8")
    if body.startswith("# Managed by LarOps"):
        return
    if force:
        return
    raise NginxSiteServiceError(
        f"Nginx site config already exists and is not managed by LarOps: {paths.server_config_file}. "
        "Use --force to overwrite deliberately."
    )


def _reload_nginx() -> None:
    try:
        run_command(["nginx", "-t"], check=True)
        run_command(["systemctl", "reload", "nginx"], check=True)
    except FileNotFoundError as exc:
        raise NginxSiteServiceError(
            "nginx is not installed. Install the web stack with `larops stack install --web --apply` "
            "or install `nginx` manually."
        ) from exc
    except ShellCommandError as exc:
        raise NginxSiteServiceError(str(exc)) from exc


def apply_nginx_site_config(
    *,
    domain: str,
    current_path: Path,
    php_version: str,
    https_enabled: bool,
    force: bool,
) -> dict[str, Any]:
    paths = resolve_nginx_site_paths(domain)
    _validate_existing_config(paths, force=force)
    snapshot = capture_nginx_site_snapshot(domain)
    document_root = _resolve_document_root(current_path)
    config_body = render_nginx_site_config(
        domain=domain,
        document_root=document_root,
        fastcgi_pass=_php_fastcgi_pass(php_version=php_version, family=paths.family),
        family=paths.family,
        https_enabled=https_enabled,
    )

    try:
        paths.server_config_file.parent.mkdir(parents=True, exist_ok=True)
        paths.server_config_file.write_text(config_body, encoding="utf-8")
        _write_site_activation(paths)
        selinux = _relabel_nginx_paths(paths)
        _reload_nginx()
    except (NginxSiteServiceError, OSError) as exc:
        restore_nginx_site_snapshot(snapshot, reload_after_restore=False)
        raise NginxSiteServiceError(str(exc)) from exc

    return {
        "domain": domain,
        "family": paths.family,
        "document_root": str(document_root),
        "server_config_file": str(paths.server_config_file),
        "enabled_site_file": str(paths.enabled_site_file),
        "activation_mode": paths.activation_mode,
        "https_enabled": https_enabled,
        "fastcgi_pass": _php_fastcgi_pass(php_version=php_version, family=paths.family),
        "selinux": selinux,
    }
