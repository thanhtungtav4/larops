from __future__ import annotations

from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.runtime import AppContext
from larops.services.secure_service import (
    SecureServiceError,
    apply_secure_nginx,
    apply_secure_ssh,
    resolve_nginx_security_profile,
    resolve_nginx_hardening_paths,
)

secure_app = typer.Typer(help="Apply preventive host and web hardening controls.")


@secure_app.command("ssh")
def secure_ssh(
    ctx: typer.Context,
    sshd_drop_in_file: Path = typer.Option(
        Path("/etc/ssh/sshd_config.d/larops.conf"),
        "--sshd-drop-in-file",
        help="sshd drop-in file managed by LarOps.",
        dir_okay=False,
    ),
    sshd_config_file: Path = typer.Option(
        Path("/etc/ssh/sshd_config"),
        "--sshd-config-file",
        help="Main sshd config file used for validation.",
        dir_okay=False,
    ),
    sshd_bin: str = typer.Option("sshd", "--sshd-bin", help="sshd binary used for config validation."),
    port: int | None = typer.Option(None, "--port", help="Optional SSH port to enforce in the drop-in."),
    root_login_mode: str = typer.Option(
        "no",
        "--root-login-mode",
        help="PermitRootLogin mode: no|prohibit-password|yes.",
    ),
    ssh_key_only: bool = typer.Option(
        False,
        "--ssh-key-only/--allow-password-auth",
        help="Disable password-based auth and keep pubkey auth only.",
    ),
    max_auth_tries: int = typer.Option(3, "--max-auth-tries", help="MaxAuthTries value."),
    login_grace_time: int = typer.Option(30, "--login-grace-time", help="LoginGraceTime value in seconds."),
    client_alive_interval: int = typer.Option(300, "--client-alive-interval", help="ClientAliveInterval value."),
    client_alive_count_max: int = typer.Option(2, "--client-alive-count-max", help="ClientAliveCountMax value."),
    allow_user: list[str] = typer.Option([], "--allow-user", help="AllowUsers entry. Repeatable."),
    allow_group: list[str] = typer.Option([], "--allow-group", help="AllowGroups entry. Repeatable."),
    max_startups: str | None = typer.Option(
        None,
        "--max-startups",
        help="Optional MaxStartups value, for example 10:30:60.",
    ),
    allow_tcp_forwarding: bool = typer.Option(
        True,
        "--allow-tcp-forwarding/--disable-tcp-forwarding",
        help="Control AllowTcpForwarding.",
    ),
    allow_agent_forwarding: bool = typer.Option(
        False,
        "--allow-agent-forwarding/--disable-agent-forwarding",
        help="Control AllowAgentForwarding.",
    ),
    x11_forwarding: bool = typer.Option(False, "--x11-forwarding/--no-x11-forwarding", help="Control X11Forwarding."),
    reload_service: str | None = typer.Option(None, "--reload-service", help="Optional explicit systemd service name to reload."),
    reload_after_validate: bool = typer.Option(True, "--reload/--no-reload", help="Reload ssh service after validation."),
    apply: bool = typer.Option(False, "--apply", help="Apply SSH hardening."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Secure SSH plan prepared.",
        sshd_drop_in_file=str(sshd_drop_in_file),
        sshd_config_file=str(sshd_config_file),
        sshd_bin=sshd_bin,
        port=port,
        root_login_mode=root_login_mode,
        ssh_key_only=ssh_key_only,
        max_auth_tries=max_auth_tries,
        login_grace_time=login_grace_time,
        client_alive_interval=client_alive_interval,
        client_alive_count_max=client_alive_count_max,
        allow_users=allow_user,
        allow_groups=allow_group,
        max_startups=max_startups,
        allow_tcp_forwarding=allow_tcp_forwarding,
        allow_agent_forwarding=allow_agent_forwarding,
        x11_forwarding=x11_forwarding,
        reload_service=reload_service,
        reload_after_validate=reload_after_validate,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("secure-ssh"):
            result = apply_secure_ssh(
                sshd_drop_in_file=sshd_drop_in_file,
                sshd_config_file=sshd_config_file,
                sshd_bin=sshd_bin,
                port=port,
                root_login_mode=root_login_mode,
                ssh_key_only=ssh_key_only,
                max_auth_tries=max_auth_tries,
                login_grace_time_seconds=login_grace_time,
                client_alive_interval_seconds=client_alive_interval,
                client_alive_count_max=client_alive_count_max,
                allow_users=allow_user,
                allow_groups=allow_group,
                max_startups=max_startups,
                allow_tcp_forwarding=allow_tcp_forwarding,
                allow_agent_forwarding=allow_agent_forwarding,
                x11_forwarding=x11_forwarding,
                reload_service=reload_service,
                reload_after_validate=reload_after_validate,
            )
    except (CommandLockError, SecureServiceError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output("ok", "Secure SSH applied.", result=result)


@secure_app.command("nginx")
def secure_nginx(
    ctx: typer.Context,
    profile: str = typer.Option("baseline", "--profile", help="Hardening profile: baseline|strict|api-heavy."),
    http_config_file: Path | None = typer.Option(
        None,
        "--http-config-file",
        help="HTTP-context Nginx config file for LarOps hardening.",
        dir_okay=False,
    ),
    server_snippet_file: Path | None = typer.Option(
        None,
        "--server-snippet-file",
        help="Server-context Nginx security snippet.",
        dir_okay=False,
    ),
    server_config_file: Path | None = typer.Option(
        None,
        "--server-config-file",
        help="Optional server config file to inject snippet include into.",
        dir_okay=False,
    ),
    nginx_root_config_file: Path | None = typer.Option(
        None,
        "--nginx-root-config-file",
        help="Optional root nginx.conf used to verify EL9 default.d auto-includes.",
        dir_okay=False,
    ),
    login_rate: str | None = typer.Option(None, "--login-rate", help="Optional rate limit override for login/password routes."),
    api_rate: str | None = typer.Option(None, "--api-rate", help="Optional rate limit override for API routes."),
    login_burst: int | None = typer.Option(None, "--login-burst", help="Optional burst override for login rate limit."),
    api_burst: int | None = typer.Option(None, "--api-burst", help="Optional burst override for API rate limit."),
    block_path: list[str] = typer.Option([], "--block-path", help="Extra exact or prefix path to return 404 for. Repeatable."),
    nginx_bin: str = typer.Option("nginx", "--nginx-bin", help="nginx binary used for config validation."),
    reload_service: str | None = typer.Option(None, "--reload-service", help="Optional explicit systemd service name to reload."),
    reload_after_validate: bool = typer.Option(True, "--reload/--no-reload", help="Reload nginx after validation."),
    apply: bool = typer.Option(False, "--apply", help="Apply Nginx hardening."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        effective_profile = resolve_nginx_security_profile(
            profile=profile,
            login_rate=login_rate,
            api_rate=api_rate,
            login_burst=login_burst,
            api_burst=api_burst,
            extra_block_paths=block_path,
        )
    except SecureServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    resolved_paths = resolve_nginx_hardening_paths(
        http_config_file=http_config_file,
        server_snippet_file=server_snippet_file,
        root_config_file=nginx_root_config_file,
    )
    app_ctx.emit_output(
        "ok",
        "Secure Nginx plan prepared.",
        profile=effective_profile["profile"],
        http_config_file=str(resolved_paths["http_config_file"]),
        server_snippet_file=str(resolved_paths["server_snippet_file"]),
        server_config_file=str(server_config_file) if server_config_file else None,
        nginx_root_config_file=str(resolved_paths["root_config_file"]),
        login_rate=effective_profile["login_rate"],
        api_rate=effective_profile["api_rate"],
        login_burst=effective_profile["login_burst"],
        api_burst=effective_profile["api_burst"],
        extra_block_paths=effective_profile["extra_block_paths"],
        nginx_bin=nginx_bin,
        reload_service=reload_service,
        reload_after_validate=reload_after_validate,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("secure-nginx"):
            result = apply_secure_nginx(
                http_config_file=http_config_file,
                server_snippet_file=server_snippet_file,
                server_config_file=server_config_file,
                root_config_file=nginx_root_config_file,
                profile=profile,
                login_rate=login_rate,
                api_rate=api_rate,
                login_burst=login_burst,
                api_burst=api_burst,
                extra_block_paths=block_path,
                nginx_bin=nginx_bin,
                reload_service=reload_service,
                reload_after_validate=reload_after_validate,
            )
    except (CommandLockError, SecureServiceError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output("ok", "Secure Nginx applied.", result=result)
