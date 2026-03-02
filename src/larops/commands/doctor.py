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
    )
    if target != "host":
        checks.extend(
            run_app_checks(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=target,
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
    )
    if target != "host":
        checks.extend(
            run_app_checks(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=target,
            )
        )
    report = summarize(checks)
    app_ctx.emit_output(report["overall"], f"Doctor quick report for {target}", target=target, report=report)

