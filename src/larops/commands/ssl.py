from __future__ import annotations

import socket
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.app_lifecycle import AppLifecycleError, get_app_paths, load_metadata
from larops.services.nginx_site_service import NginxSiteServiceError, apply_nginx_site_config
from larops.services.ssl_auto_renew import (
    SslAutoRenewError,
    disable_ssl_auto_renew,
    enable_ssl_auto_renew,
    status_ssl_auto_renew,
)
from larops.services.ssl_service import (
    SslServiceError,
    build_issue_command,
    build_renew_command,
    default_cert_file,
    read_certificate_info,
    run_issue,
    run_renew,
)

ssl_app = typer.Typer(help="Manage SSL certificate lifecycle.")
auto_renew_app = typer.Typer(help="Manage SSL auto-renew timer.")
ssl_app.add_typer(auto_renew_app, name="auto-renew")


def _resolve_managed_site_context(app_ctx: AppContext, domain: str) -> dict | None:
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        metadata = load_metadata(paths.metadata)
    except AppLifecycleError:
        return None

    if not paths.current.exists():
        return None

    current_path = paths.current.resolve(strict=False)
    public_path = current_path / "public"
    webroot = public_path if public_path.exists() else current_path
    php_version = str(metadata.get("php") or app_ctx.config.php_version or "8.3")
    return {
        "paths": paths,
        "current_path": current_path,
        "webroot": webroot,
        "php_version": php_version,
    }


def _emit(app_ctx: AppContext, severity: str, event_type: str, message: str, metadata: dict | None = None) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            message=message,
            metadata=metadata or {},
        )
    )


