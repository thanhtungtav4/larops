from __future__ import annotations

import re
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.runtime import AppContext
from larops.services.runtime_process import (
    RuntimeProcessError,
    disable_process,
    enable_process,
    status_process,
    terminate_process,
)

horizon_app = typer.Typer(help="Manage Laravel Horizon process.")


def _lock_name(domain: str) -> str:
    return f"horizon-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _policy_for(app_ctx: AppContext) -> dict:
    return app_ctx.config.runtime_policy.horizon.model_dump()


@horizon_app.command("enable")
def enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Horizon enable plan prepared for {domain}",
        domain=domain,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        with CommandLock(_lock_name(domain)):
            spec = enable_process(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                service_user=app_ctx.config.systemd.user,
                domain=domain,
                process_type="horizon",
                options={},
                policy=_policy_for(app_ctx),
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Horizon enabled for {domain}", spec=spec)


@horizon_app.command("disable")
def disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Horizon disable plan prepared for {domain}",
        domain=domain,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        with CommandLock(_lock_name(domain)):
            spec = disable_process(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                process_type="horizon",
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Horizon disabled for {domain}", spec=spec)


@horizon_app.command("terminate")
def terminate(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply terminate operation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Horizon terminate plan prepared for {domain}",
        domain=domain,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        with CommandLock(_lock_name(domain)):
            spec = terminate_process(
                state_path=Path(app_ctx.config.state_path),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                process_type="horizon",
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Horizon terminated for {domain}", spec=spec)


@horizon_app.command("status")
def status(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
) -> None:
    app_ctx: AppContext = ctx.obj
    spec = status_process(
        state_path=Path(app_ctx.config.state_path),
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
        domain=domain,
        process_type="horizon",
        policy=_policy_for(app_ctx),
    )
    app_ctx.emit_output("ok", f"Horizon status for {domain}", process=spec)
