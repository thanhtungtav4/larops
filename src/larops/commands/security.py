from __future__ import annotations

import socket
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.host_layout_service import (
    default_fail2ban_filter_file,
    default_fail2ban_jail_file,
    default_fail2ban_log_path,
    default_nginx_access_log_path,
)
from larops.services.security_service import (
    SecurityReportError,
    SecurityServiceError,
    apply_security_install_plan,
    build_security_install_plan,
    build_security_report,
    collect_security_posture,
    collect_security_status,
    determine_security_status_level,
)

security_app = typer.Typer(help="Install and inspect baseline host security controls.")


def _emit(
    app_ctx: AppContext,
    *,
    severity: str,
    event_type: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            message=message,
            metadata=metadata or {},
        )
    )


def _resolve_fail2ban_jail_file(path: Path | None) -> Path:
    return path or default_fail2ban_jail_file()


def _resolve_fail2ban_filter_file(path: Path | None) -> Path:
    return path or default_fail2ban_filter_file()


def _resolve_fail2ban_log_path(path: Path | None) -> Path:
    return path or default_fail2ban_log_path()


def _resolve_nginx_log_path(path: Path | None) -> Path:
    return path or default_nginx_access_log_path()


@security_app.command("install")
def install(
    ctx: typer.Context,
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port to allow in UFW and Fail2ban jail."),
    limit_ssh: bool = typer.Option(True, "--limit-ssh/--no-limit-ssh", help="Enable UFW SSH rate limiting."),
    ufw_logging: str = typer.Option("low", "--ufw-logging", help="UFW logging level: off|on|low|medium|high|full."),
    fail2ban_jail_file: Path | None = typer.Option(
        None,
        "--fail2ban-jail-file",
        help="Fail2ban jail file path.",
        dir_okay=False,
    ),
    fail2ban_filter_file: Path | None = typer.Option(
        None,
        "--fail2ban-filter-file",
        help="Fail2ban filter file path.",
        dir_okay=False,
    ),
    nginx_log_path: Path | None = typer.Option(
        None,
        "--nginx-log-path",
        help="Nginx access log path for scan jail.",
        dir_okay=False,
    ),
    fail2ban_log_path: Path | None = typer.Option(
        None,
        "--fail2ban-log-path",
        help="Fail2ban log file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply security installation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    if ssh_port < 1 or ssh_port > 65535:
        app_ctx.emit_output("error", "SSH port must be between 1 and 65535.")
        raise typer.Exit(code=2)

    resolved_fail2ban_jail_file = _resolve_fail2ban_jail_file(fail2ban_jail_file)
    resolved_fail2ban_filter_file = _resolve_fail2ban_filter_file(fail2ban_filter_file)
    resolved_nginx_log_path = _resolve_nginx_log_path(nginx_log_path)
    resolved_fail2ban_log_path = _resolve_fail2ban_log_path(fail2ban_log_path)

    try:
        plan = build_security_install_plan(
            ssh_port=ssh_port,
            limit_ssh=limit_ssh,
            ufw_logging=ufw_logging,
            fail2ban_jail_path=resolved_fail2ban_jail_file,
            fail2ban_filter_path=resolved_fail2ban_filter_file,
            nginx_log_path=resolved_nginx_log_path,
            fail2ban_log_path=resolved_fail2ban_log_path,
        )
    except SecurityServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(
        "ok",
        "Security install plan prepared.",
        ssh_port=ssh_port,
        limit_ssh=limit_ssh,
        firewall_backend=plan.firewall_backend,
        firewall_commands=plan.firewall_commands,
        fail2ban_jail_file=str(plan.fail2ban_jail_path),
        fail2ban_filter_file=str(plan.fail2ban_filter_path),
        nginx_log_path=str(resolved_nginx_log_path),
        fail2ban_log_path=str(resolved_fail2ban_log_path),
        notes=plan.notes or [],
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="warn",
        event_type="security.install.started",
        message="Security baseline installation started.",
        metadata={
            "ssh_port": ssh_port,
            "limit_ssh": limit_ssh,
            "ufw_logging": ufw_logging,
        },
    )
    try:
        with CommandLock("security-install"):
            result = apply_security_install_plan(plan)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except ShellCommandError as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="security.install.failed",
            message="Security baseline installation failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="security.install.completed",
        message="Security baseline installation completed.",
        metadata={"ssh_port": ssh_port},
    )
    app_ctx.emit_output("ok", "Security installation completed.", result=result)


@security_app.command("status")
def status(
    ctx: typer.Context,
    fail2ban_jail_file: Path | None = typer.Option(
        None,
        "--fail2ban-jail-file",
        help="Fail2ban jail file path.",
        dir_okay=False,
    ),
    fail2ban_filter_file: Path | None = typer.Option(
        None,
        "--fail2ban-filter-file",
        help="Fail2ban filter file path.",
        dir_okay=False,
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_fail2ban_jail_file = _resolve_fail2ban_jail_file(fail2ban_jail_file)
    resolved_fail2ban_filter_file = _resolve_fail2ban_filter_file(fail2ban_filter_file)
    try:
        report = collect_security_status(
            fail2ban_jail_path=resolved_fail2ban_jail_file,
            fail2ban_filter_path=resolved_fail2ban_filter_file,
        )
    except SecurityServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    status_level = determine_security_status_level(report)
    app_ctx.emit_output(status_level, "Security status.", report=report)


@security_app.command("posture")
def posture(
    ctx: typer.Context,
    fail2ban_jail_file: Path | None = typer.Option(
        None,
        "--fail2ban-jail-file",
        help="Fail2ban jail file path.",
        dir_okay=False,
    ),
    fail2ban_filter_file: Path | None = typer.Option(
        None,
        "--fail2ban-filter-file",
        help="Fail2ban filter file path.",
        dir_okay=False,
    ),
    sshd_drop_in_file: Path = typer.Option(
        Path("/etc/ssh/sshd_config.d/larops.conf"),
        "--sshd-drop-in-file",
        help="LarOps-managed sshd drop-in file.",
        dir_okay=False,
    ),
    nginx_http_config_file: Path | None = typer.Option(
        None,
        "--nginx-http-config-file",
        help="LarOps-managed HTTP-context Nginx security config.",
        dir_okay=False,
    ),
    nginx_server_snippet_file: Path | None = typer.Option(
        None,
        "--nginx-server-snippet-file",
        help="LarOps-managed server-context Nginx security snippet.",
        dir_okay=False,
    ),
    nginx_server_config_file: Path | None = typer.Option(
        None,
        "--nginx-server-config-file",
        help="Optional vhost file used to verify snippet include injection.",
        dir_okay=False,
    ),
    nginx_root_config_file: Path | None = typer.Option(
        None,
        "--nginx-root-config-file",
        help="Optional root nginx.conf used to verify EL9 default.d auto-includes.",
        dir_okay=False,
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_fail2ban_jail_file = _resolve_fail2ban_jail_file(fail2ban_jail_file)
    resolved_fail2ban_filter_file = _resolve_fail2ban_filter_file(fail2ban_filter_file)
    try:
        report = collect_security_posture(
            state_path=Path(app_ctx.config.state_path),
            unit_dir=Path(app_ctx.config.systemd.unit_dir),
            systemd_manage=app_ctx.config.systemd.manage,
            fail2ban_jail_path=resolved_fail2ban_jail_file,
            fail2ban_filter_path=resolved_fail2ban_filter_file,
            sshd_drop_in_file=sshd_drop_in_file,
            nginx_http_config_file=nginx_http_config_file,
            nginx_server_snippet_file=nginx_server_snippet_file,
            nginx_server_config_file=nginx_server_config_file,
            nginx_root_config_file=nginx_root_config_file,
        )
    except SecurityServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(report["level"], "Security posture.", report=report)


@security_app.command("report")
def report(
    ctx: typer.Context,
    fail2ban_log_path: Path | None = typer.Option(
        None,
        "--fail2ban-log-path",
        help="Fail2ban log path.",
        dir_okay=False,
    ),
    nginx_log_path: Path | None = typer.Option(
        None,
        "--nginx-log-path",
        help="Nginx access log path.",
        dir_okay=False,
    ),
    top: int = typer.Option(10, "--top", help="Top N entries per section."),
    max_lines: int = typer.Option(5000, "--max-lines", help="Maximum number of lines scanned per log."),
    since: str | None = typer.Option(None, "--since", help="Relative time window (examples: 15m, 6h, 2d, 1w)."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_fail2ban_log_path = _resolve_fail2ban_log_path(fail2ban_log_path)
    resolved_nginx_log_path = _resolve_nginx_log_path(nginx_log_path)
    try:
        payload = build_security_report(
            fail2ban_log_path=resolved_fail2ban_log_path,
            nginx_log_path=resolved_nginx_log_path,
            max_lines=max_lines,
            top=top,
            since=since,
        )
    except SecurityReportError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    level = "ok"
    if payload["nginx_scan"]["suspicious_404_total"] > 0:
        level = "warn"
    app_ctx.emit_output(level, "Security report.", report=payload)