@auto_renew_app.command("enable")
def auto_renew_enable(
    ctx: typer.Context,
    on_calendar: str = typer.Option(
        "*-*-* 03,15:00:00",
        "--on-calendar",
        help="systemd OnCalendar expression for renew schedule.",
    ),
    randomized_delay: int = typer.Option(
        1800,
        "--randomized-delay",
        help="RandomizedDelaySec value in seconds.",
    ),
    user: str = typer.Option("root", "--user", help="System user used by renew service."),
    reload_command: str | None = typer.Option(
        "systemctl reload nginx",
        "--reload-command",
        help="Optional certbot deploy-hook command after successful renewal.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply auto-renew setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    renew_command = build_renew_command(force=False, dry_run=False)
    if reload_command:
        renew_command.extend(["--deploy-hook", reload_command])

    app_ctx.emit_output(
        "ok",
        "SSL auto-renew enable plan prepared.",
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        reload_command=reload_command,
        renew_command=renew_command,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("ssl-auto-renew-enable"):
            result = enable_ssl_auto_renew(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                renew_command=renew_command,
            )
    except (CommandLockError, SslAutoRenewError, ShellCommandError) as exc:
        _emit(app_ctx, "error", "ssl.auto_renew.enable.failed", "SSL auto-renew enable failed.", {"error": str(exc)})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "ssl.auto_renew.enable.completed",
        "SSL auto-renew enable completed.",
        {"on_calendar": on_calendar, "randomized_delay": randomized_delay},
    )
    app_ctx.emit_output("ok", "SSL auto-renew enabled.", auto_renew=result)


@auto_renew_app.command("disable")
def auto_renew_disable(
    ctx: typer.Context,
    remove_units: bool = typer.Option(
        False,
        "--remove-units",
        help="Remove timer/service unit files after disable.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply auto-renew disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "SSL auto-renew disable plan prepared.",
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("ssl-auto-renew-disable"):
            result = disable_ssl_auto_renew(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_units=remove_units,
            )
    except (CommandLockError, SslAutoRenewError, ShellCommandError) as exc:
        _emit(app_ctx, "error", "ssl.auto_renew.disable.failed", "SSL auto-renew disable failed.", {"error": str(exc)})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "ssl.auto_renew.disable.completed",
        "SSL auto-renew disable completed.",
        {"remove_units": remove_units},
    )
    app_ctx.emit_output("ok", "SSL auto-renew disabled.", auto_renew=result)


@auto_renew_app.command("status")
def auto_renew_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_ssl_auto_renew(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "SSL auto-renew status.", auto_renew=result)


@ssl_app.command("issue")
def issue(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain for certificate issuance."),
    email: str | None = typer.Option(None, "--email", help="Email used for certificate registration."),
    challenge: str = typer.Option("http", "--challenge", help="Challenge type: http or dns."),
    dns_provider: str | None = typer.Option(None, "--dns-provider", help="DNS provider short name for certbot."),
    webroot_path: str | None = typer.Option(None, "--webroot-path", help="HTTP challenge webroot path."),
    staging: bool = typer.Option(False, "--staging", help="Use Let's Encrypt staging."),
    apply: bool = typer.Option(False, "--apply", help="Apply certificate issuance."),
) -> None:
    app_ctx: AppContext = ctx.obj
    managed_site = _resolve_managed_site_context(app_ctx, domain)
    resolved_webroot = webroot_path
    if resolved_webroot is None and challenge == "http" and managed_site is not None:
        resolved_webroot = str(managed_site["webroot"])

    try:
        command = build_issue_command(
            domain=domain,
            email=email,
            challenge=challenge,
            dns_provider=dns_provider,
            staging=staging,
            webroot_path=resolved_webroot,
        )
    except SslServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"SSL issue plan prepared for {domain}",
        domain=domain,
        command=command,
        webroot_path=resolved_webroot,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("ssl-issue"):
            output = run_issue(command)
            nginx_result = None
            if managed_site is not None:
                nginx_result = apply_nginx_site_config(
                    domain=domain,
                    current_path=managed_site["current_path"],
                    php_version=managed_site["php_version"],
                    https_enabled=True,
                    force=True,
                )
    except (CommandLockError, ShellCommandError, SslServiceError, NginxSiteServiceError) as exc:
        _emit(app_ctx, "error", "ssl.issue.failed", "SSL issue failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "ssl.issue.completed", "SSL issue completed.", {"domain": domain})
    app_ctx.emit_output("ok", f"SSL issue completed for {domain}", output=output, nginx=nginx_result)


@ssl_app.command("renew")
def renew(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Force certificate renewal."),
    dry_run_renew: bool = typer.Option(False, "--dry-run-renew", help="Pass certbot dry-run flag."),
    apply: bool = typer.Option(False, "--apply", help="Apply renewal."),
) -> None:
    app_ctx: AppContext = ctx.obj
    command = build_renew_command(force=force, dry_run=dry_run_renew)
    app_ctx.emit_output(
        "ok",
        "SSL renew plan prepared.",
        command=command,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("ssl-renew"):
            output = run_renew(command)
    except (CommandLockError, ShellCommandError, SslServiceError) as exc:
        _emit(app_ctx, "error", "ssl.renew.failed", "SSL renew failed.", {"error": str(exc)})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "ssl.renew.completed", "SSL renew completed.")
    app_ctx.emit_output("ok", "SSL renew completed.", output=output)


@ssl_app.command("check")
def check(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain to check."),
    cert_file: Path | None = typer.Option(
        None,
        "--cert-file",
        help="Optional cert file override for checks.",
        exists=False,
        dir_okay=False,
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    file_path = cert_file or default_cert_file(domain)

    try:
        info = read_certificate_info(file_path)
    except (SslServiceError, ShellCommandError) as exc:
        app_ctx.emit_output("error", str(exc), domain=domain, cert_file=str(file_path))
        raise typer.Exit(code=2) from exc

    status = "ok" if info.days_remaining >= 15 else "warn"
    app_ctx.emit_output(
        status,
        f"SSL certificate status for {domain}",
        domain=domain,
        cert_file=str(info.cert_file),
        subject=info.subject,
        issuer=info.issuer,
        not_after=info.not_after.isoformat(),
        days_remaining=info.days_remaining,
    )
