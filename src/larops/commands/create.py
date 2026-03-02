from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.app_lifecycle import (
    AppLifecycleError,
    deploy_release,
    get_app_paths,
    initialize_app,
    load_metadata,
    prune_releases,
    save_metadata,
)
from larops.services.runtime_process import RuntimeProcessError, enable_process

create_app = typer.Typer(help="WordOps-style create shortcuts.")


def _lock_name(domain: str) -> str:
    return f"create-site-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


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


@create_app.command("site")
def create_site(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    source: Path | None = typer.Option(
        None,
        "--source",
        help="Laravel source directory to deploy (default: deploy.source_base_path/<domain>).",
        exists=False,
        file_okay=False,
    ),
    ref: str = typer.Option("main", "--ref", help="Source ref metadata."),
    deploy: bool = typer.Option(True, "--deploy/--no-deploy", help="Deploy source after create."),
    worker: bool = typer.Option(False, "--worker/--no-worker", help="Enable queue worker."),
    scheduler: bool = typer.Option(False, "--scheduler/--no-scheduler", help="Enable scheduler."),
    horizon: bool = typer.Option(False, "--horizon/--no-horizon", help="Enable horizon."),
    queue: str = typer.Option("default", "--queue", help="Worker queue."),
    concurrency: int = typer.Option(1, "--concurrency", help="Worker concurrency."),
    tries: int = typer.Option(3, "--tries", help="Worker tries."),
    timeout: int = typer.Option(90, "--timeout", help="Worker timeout in seconds."),
    schedule_command: str = typer.Option(
        "php artisan schedule:run",
        "--schedule-command",
        help="Scheduler command.",
    ),
    php: str = typer.Option("8.3", "--php", help="PHP runtime version."),
    db: str = typer.Option("mysql", "--db", help="Database engine."),
    ssl: bool = typer.Option(False, "--ssl", help="Enable SSL metadata flag."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing app metadata."),
    apply: bool = typer.Option(False, "--apply", help="Apply create site workflow."),
) -> None:
    app_ctx: AppContext = ctx.obj
    if not deploy and (worker or scheduler or horizon):
        app_ctx.emit_output(
            "error",
            "Runtime enable requires --deploy. Remove runtime flags or use --deploy.",
        )
        raise typer.Exit(code=2)

    source_path = (
        source.resolve()
        if source is not None
        else (Path(app_ctx.config.deploy.source_base_path) / domain).resolve()
    )
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    runtime_plan = {
        "worker": worker,
        "scheduler": scheduler,
        "horizon": horizon,
    }
    app_ctx.emit_output(
        "ok",
        f"Create site plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        ref=ref,
        deploy=deploy,
        runtime=runtime_plan,
        php=php,
        db=db,
        ssl=ssl,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="info",
        event_type="create.site.started",
        domain=domain,
        message="Create site started.",
        metadata={
            "ref": ref,
            "deploy": deploy,
            "runtime": runtime_plan,
        },
    )

    try:
        with CommandLock(_lock_name(domain)):
            metadata_payload = {
                "domain": domain,
                "php": php,
                "db": db,
                "ssl": ssl,
                "created_at": datetime.now(UTC).isoformat(),
            }
            initialize_app(paths, metadata_payload, overwrite=force)
            release_id: str | None = None
            deleted_releases: list[str] = []

            if deploy:
                release_id = deploy_release(paths, source_path, ref)
                deleted_releases = prune_releases(paths, app_ctx.config.deploy.keep_releases)
                metadata = load_metadata(paths.metadata)
                metadata["last_deploy"] = {
                    "release_id": release_id,
                    "ref": ref,
                    "deployed_at": datetime.now(UTC).isoformat(),
                    "source": str(source_path),
                }
                save_metadata(paths.metadata, metadata)

            runtime_results: dict[str, dict] = {}
            if worker:
                runtime_results["worker"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="worker",
                    options={
                        "queue": queue,
                        "concurrency": concurrency,
                        "tries": tries,
                        "timeout": timeout,
                    },
                )
            if scheduler:
                runtime_results["scheduler"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="scheduler",
                    options={"command": schedule_command},
                )
            if horizon:
                runtime_results["horizon"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="horizon",
                    options={},
                )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (AppLifecycleError, RuntimeProcessError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="create.site.failed",
            domain=domain,
            message="Create site failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="create.site.completed",
        domain=domain,
        message="Create site completed.",
        metadata={"deploy": deploy, "runtime": runtime_plan},
    )
    app_ctx.emit_output(
        "ok",
        f"Create site completed for {domain}",
        deployed=deploy,
        release_id=release_id,
        deleted_releases=deleted_releases,
        runtime_enabled=runtime_results,
    )
