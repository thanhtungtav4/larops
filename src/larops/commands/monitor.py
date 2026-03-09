from __future__ import annotations

import socket
from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.core.locks import CommandLock, CommandLockError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.host_layout_service import default_nginx_access_log_path
from larops.services.monitor_app_service import MonitorAppError, run_app_monitor
from larops.services.monitor_fim_service import (
    DEFAULT_FIM_PATTERNS,
    MonitorFimError,
    init_fim_baseline,
    run_fim_check,
)
from larops.services.monitor_service_watch import (
    MonitorServiceWatchError,
    is_service_healthy,
    resolve_service_targets,
    watch_services,
)
from larops.services.monitor_scan_service import MonitorScanError, scan_nginx_incremental
from larops.services.monitor_systemd import (
    MonitorSystemdError,
    disable_monitor_fim_timer,
    disable_monitor_app_timer,
    disable_monitor_scan_timer,
    disable_monitor_service_timer,
    enable_monitor_app_timer,
    enable_monitor_fim_timer,
    enable_monitor_scan_timer,
    enable_monitor_service_timer,
    status_monitor_app_timer,
    status_monitor_fim_timer,
    status_monitor_scan_timer,
    status_monitor_service_timer,
)

monitor_app = typer.Typer(help="Security monitor tools (scan, file integrity, service watchdog).")
scan_app = typer.Typer(help="Incremental Nginx scan monitor.")
fim_app = typer.Typer(help="File integrity monitor.")
service_app = typer.Typer(help="Critical system service watchdog.")
app_watch_app = typer.Typer(help="Application runtime and heartbeat monitor.")
scan_timer_app = typer.Typer(help="Manage monitor scan systemd timer.")
fim_timer_app = typer.Typer(help="Manage monitor FIM systemd timer.")
service_timer_app = typer.Typer(help="Manage service watchdog systemd timer.")
app_timer_app = typer.Typer(help="Manage application monitor systemd timer.")
monitor_app.add_typer(scan_app, name="scan")
monitor_app.add_typer(fim_app, name="fim")
monitor_app.add_typer(service_app, name="service")
monitor_app.add_typer(app_watch_app, name="app")
scan_app.add_typer(scan_timer_app, name="timer")
fim_app.add_typer(fim_timer_app, name="timer")
service_app.add_typer(service_timer_app, name="timer")
app_watch_app.add_typer(app_timer_app, name="timer")


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


def _default_scan_state_file(app_ctx: AppContext) -> Path:
    return Path(app_ctx.config.state_path) / "security" / "scan_state.json"


def _default_fim_baseline_file(app_ctx: AppContext) -> Path:
    return Path(app_ctx.config.state_path) / "security" / "fim_baseline.json"


def _default_service_state_file(app_ctx: AppContext) -> Path:
    return Path(app_ctx.config.state_path) / "security" / "service_watch_state.json"


def _default_app_state_file(app_ctx: AppContext, domain: str) -> Path:
    return Path(app_ctx.config.state_path) / "monitor" / "app" / f"{domain}.json"


def _resolve_cli_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


