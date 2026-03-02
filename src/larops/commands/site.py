from __future__ import annotations

import typer

from larops.commands.create import create_site, manage_site_runtime
from larops.runtime import AppContext

site_app = typer.Typer(help="Site lifecycle shortcuts.")
runtime_app = typer.Typer(help="Manage runtime services for a site.")
site_app.command("create")(create_site)
site_app.add_typer(runtime_app, name="runtime")


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
