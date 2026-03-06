from __future__ import annotations

from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.core.locks import CommandLock, CommandLockError
from larops.runtime import AppContext
from larops.services.app_lifecycle import list_registered_apps
from larops.services.doctor_metrics_service import DoctorMetricsError, render_prometheus_metrics, write_metrics_file
from larops.services.doctor_service import run_app_checks, run_host_checks, summarize
from larops.services.doctor_systemd import (
    DoctorMetricsSystemdError,
    disable_doctor_metrics_timer,
    enable_doctor_metrics_timer,
    status_doctor_metrics_timer,
)

doctor_app = typer.Typer(help="Run health checks.")
metrics_app = typer.Typer(help="Export fleet health metrics.")
metrics_timer_app = typer.Typer(help="Manage doctor metrics export timer.")
doctor_app.add_typer(metrics_app, name="metrics")
metrics_app.add_typer(metrics_timer_app, name="timer")


def _runtime_policies(app_ctx: AppContext) -> dict[str, dict]:
    return {
        "worker": app_ctx.config.runtime_policy.worker.model_dump(),
        "scheduler": app_ctx.config.runtime_policy.scheduler.model_dump(),
        "horizon": app_ctx.config.runtime_policy.horizon.model_dump(),
    }


def _host_report(app_ctx: AppContext, *, quick: bool) -> dict:
    checks = run_host_checks(
        state_path=Path(app_ctx.config.state_path),
        events_path=Path(app_ctx.config.events.path),
        quick=quick,
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    return summarize(checks)


def _app_report(app_ctx: AppContext, *, domain: str) -> dict:
    checks = run_app_checks(
        base_releases_path=Path(app_ctx.config.deploy.releases_path),
        state_path=Path(app_ctx.config.state_path),
        domain=domain,
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
        app_command_checks=list(app_ctx.config.doctor.app_command_checks),
        heartbeat_checks=list(app_ctx.config.doctor.heartbeat_checks),
        queue_backlog_checks=list(app_ctx.config.doctor.queue_backlog_checks),
        failed_job_checks=list(app_ctx.config.doctor.failed_job_checks),
        runtime_policies=_runtime_policies(app_ctx),
        offsite_config=app_ctx.config.backups.offsite,
    )
    return summarize(checks)


def _status_rank(status: str) -> int:
    return {"ok": 0, "warn": 1, "error": 2}.get(status, 2)


def _compact_target_report(*, target: str, report: dict, include_checks: bool) -> dict:
    payload = {
        "target": target,
        "overall": report["overall"],
        "counts": report["counts"],
    }
    if include_checks:
        payload["checks"] = report["checks"]
    return payload


def _merge_reports(*reports: dict) -> dict:
    checks: list[dict] = []
    for report in reports:
        checks.extend(report["checks"])
    if any(check["status"] == "error" for check in checks):
        overall = "error"
    elif any(check["status"] == "warn" for check in checks):
        overall = "warn"
    else:
        overall = "ok"
    return {
        "overall": overall,
        "checks": checks,
        "counts": {
            "ok": len([check for check in checks if check["status"] == "ok"]),
            "warn": len([check for check in checks if check["status"] == "warn"]),
            "error": len([check for check in checks if check["status"] == "error"]),
        },
    }


def _resolve_cli_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


def _fleet_report(app_ctx: AppContext, *, quick: bool, include_host: bool) -> dict:
    targets: list[dict] = []

    if include_host:
        host_report = _host_report(app_ctx, quick=quick)
        targets.append(_compact_target_report(target="host", report=host_report, include_checks=True))

    domains = list_registered_apps(Path(app_ctx.config.state_path))
    for domain in domains:
        app_report = _app_report(app_ctx, domain=domain)
        targets.append(_compact_target_report(target=domain, report=app_report, include_checks=True))

    overall = "ok"
    for target_report in targets:
        if _status_rank(target_report["overall"]) > _status_rank(overall):
            overall = str(target_report["overall"])

    return {
        "overall": overall,
        "registered_apps": domains,
        "target_count": len(targets),
        "counts": {
            "ok": len([item for item in targets if item["overall"] == "ok"]),
            "warn": len([item for item in targets if item["overall"] == "warn"]),
            "error": len([item for item in targets if item["overall"] == "error"]),
        },
        "targets": targets,
    }


@doctor_app.command("run")
def run(
    ctx: typer.Context,
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    app_ctx: AppContext = ctx.obj
    host_report = _host_report(app_ctx, quick=False)
    report = host_report if target == "host" else _merge_reports(host_report, _app_report(app_ctx, domain=target))
    app_ctx.emit_output(report["overall"], f"Doctor report for {target}", target=target, report=report)


@doctor_app.command("quick")
def quick(
    ctx: typer.Context,
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    app_ctx: AppContext = ctx.obj
    host_report = _host_report(app_ctx, quick=True)
    report = host_report if target == "host" else _merge_reports(host_report, _app_report(app_ctx, domain=target))
    app_ctx.emit_output(report["overall"], f"Doctor quick report for {target}", target=target, report=report)


@doctor_app.command("fleet")
def fleet(
    ctx: typer.Context,
    quick: bool = typer.Option(False, "--quick", help="Run quick host checks instead of full host checks."),
    include_checks: bool = typer.Option(False, "--include-checks", help="Include per-target check details."),
    include_host: bool = typer.Option(True, "--include-host/--skip-host", help="Include host summary in fleet report."),
) -> None:
    app_ctx: AppContext = ctx.obj
    summary = _fleet_report(app_ctx, quick=quick, include_host=include_host)
    if not include_checks:
        summary["targets"] = [
            _compact_target_report(target=item["target"], report=item, include_checks=False)
            for item in summary["targets"]
        ]
    app_ctx.emit_output(summary["overall"], "Doctor fleet report", report=summary)


@metrics_app.command("run")
def metrics_run(
    ctx: typer.Context,
    quick: bool = typer.Option(False, "--quick", help="Run quick host checks instead of full host checks."),
    include_checks: bool = typer.Option(False, "--include-checks", help="Export per-check metrics as well."),
    include_host: bool = typer.Option(True, "--include-host/--skip-host", help="Include host summary in metrics export."),
    output_file: Path | None = typer.Option(
        None,
        "--output-file",
        help="Write metrics to a Prometheus textfile collector file.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Required when writing metrics to a file."),
) -> None:
    app_ctx: AppContext = ctx.obj
    summary = _fleet_report(app_ctx, quick=quick, include_host=include_host)
    metrics_text = render_prometheus_metrics(summary, include_checks=include_checks)

    if output_file is None:
        if app_ctx.json_output:
            app_ctx.emit_output(
                summary["overall"],
                "Doctor metrics generated.",
                report=summary,
                metrics=metrics_text,
            )
        else:
            print(metrics_text)
        return

    app_ctx.emit_output(
        "ok",
        "Doctor metrics export plan prepared.",
        output_file=str(output_file),
        quick=quick,
        include_checks=include_checks,
        include_host=include_host,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("doctor-metrics-export"):
            write_metrics_file(output_file=output_file, metrics_text=metrics_text)
    except (CommandLockError, DoctorMetricsError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc

    app_ctx.emit_output(
        summary["overall"],
        "Doctor metrics exported.",
        output_file=str(output_file),
        report=summary,
    )


@metrics_timer_app.command("enable")
def metrics_timer_enable(
    ctx: typer.Context,
    output_file: Path = typer.Option(
        Path("/var/lib/node_exporter/textfile_collector/larops.prom"),
        "--output-file",
        help="Prometheus textfile collector output path.",
        dir_okay=False,
    ),
    on_calendar: str = typer.Option("*-*-* *:*:00", "--on-calendar", help="systemd OnCalendar schedule."),
    randomized_delay: int = typer.Option(10, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by metrics export service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    quick: bool = typer.Option(False, "--quick", help="Use quick host checks."),
    include_checks: bool = typer.Option(False, "--include-checks", help="Export per-check metrics."),
    include_host: bool = typer.Option(True, "--include-host/--skip-host", help="Include host summary."),
    apply: bool = typer.Option(False, "--apply", help="Apply metrics timer setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Doctor metrics timer enable plan prepared.",
        output_file=str(output_file),
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        quick=quick,
        include_checks=include_checks,
        include_host=include_host,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("doctor-metrics-timer-enable"):
            result = enable_doctor_metrics_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                output_file=output_file,
                quick=quick,
                include_checks=include_checks,
                include_host=include_host,
            )
    except (CommandLockError, DoctorMetricsSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Doctor metrics timer enabled.", metrics_timer=result)


@metrics_timer_app.command("disable")
def metrics_timer_disable(
    ctx: typer.Context,
    remove_units: bool = typer.Option(False, "--remove-units", help="Remove timer/service unit files after disable."),
    apply: bool = typer.Option(False, "--apply", help="Apply metrics timer disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Doctor metrics timer disable plan prepared.",
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("doctor-metrics-timer-disable"):
            result = disable_doctor_metrics_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_units=remove_units,
            )
    except (CommandLockError, DoctorMetricsSystemdError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", "Doctor metrics timer disabled.", metrics_timer=result)


@metrics_timer_app.command("status")
def metrics_timer_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_doctor_metrics_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "Doctor metrics timer status.", metrics_timer=result)