@scan_app.command("run")
def scan_run(
    ctx: typer.Context,
    nginx_log_path: Path | None = typer.Option(
        None,
        "--nginx-log-path",
        help="Nginx access log path.",
        dir_okay=False,
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Scanner offset state file path.",
        dir_okay=False,
    ),
    threshold_hits: int = typer.Option(8, "--threshold-hits", help="Alert threshold by IP inside the scan window."),
    window_seconds: int = typer.Option(300, "--window-seconds", help="Rolling time window for suspicious hits."),
    max_lines: int = typer.Option(5000, "--max-lines", help="Maximum lines read per run."),
    top: int = typer.Option(10, "--top", help="Top N paths/IPs in output."),
    apply: bool = typer.Option(False, "--apply", help="Execute scan and emit events."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_nginx_log_path = nginx_log_path or default_nginx_access_log_path()
    resolved_state_file = state_file or _default_scan_state_file(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Monitor scan plan prepared.",
        nginx_log_path=str(resolved_nginx_log_path),
        state_file=str(resolved_state_file),
        threshold_hits=threshold_hits,
        window_seconds=window_seconds,
        max_lines=max_lines,
        top=top,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-scan-run"):
            result = scan_nginx_incremental(
                log_path=resolved_nginx_log_path,
                state_path=resolved_state_file,
                threshold_hits=threshold_hits,
                window_seconds=window_seconds,
                max_lines=max_lines,
                top=top,
            )
    except (CommandLockError, MonitorScanError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="monitor.scan.failed",
            message="Monitor scan failed.",
            metadata={"error": str(exc), "log_path": str(resolved_nginx_log_path)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    for alert in result["alerts"]:
        _emit(
            app_ctx,
            severity="warn",
            event_type="monitor.scan.threshold_exceeded",
            message="Suspicious scan threshold exceeded.",
            metadata={
                "log_path": str(resolved_nginx_log_path),
                "ip": alert["ip"],
                "hits": alert["hits"],
                "threshold": alert["threshold"],
                "window_seconds": alert["window_seconds"],
            },
        )
    _emit(
        app_ctx,
        severity="info",
        event_type="monitor.scan.completed",
        message="Monitor scan completed.",
        metadata={
            "log_path": str(resolved_nginx_log_path),
            "suspicious_total": result["suspicious_total"],
            "alerts": len(result["alerts"]),
            "window_seconds": result["window_seconds"],
        },
    )
    status = "warn" if result["alerts"] else "ok"
    app_ctx.emit_output(status, "Monitor scan completed.", result=result)


@scan_timer_app.command("enable")
def scan_timer_enable(
    ctx: typer.Context,
    on_calendar: str = typer.Option(
        "*-*-* *:*:00",
        "--on-calendar",
        help="systemd OnCalendar expression for monitor scan schedule.",
    ),
    randomized_delay: int = typer.Option(15, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by monitor scan service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    nginx_log_path: Path | None = typer.Option(
        None,
        "--nginx-log-path",
        help="Nginx access log path.",
        dir_okay=False,
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Scanner offset state file path.",
        dir_okay=False,
    ),
    threshold_hits: int = typer.Option(8, "--threshold-hits", help="Alert threshold by IP inside the scan window."),
    window_seconds: int = typer.Option(300, "--window-seconds", help="Rolling time window for suspicious hits."),
    max_lines: int = typer.Option(5000, "--max-lines", help="Maximum lines read per run."),
    top: int = typer.Option(10, "--top", help="Top N paths/IPs in output."),
    apply: bool = typer.Option(False, "--apply", help="Apply timer setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    resolved_nginx_log_path = nginx_log_path or default_nginx_access_log_path()
    resolved_state = state_file or _default_scan_state_file(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Monitor scan timer enable plan prepared.",
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        nginx_log_path=str(resolved_nginx_log_path),
        state_file=str(resolved_state),
        threshold_hits=threshold_hits,
        window_seconds=window_seconds,
        max_lines=max_lines,
        top=top,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-scan-timer-enable"):
            result = enable_monitor_scan_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                nginx_log_path=resolved_nginx_log_path,
                state_file=resolved_state,
                threshold_hits=threshold_hits,
                window_seconds=window_seconds,
                max_lines=max_lines,
                top=top,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor scan timer enabled.", timer=result)


@scan_timer_app.command("disable")
def scan_timer_disable(
    ctx: typer.Context,
    remove_units: bool = typer.Option(
        False,
        "--remove-units",
        help="Remove timer/service unit files after disable.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply timer disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Monitor scan timer disable plan prepared.",
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-scan-timer-disable"):
            result = disable_monitor_scan_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_units=remove_units,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor scan timer disabled.", timer=result)


@scan_timer_app.command("status")
def scan_timer_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_monitor_scan_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "Monitor scan timer status.", timer=result)


@service_app.command("run")
def service_run(
    ctx: typer.Context,
    service: list[str] = typer.Option([], "--service", help="Critical system service to watch. Repeatable."),
    profile: list[str] = typer.Option(
        [],
        "--profile",
        help="Service watch profile to expand (repeatable). Example: laravel-host.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Service watchdog state file path.",
        dir_okay=False,
    ),
    restart_down_services: bool = typer.Option(
        True,
        "--restart-down-services/--no-restart-down-services",
        help="Attempt systemctl restart when service is not active.",
    ),
    restart_cooldown: int = typer.Option(
        300,
        "--restart-cooldown",
        help="Minimum seconds between restart attempts for the same service.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute service watchdog."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_state_file = state_file or _default_service_state_file(app_ctx)
    try:
        resolved_services = resolve_service_targets(services=service, profiles=profile)
    except MonitorServiceWatchError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(
        "ok",
        "Monitor service plan prepared.",
        services=service,
        profiles=profile,
        resolved_services=resolved_services,
        state_file=str(resolved_state_file),
        restart_down_services=restart_down_services,
        restart_cooldown=restart_cooldown,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-service-run"):
            result = watch_services(
                services=service,
                profiles=profile,
                state_file=resolved_state_file,
                restart_down_services=restart_down_services,
                restart_cooldown_seconds=restart_cooldown,
            )
    except (CommandLockError, MonitorServiceWatchError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="monitor.service.failed",
            message="Service watchdog failed.",
            metadata={"error": str(exc), "state_file": str(resolved_state_file)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    status = "ok"
    for item in result["services"]:
        transition = item["transition"]
        metadata = {
            "service": item["service"],
            "before": item["before"],
            "after": item["after"],
            "action": item["action"],
        }
        if transition == "restarted":
            status = "warn" if status == "ok" else status
            _emit(
                app_ctx,
                severity="warn",
                event_type="monitor.service.restarted",
                message=f"Service {item['service']} was down and restarted successfully.",
                metadata=metadata,
            )
        elif transition == "restart_failed":
            status = "error"
            _emit(
                app_ctx,
                severity="critical",
                event_type="monitor.service.restart_failed",
                message=f"Service {item['service']} is down and restart failed.",
                metadata=metadata,
            )
        elif transition == "down":
            status = "error"
            _emit(
                app_ctx,
                severity="error",
                event_type="monitor.service.down",
                message=f"Service {item['service']} is down.",
                metadata=metadata,
            )
        elif transition == "recovered":
            _emit(
                app_ctx,
                severity="info",
                event_type="monitor.service.recovered",
                message=f"Service {item['service']} is active again.",
                metadata=metadata,
            )
        if not is_service_healthy(str(item["after"]["active"])):
            status = "error"

    app_ctx.emit_output(status, "Monitor service completed.", result=result)


@service_timer_app.command("enable")
def service_timer_enable(
    ctx: typer.Context,
    service: list[str] = typer.Option([], "--service", help="Critical system service to watch. Repeatable."),
    profile: list[str] = typer.Option(
        [],
        "--profile",
        help="Service watch profile to expand (repeatable). Example: laravel-host.",
    ),
    on_calendar: str = typer.Option(
        "*-*-* *:*:00",
        "--on-calendar",
        help="systemd OnCalendar expression for service watchdog schedule.",
    ),
    randomized_delay: int = typer.Option(10, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by watchdog service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Service watchdog state file path.",
        dir_okay=False,
    ),
    restart_down_services: bool = typer.Option(
        True,
        "--restart-down-services/--no-restart-down-services",
        help="Attempt systemctl restart when service is not active.",
    ),
    restart_cooldown: int = typer.Option(
        300,
        "--restart-cooldown",
        help="Minimum seconds between restart attempts for the same service.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply watchdog timer setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    resolved_state = state_file or _default_service_state_file(app_ctx)
    try:
        resolved_services = resolve_service_targets(services=service, profiles=profile)
    except MonitorServiceWatchError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(
        "ok",
        "Monitor service timer enable plan prepared.",
        services=service,
        profiles=profile,
        resolved_services=resolved_services,
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        state_file=str(resolved_state),
        restart_down_services=restart_down_services,
        restart_cooldown=restart_cooldown,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-service-timer-enable"):
            result = enable_monitor_service_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                state_file=resolved_state,
                services=service,
                profiles=profile,
                restart_down_services=restart_down_services,
                restart_cooldown_seconds=restart_cooldown,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor service timer enabled.", timer=result)


@service_timer_app.command("disable")
def service_timer_disable(
    ctx: typer.Context,
    remove_units: bool = typer.Option(False, "--remove-units", help="Remove timer/service unit files after disable."),
    apply: bool = typer.Option(False, "--apply", help="Apply watchdog timer disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Monitor service timer disable plan prepared.",
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-service-timer-disable"):
            result = disable_monitor_service_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_units=remove_units,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor service timer disabled.", timer=result)


@service_timer_app.command("status")
def service_timer_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_monitor_service_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "Monitor service timer status.", timer=result)


@app_watch_app.command("run")
def app_run(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Application monitor state file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute application monitor."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_state_file = state_file or _default_app_state_file(app_ctx, domain)
    app_ctx.emit_output(
        "ok",
        "Monitor app plan prepared.",
        domain=domain,
        state_file=str(resolved_state_file),
        app_command_checks=[item.model_dump() for item in app_ctx.config.doctor.app_command_checks],
        heartbeat_checks=[item.model_dump() for item in app_ctx.config.doctor.heartbeat_checks],
        queue_backlog_checks=[item.model_dump() for item in app_ctx.config.doctor.queue_backlog_checks],
        failed_job_checks=[item.model_dump() for item in app_ctx.config.doctor.failed_job_checks],
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"monitor-app-run-{domain}"):
            result = run_app_monitor(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                app_command_checks=list(app_ctx.config.doctor.app_command_checks),
                heartbeat_checks=list(app_ctx.config.doctor.heartbeat_checks),
                queue_backlog_checks=list(app_ctx.config.doctor.queue_backlog_checks),
                failed_job_checks=list(app_ctx.config.doctor.failed_job_checks),
                runtime_policies={
                    "worker": app_ctx.config.runtime_policy.worker.model_dump(),
                    "scheduler": app_ctx.config.runtime_policy.scheduler.model_dump(),
                    "horizon": app_ctx.config.runtime_policy.horizon.model_dump(),
                },
                offsite_config=app_ctx.config.backups.offsite,
                monitor_state_file=resolved_state_file,
            )
    except (CommandLockError, MonitorAppError, RuntimeError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="monitor.app.failed",
            message=f"Application monitor failed for {domain}.",
            metadata={"domain": domain, "error": str(exc), "state_file": str(resolved_state_file)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    for transition in result["transitions"]:
        metadata = {
            "domain": domain,
            "check_name": transition["name"],
            "detail": transition["detail"],
            "status": transition["status"],
            "previous_status": transition["previous_status"],
        }
        if transition["transition"] == "alert":
            severity = "error" if transition["status"] == "error" else "warn"
            _emit(
                app_ctx,
                severity=severity,
                event_type="monitor.app.alert",
                message=f"Application monitor alert for {domain}: {transition['name']}",
                metadata=metadata,
            )
        elif transition["transition"] == "recovered":
            _emit(
                app_ctx,
                severity="info",
                event_type="monitor.app.recovered",
                message=f"Application monitor recovered for {domain}: {transition['name']}",
                metadata=metadata,
            )

    app_ctx.emit_output(result["report"]["overall"], f"Monitor app completed for {domain}.", result=result)


@app_timer_app.command("enable")
def app_timer_enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    on_calendar: str = typer.Option(
        "*-*-* *:*:00",
        "--on-calendar",
        help="systemd OnCalendar expression for application monitor schedule.",
    ),
    randomized_delay: int = typer.Option(10, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by application monitor service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Application monitor state file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply application monitor timer setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    resolved_state = state_file or _default_app_state_file(app_ctx, domain)
    app_ctx.emit_output(
        "ok",
        "Monitor app timer enable plan prepared.",
        domain=domain,
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        state_file=str(resolved_state),
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"monitor-app-timer-enable-{domain}"):
            result = enable_monitor_app_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                domain=domain,
                state_file=resolved_state,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Monitor app timer enabled for {domain}.", timer=result)


@app_timer_app.command("disable")
def app_timer_disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    remove_units: bool = typer.Option(False, "--remove-units", help="Remove timer/service unit files after disable."),
    apply: bool = typer.Option(False, "--apply", help="Apply application monitor timer disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Monitor app timer disable plan prepared.",
        domain=domain,
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"monitor-app-timer-disable-{domain}"):
            result = disable_monitor_app_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                remove_units=remove_units,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Monitor app timer disabled for {domain}.", timer=result)


@app_timer_app.command("status")
def app_timer_status(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_monitor_app_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
        domain=domain,
    )
    app_ctx.emit_output("ok", f"Monitor app timer status for {domain}.", timer=result)


@fim_app.command("init")
def fim_init(
    ctx: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help="Root directory to monitor.", file_okay=False),
    baseline_file: Path | None = typer.Option(
        None,
        "--baseline-file",
        help="FIM baseline file path.",
        dir_okay=False,
    ),
    pattern: list[str] = typer.Option([], "--pattern", help="Watch pattern (repeatable)."),
    algorithm: str = typer.Option("sha256", "--algorithm", help="Hash algorithm."),
    apply: bool = typer.Option(False, "--apply", help="Create baseline."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_baseline = baseline_file or _default_fim_baseline_file(app_ctx)
    patterns = pattern or list(DEFAULT_FIM_PATTERNS)
    app_ctx.emit_output(
        "ok",
        "Monitor fim init plan prepared.",
        root=str(root),
        baseline_file=str(resolved_baseline),
        patterns=patterns,
        algorithm=algorithm,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-fim-init"):
            result = init_fim_baseline(
                root=root,
                baseline_path=resolved_baseline,
                patterns=patterns,
                algorithm=algorithm,
            )
    except (CommandLockError, MonitorFimError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="monitor.fim.init.failed",
            message="FIM baseline initialization failed.",
            metadata={"error": str(exc), "baseline": str(resolved_baseline)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="monitor.fim.init.completed",
        message="FIM baseline initialized.",
        metadata={"baseline": str(resolved_baseline), "file_count": result["file_count"]},
    )
    app_ctx.emit_output("ok", "Monitor fim baseline initialized.", result=result)


@fim_app.command("run")
def fim_run(
    ctx: typer.Context,
    baseline_file: Path | None = typer.Option(
        None,
        "--baseline-file",
        help="FIM baseline file path.",
        dir_okay=False,
    ),
    root: Path | None = typer.Option(None, "--root", help="Root directory override.", file_okay=False),
    update_baseline: bool = typer.Option(
        False,
        "--update-baseline",
        help="Update baseline after this run.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute FIM check."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_baseline = baseline_file or _default_fim_baseline_file(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Monitor fim run plan prepared.",
        baseline_file=str(resolved_baseline),
        root=str(root) if root is not None else None,
        update_baseline=update_baseline,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-fim-run"):
            result = run_fim_check(
                baseline_path=resolved_baseline,
                root=root,
                update_baseline=update_baseline,
            )
    except (CommandLockError, MonitorFimError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="monitor.fim.run.failed",
            message="FIM check failed.",
            metadata={"error": str(exc), "baseline": str(resolved_baseline)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    if result["has_changes"]:
        _emit(
            app_ctx,
            severity="warn",
            event_type="monitor.fim.changes_detected",
            message="FIM detected file changes.",
            metadata={
                "baseline": str(resolved_baseline),
                "created": result["counts"]["created"],
                "deleted": result["counts"]["deleted"],
                "changed": result["counts"]["changed"],
            },
        )
        for path in result["created"]:
            _emit(
                app_ctx,
                severity="warn",
                event_type="monitor.fim.created",
                message="FIM created file detected.",
                metadata={"path": path},
            )
        for path in result["deleted"]:
            _emit(
                app_ctx,
                severity="warn",
                event_type="monitor.fim.deleted",
                message="FIM deleted file detected.",
                metadata={"path": path},
            )
        for item in result["changed"]:
            _emit(
                app_ctx,
                severity="warn",
                event_type="monitor.fim.changed",
                message="FIM changed file detected.",
                metadata=item,
            )
    else:
        _emit(
            app_ctx,
            severity="info",
            event_type="monitor.fim.clean",
            message="FIM check clean.",
            metadata={"baseline": str(resolved_baseline)},
        )

    status = "warn" if result["has_changes"] else "ok"
    app_ctx.emit_output(status, "Monitor fim run completed.", result=result)


@fim_timer_app.command("enable")
def fim_timer_enable(
    ctx: typer.Context,
    on_calendar: str = typer.Option(
        "*-*-* *:15:00",
        "--on-calendar",
        help="systemd OnCalendar expression for monitor FIM schedule.",
    ),
    randomized_delay: int = typer.Option(120, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by monitor FIM service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    baseline_file: Path | None = typer.Option(
        None,
        "--baseline-file",
        help="FIM baseline file path.",
        dir_okay=False,
    ),
    root: Path | None = typer.Option(None, "--root", help="Root directory override.", file_okay=False),
    update_baseline: bool = typer.Option(
        False,
        "--update-baseline",
        help="Update baseline after each scheduled run.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply timer setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    resolved_baseline = baseline_file or _default_fim_baseline_file(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Monitor fim timer enable plan prepared.",
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        baseline_file=str(resolved_baseline),
        root=str(root) if root is not None else None,
        update_baseline=update_baseline,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-fim-timer-enable"):
            result = enable_monitor_fim_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                baseline_file=resolved_baseline,
                root=root,
                update_baseline=update_baseline,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor fim timer enabled.", timer=result)


@fim_timer_app.command("disable")
def fim_timer_disable(
    ctx: typer.Context,
    remove_units: bool = typer.Option(
        False,
        "--remove-units",
        help="Remove timer/service unit files after disable.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply timer disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Monitor fim timer disable plan prepared.",
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("monitor-fim-timer-disable"):
            result = disable_monitor_fim_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_units=remove_units,
            )
    except (CommandLockError, MonitorSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Monitor fim timer disabled.", timer=result)


@fim_timer_app.command("status")
def fim_timer_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_monitor_fim_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "Monitor fim timer status.", timer=result)
