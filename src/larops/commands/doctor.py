from __future__ import annotations

from pathlib import Path

import typer

from larops.runtime import AppContext
from larops.services.app_lifecycle import list_registered_apps
from larops.services.doctor_service import run_app_checks, run_host_checks, summarize

doctor_app = typer.Typer(help="Run health checks.")


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
    targets: list[dict] = []

    if include_host:
        host_report = _host_report(app_ctx, quick=quick)
        targets.append(_compact_target_report(target="host", report=host_report, include_checks=include_checks))

    domains = list_registered_apps(Path(app_ctx.config.state_path))
    for domain in domains:
        app_report = _app_report(app_ctx, domain=domain)
        targets.append(_compact_target_report(target=domain, report=app_report, include_checks=include_checks))

    overall = "ok"
    for target_report in targets:
        if _status_rank(target_report["overall"]) > _status_rank(overall):
            overall = str(target_report["overall"])

    summary = {
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
    app_ctx.emit_output(overall, "Doctor fleet report", report=summary)
