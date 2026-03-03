from __future__ import annotations

import socket
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.security_service import (
    SecurityReportError,
    apply_security_install_plan,
    build_security_install_plan,
    build_security_report,
    collect_security_status,
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


@security_app.command("install")
def install(
    ctx: typer.Context,
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port to allow in UFW and Fail2ban jail."),
    limit_ssh: bool = typer.Option(True, "--limit-ssh/--no-limit-ssh", help="Enable UFW SSH rate limiting."),
    ufw_logging: str = typer.Option("low", "--ufw-logging", help="UFW logging level: off|on|low|medium|high|full."),
    fail2ban_jail_file: Path = typer.Option(
        Path("/etc/fail2ban/jail.d/larops.conf"),
        "--fail2ban-jail-file",
        help="Fail2ban jail file path.",
        dir_okay=False,
    ),
    fail2ban_filter_file: Path = typer.Option(
        Path("/etc/fail2ban/filter.d/larops-nginx-scan.conf"),
        "--fail2ban-filter-file",
        help="Fail2ban filter file path.",
        dir_okay=False,
    ),
    nginx_log_path: Path = typer.Option(
        Path("/var/log/nginx/access.log"),
        "--nginx-log-path",
        help="Nginx access log path for scan jail.",
        dir_okay=False,
    ),
    fail2ban_log_path: Path = typer.Option(
        Path("/var/log/fail2ban.log"),
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

    plan = build_security_install_plan(
        ssh_port=ssh_port,
        limit_ssh=limit_ssh,
        ufw_logging=ufw_logging,
        fail2ban_jail_path=fail2ban_jail_file,
        fail2ban_filter_path=fail2ban_filter_file,
        nginx_log_path=nginx_log_path,
        fail2ban_log_path=fail2ban_log_path,
    )
    app_ctx.emit_output(
        "ok",
        "Security install plan prepared.",
        ssh_port=ssh_port,
        limit_ssh=limit_ssh,
        ufw_commands=plan.ufw_commands,
        fail2ban_jail_file=str(plan.fail2ban_jail_path),
        fail2ban_filter_file=str(plan.fail2ban_filter_path),
        nginx_log_path=str(nginx_log_path),
        fail2ban_log_path=str(fail2ban_log_path),
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
    fail2ban_jail_file: Path = typer.Option(
        Path("/etc/fail2ban/jail.d/larops.conf"),
        "--fail2ban-jail-file",
        help="Fail2ban jail file path.",
        dir_okay=False,
    ),
    fail2ban_filter_file: Path = typer.Option(
        Path("/etc/fail2ban/filter.d/larops-nginx-scan.conf"),
        "--fail2ban-filter-file",
        help="Fail2ban filter file path.",
        dir_okay=False,
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    report = collect_security_status(
        fail2ban_jail_path=fail2ban_jail_file,
        fail2ban_filter_path=fail2ban_filter_file,
    )
    status_level = "ok"
    if report["ufw"]["exit_code"] != 0 or report["fail2ban"]["exit_code"] != 0:
        status_level = "warn"
    app_ctx.emit_output(status_level, "Security status.", report=report)


@security_app.command("report")
def report(
    ctx: typer.Context,
    fail2ban_log_path: Path = typer.Option(
        Path("/var/log/fail2ban.log"),
        "--fail2ban-log-path",
        help="Fail2ban log path.",
        dir_okay=False,
    ),
    nginx_log_path: Path = typer.Option(
        Path("/var/log/nginx/access.log"),
        "--nginx-log-path",
        help="Nginx access log path.",
        dir_okay=False,
    ),
    top: int = typer.Option(10, "--top", help="Top N entries per section."),
    max_lines: int = typer.Option(5000, "--max-lines", help="Maximum number of lines scanned per log."),
    since: str | None = typer.Option(None, "--since", help="Relative time window (examples: 15m, 6h, 2d, 1w)."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        payload = build_security_report(
            fail2ban_log_path=fail2ban_log_path,
            nginx_log_path=nginx_log_path,
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
