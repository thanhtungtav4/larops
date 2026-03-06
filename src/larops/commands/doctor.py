from __future__ import annotations

from pathlib import Path

import typer

from larops.runtime import AppContext
from larops.services.doctor_service import run_app_checks, run_host_checks, summarize

doctor_app = typer.Typer(help="Run health checks.")


@doctor_app.command("run")
def run(
    ctx: typer.Context,
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    app_ctx: AppContext = ctx.obj
    checks = run_host_checks(
        state_path=Path(app_ctx.config.state_path),
        events_path=Path(app_ctx.config.events.path),
        quick=False,
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    if target != "host":
        checks.extend(
            run_app_checks(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=target,
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                app_command_checks=list(app_ctx.config.doctor.app_command_checks),
            )
        )
    report = summarize(checks)
    app_ctx.emit_output(report["overall"], f"Doctor report for {target}", target=target, report=report)


@doctor_app.command("quick")
def quick(
    ctx: typer.Context,
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    app_ctx: AppContext = ctx.obj
    checks = run_host_checks(
        state_path=Path(app_ctx.config.state_path),
        events_path=Path(app_ctx.config.events.path),
        quick=True,
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    if target != "host":
        checks.extend(
            run_app_checks(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=target,
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                app_command_checks=list(app_ctx.config.doctor.app_command_checks),
            )
        )
    report = summarize(checks)
    app_ctx.emit_output(report["overall"], f"Doctor quick report for {target}", target=target, report=report)
