from __future__ import annotations

import re
import socket
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.runtime_process import (
    RuntimeProcessError,
    disable_process,
    enable_process,
    restart_process,
    status_process,
)

worker_app = typer.Typer(help="Manage queue worker process.")


def _lock_name(domain: str) -> str:
    return f"worker-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _emit(
    app_ctx: AppContext,
    *,
    severity: str,
    event_type: str,
    domain: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            app=domain,
            message=message,
            metadata=metadata or {},
        )
    )


def _policy_for(app_ctx: AppContext) -> dict:
    return app_ctx.config.runtime_policy.worker.model_dump()


@worker_app.command("enable")
def enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    queue: str = typer.Option("default", "--queue", help="Queue name."),
    concurrency: int = typer.Option(1, "--concurrency", help="Worker concurrency setting."),
    tries: int = typer.Option(3, "--tries", help="Worker retry attempts."),
    timeout: int = typer.Option(90, "--timeout", help="Worker timeout in seconds."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    if concurrency < 1:
        app_ctx.emit_output("error", "Worker concurrency must be >= 1.")
        raise typer.Exit(code=2)
    if tries < 1:
        app_ctx.emit_output("error", "Worker tries must be >= 1.")
        raise typer.Exit(code=2)
    if timeout < 1:
        app_ctx.emit_output("error", "Worker timeout must be >= 1 second.")
        raise typer.Exit(code=2)
    options = {
        "queue": queue,
        "concurrency": concurrency,
        "tries": tries,
        "timeout": timeout,
    }

    app_ctx.emit_output(
        "ok",
        f"Worker enable plan prepared for {domain}",
        domain=domain,
        options=options,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="info",
        event_type="worker.enable.started",
        domain=domain,
        message="Worker enable started.",
        metadata=options,
    )
    try:
        with CommandLock(_lock_name(domain)):
            spec = enable_process(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                service_user=app_ctx.config.systemd.user,
                domain=domain,
                process_type="worker",
                options=options,
                policy=_policy_for(app_ctx),
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="worker.enable.failed",
            domain=domain,
            message="Worker enable failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="worker.enable.completed",
        domain=domain,
        message="Worker enable completed.",
    )
    app_ctx.emit_output("ok", f"Worker enabled for {domain}", spec=spec)


@worker_app.command("disable")
def disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Worker disable plan prepared for {domain}",
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
                process_type="worker",
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Worker disabled for {domain}", spec=spec)


@worker_app.command("restart")
def restart(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply runtime change."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"Worker restart plan prepared for {domain}",
        domain=domain,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        with CommandLock(_lock_name(domain)):
            spec = restart_process(
                state_path=Path(app_ctx.config.state_path),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                process_type="worker",
                policy=_policy_for(app_ctx),
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"Worker restarted for {domain}", spec=spec)


@worker_app.command("status")
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
        process_type="worker",
        policy=_policy_for(app_ctx),
    )
    app_ctx.emit_output("ok", f"Worker status for {domain}", process=spec)
