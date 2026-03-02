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
    get_current_release,
    initialize_app,
    list_releases,
    load_metadata,
    prune_releases,
    resolve_rollback_target,
    rollback_release,
    save_metadata,
)

app_cmd = typer.Typer(help="Manage Laravel application lifecycle.")


def _lock_name(domain: str) -> str:
    return f"app-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _emit_event(
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


@app_cmd.command("create")
def create(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    php: str = typer.Option("8.3", "--php", help="PHP runtime version."),
    db: str = typer.Option("mysql", "--db", help="Database engine."),
    ssl: bool = typer.Option(False, "--ssl", help="Issue SSL certificate."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing metadata."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply filesystem changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    payload = {
        "domain": domain,
        "php": php,
        "db": db,
        "ssl": ssl,
        "created_at": datetime.now(UTC).isoformat(),
    }

    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.create.started",
        domain=domain,
        message="App create started.",
        metadata={"apply": apply, "dry_run": app_ctx.dry_run},
    )
    app_ctx.emit_output(
        "ok",
        f"App create plan prepared for {domain}",
        domain=domain,
        paths={
            "root": str(paths.root),
            "releases": str(paths.releases),
            "shared": str(paths.shared),
            "metadata": str(paths.metadata),
        },
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(_lock_name(domain)):
            initialize_app(paths, payload, overwrite=force)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except AppLifecycleError as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="app.create.failed",
            domain=domain,
            message="App create failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.create.completed",
        domain=domain,
        message="App create completed.",
    )
    app_ctx.emit_output("ok", f"Application created: {domain}")


@app_cmd.command("deploy")
def deploy(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    ref: str = typer.Option("main", "--ref", help="Git ref to deploy."),
    source: Path = typer.Option(
        Path("."),
        "--source",
        help="Local source directory to deploy.",
        exists=False,
        file_okay=False,
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply filesystem changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    source_path = source.resolve()
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    keep_releases = app_ctx.config.deploy.keep_releases

    _emit_event(
        app_ctx,
        severity="info",
        event_type="deploy.started",
        domain=domain,
        message="Deploy started.",
        metadata={"apply": apply, "dry_run": app_ctx.dry_run, "ref": ref},
    )

    app_ctx.emit_output(
        "ok",
        f"Deploy plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        ref=ref,
        keep_releases=keep_releases,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(_lock_name(domain)):
            metadata = load_metadata(paths.metadata)
            release_id = deploy_release(paths, source_path, ref)
            deleted_releases = prune_releases(paths, keep_releases)

            metadata["last_deploy"] = {
                "release_id": release_id,
                "ref": ref,
                "deployed_at": datetime.now(UTC).isoformat(),
                "source": str(source_path),
            }
            save_metadata(paths.metadata, metadata)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except AppLifecycleError as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="deploy.failed",
            domain=domain,
            message="Deploy failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="deploy.completed",
        domain=domain,
        message="Deploy completed.",
        metadata={"release_id": release_id, "deleted_releases": deleted_releases},
    )
    app_ctx.emit_output(
        "ok",
        f"Deployment completed for {domain}",
        release_id=release_id,
        deleted_releases=deleted_releases,
    )


@app_cmd.command("rollback")
def rollback(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    to: str = typer.Option("previous", "--to", help="Release id or 'previous'."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply rollback. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        target = resolve_rollback_target(paths, to)
    except AppLifecycleError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"Rollback plan prepared for {domain}",
        domain=domain,
        current=get_current_release(paths),
        target=target,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit_event(
        app_ctx,
        severity="warn",
        event_type="rollback.started",
        domain=domain,
        message="Rollback started.",
        metadata={"target": target},
    )

    try:
        with CommandLock(_lock_name(domain)):
            rollback_release(paths, target)
            metadata = load_metadata(paths.metadata)
            metadata["last_rollback"] = {
                "target": target,
                "rolled_back_at": datetime.now(UTC).isoformat(),
            }
            save_metadata(paths.metadata, metadata)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except AppLifecycleError as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="rollback.failed",
            domain=domain,
            message="Rollback failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="rollback.completed",
        domain=domain,
        message="Rollback completed.",
        metadata={"target": target},
    )
    app_ctx.emit_output("ok", f"Rollback completed for {domain}", target=target)


@app_cmd.command("info")
def info(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        metadata = load_metadata(paths.metadata)
    except AppLifecycleError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    releases = list_releases(paths)
    current = get_current_release(paths)

    app_ctx.emit_output(
        "ok",
        f"Application info: {domain}",
        domain=domain,
        root=str(paths.root),
        releases=releases,
        releases_count=len(releases),
        current_release=current,
        metadata=metadata,
    )
