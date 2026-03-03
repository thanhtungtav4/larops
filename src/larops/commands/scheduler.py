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
    run_scheduler_once,
    status_process,
)

scheduler_app = typer.Typer(help="Manage Laravel scheduler process.")


def _lock_name(domain: str) -> str:
    return f"scheduler-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _policy_for(app_ctx: AppContext) -> dict:
    return app_ctx.config.runtime_policy.scheduler.model_dump()


@scheduler_app.command("enable")
def enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    command: str = typer.Option("php artisan schedule:run", "--command", help="Scheduler command."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    options = {"command": command}
    app_ctx.emit_output(
        "ok",
        f"Scheduler enable plan prepared for {domain}",
        domain=domain,
        options=options,
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
                process_type="scheduler",
                options=options,
                policy=_policy_for(app_ctx),
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Scheduler enabled for {domain}", spec=spec)


@scheduler_app.command("disable")
def disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Scheduler disable plan prepared for {domain}",
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
                process_type="scheduler",
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Scheduler disabled for {domain}", spec=spec)


@scheduler_app.command("run-once")
def run_once(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    command: str = typer.Option("php artisan schedule:run", "--command", help="Scheduler command."),
    execute: bool = typer.Option(False, "--execute", help="Execute command in app current path."),
    apply: bool = typer.Option(False, "--apply", help="Apply run-once action."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Scheduler run-once plan prepared for {domain}",
        domain=domain,
        command=command,
        execute=execute,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        with CommandLock(_lock_name(domain)):
            result = run_scheduler_once(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                command=command,
                execute=execute,
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Scheduler run-once completed for {domain}", result=result)


@scheduler_app.command("status")
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
        process_type="scheduler",
        policy=_policy_for(app_ctx),
    )
    app_ctx.emit_output("ok", f"Scheduler status for {domain}", process=spec)
