from __future__ import annotations

import re
import socket
from pathlib import Path

import typer

from larops.commands.create import create_site, manage_site_runtime
from larops.core.locks import CommandLock, CommandLockError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.runtime_process import RuntimeProcessError
from larops.services.site_delete import SiteDeleteError, create_delete_checkpoint, default_checkpoint_dir, purge_site

site_app = typer.Typer(help="Site lifecycle shortcuts.")
runtime_app = typer.Typer(help="Manage runtime services for a site.")
site_app.command("create")(create_site)
site_app.add_typer(runtime_app, name="runtime")


def _delete_lock_name(domain: str) -> str:
    return f"site-delete-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


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


@runtime_app.command("enable")
def runtime_enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    worker: bool = typer.Option(False, "--worker/--no-worker", "-w", help="Enable queue worker."),
    scheduler: bool = typer.Option(False, "--scheduler/--no-scheduler", "-s", help="Enable scheduler."),
    horizon: bool = typer.Option(False, "--horizon/--no-horizon", help="Enable horizon."),
    queue: str = typer.Option("default", "--queue", "-q", help="Worker queue."),
    concurrency: int = typer.Option(1, "--concurrency", "-c", help="Worker concurrency."),
    tries: int = typer.Option(3, "--tries", "-t", help="Worker tries."),
    timeout: int = typer.Option(90, "--timeout", help="Worker timeout in seconds."),
    schedule_command: str = typer.Option(
        "php artisan schedule:run",
        "--schedule-command",
        help="Scheduler command.",
    ),
    apply: bool = typer.Option(False, "--apply", "-a", help="Apply runtime changes."),
) -> None:
    app_ctx: AppContext = ctx.obj
    manage_site_runtime(
        app_ctx=app_ctx,
        mode="enable",
        domain=domain,
        queue=queue,
        concurrency=concurrency,
        tries=tries,
        timeout=timeout,
        schedule_command=schedule_command,
        apply=apply,
        worker=worker,
        scheduler=scheduler,
        horizon=horizon,
    )


@runtime_app.command("disable")
def runtime_disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    worker: bool = typer.Option(False, "--worker/--no-worker", "-w", help="Disable queue worker."),
    scheduler: bool = typer.Option(False, "--scheduler/--no-scheduler", "-s", help="Disable scheduler."),
    horizon: bool = typer.Option(False, "--horizon/--no-horizon", help="Disable horizon."),
    apply: bool = typer.Option(False, "--apply", "-a", help="Apply runtime changes."),
) -> None:
    app_ctx: AppContext = ctx.obj
    manage_site_runtime(
        app_ctx=app_ctx,
        mode="disable",
        domain=domain,
        queue="default",
        concurrency=1,
        tries=3,
        timeout=90,
        schedule_command="php artisan schedule:run",
        apply=apply,
        worker=worker,
        scheduler=scheduler,
        horizon=horizon,
    )


@runtime_app.command("status")
def runtime_status(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    worker: bool = typer.Option(False, "--worker/--no-worker", "-w", help="Show worker status only."),
    scheduler: bool = typer.Option(False, "--scheduler/--no-scheduler", "-s", help="Show scheduler status only."),
    horizon: bool = typer.Option(False, "--horizon/--no-horizon", help="Show horizon status only."),
) -> None:
    app_ctx: AppContext = ctx.obj
    manage_site_runtime(
        app_ctx=app_ctx,
        mode="status",
        domain=domain,
        queue="default",
        concurrency=1,
        tries=3,
        timeout=90,
        schedule_command="php artisan schedule:run",
        apply=False,
        worker=worker,
        scheduler=scheduler,
        horizon=horizon,
    )


@site_app.command("delete")
def site_delete(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    purge: bool = typer.Option(False, "--purge", help="Delete all app/runtime metadata and files."),
    checkpoint: bool = typer.Option(True, "--checkpoint/--no-checkpoint", help="Create checkpoint before delete."),
    checkpoint_dir: Path | None = typer.Option(
        None,
        "--checkpoint-dir",
        help="Custom checkpoint directory.",
        file_okay=False,
    ),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Safety guard: must exactly match domain unless --no-prompt is used.",
    ),
    no_prompt: bool = typer.Option(False, "--no-prompt", help="Bypass confirm-domain guard."),
    apply: bool = typer.Option(False, "--apply", "-a", help="Apply site delete workflow."),
) -> None:
    app_ctx: AppContext = ctx.obj
    state_path = Path(app_ctx.config.state_path)
    base_releases_path = Path(app_ctx.config.deploy.releases_path)
    checkpoint_root = checkpoint_dir or default_checkpoint_dir(state_path, domain)

    if not purge:
        app_ctx.emit_output("error", "Site delete requires --purge for destructive operation.")
        raise typer.Exit(code=2)
    if not no_prompt and confirm != domain:
        app_ctx.emit_output(
            "error",
            "Guard check failed. Use --confirm <domain> or --no-prompt to continue.",
        )
        raise typer.Exit(code=2)

    app_ctx.emit_output(
        "ok",
        f"Site delete plan prepared for {domain}",
        domain=domain,
        purge=purge,
        checkpoint=checkpoint,
        checkpoint_dir=str(checkpoint_root),
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="warning",
        event_type="site.delete.started",
        domain=domain,
        message="Site delete started.",
        metadata={"purge": purge, "checkpoint": checkpoint},
    )
    checkpoint_file: str | None = None
    try:
        with CommandLock(_delete_lock_name(domain)):
            if checkpoint:
                checkpoint_path = create_delete_checkpoint(
                    base_releases_path=base_releases_path,
                    state_path=state_path,
                    domain=domain,
                    checkpoint_dir=checkpoint_root,
                )
                checkpoint_file = str(checkpoint_path)

            result = purge_site(
                base_releases_path=base_releases_path,
                state_path=state_path,
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (SiteDeleteError, RuntimeProcessError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="site.delete.failed",
            domain=domain,
            message="Site delete failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="site.delete.completed",
        domain=domain,
        message="Site delete completed.",
        metadata={"checkpoint": checkpoint_file},
    )
    app_ctx.emit_output(
        "ok",
        f"Site deleted for {domain}",
        domain=domain,
        checkpoint_file=checkpoint_file,
        result=result,
    )
