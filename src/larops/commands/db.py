from __future__ import annotations

import os
import socket
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.db_service import (
    DbServiceError,
    backup_filename,
    build_backup_command,
    build_restore_command,
    default_backup_dir,
    list_backups,
    run_backup,
    run_restore,
)

db_app = typer.Typer(help="Manage database backup and restore.")


def _emit(app_ctx: AppContext, severity: str, event_type: str, message: str, metadata: dict | None = None) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            message=message,
            metadata=metadata or {},
        )
    )


@db_app.command("backup")
def backup(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    database: str = typer.Option(..., "--database", help="Database name."),
    user: str = typer.Option(..., "--user", help="Database user."),
    password_env: str = typer.Option("LAROPS_DB_PASSWORD", "--password-env", help="Password env var name."),
    host: str = typer.Option("127.0.0.1", "--host", help="Database host."),
    port: int = typer.Option(3306, "--port", help="Database port."),
    target_dir: Path | None = typer.Option(None, "--target-dir", help="Backup directory.", file_okay=False),
    apply: bool = typer.Option(False, "--apply", help="Apply backup operation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    password = os.getenv(password_env, "")
    backup_dir = target_dir or default_backup_dir(Path(app_ctx.config.state_path), domain)
    backup_file = backup_dir / backup_filename(domain)

    command_preview = build_backup_command(
        backup_file=backup_file,
        database=database,
        user=user,
        password="***",
        host=host,
        port=port,
    )
    app_ctx.emit_output(
        "ok",
        f"DB backup plan prepared for {domain}",
        domain=domain,
        backup_file=str(backup_file),
        command=command_preview,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    if not password:
        app_ctx.emit_output("error", f"Password env var is empty: {password_env}")
        raise typer.Exit(code=2)

    backup_dir.mkdir(parents=True, exist_ok=True)
    command = build_backup_command(
        backup_file=backup_file,
        database=database,
        user=user,
        password=password,
        host=host,
        port=port,
    )

    try:
        with CommandLock("db-backup"):
            output = run_backup(command)
    except (CommandLockError, ShellCommandError, DbServiceError) as exc:
        _emit(app_ctx, "error", "db.backup.failed", "DB backup failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "db.backup.completed", "DB backup completed.", {"domain": domain, "file": str(backup_file)})
    app_ctx.emit_output("ok", f"DB backup completed for {domain}", backup_file=str(backup_file), output=output)


@db_app.command("restore")
def restore(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    backup_file: Path = typer.Option(..., "--backup-file", help="Backup file path.", dir_okay=False),
    database: str = typer.Option(..., "--database", help="Database name."),
    user: str = typer.Option(..., "--user", help="Database user."),
    password_env: str = typer.Option("LAROPS_DB_PASSWORD", "--password-env", help="Password env var name."),
    host: str = typer.Option("127.0.0.1", "--host", help="Database host."),
    port: int = typer.Option(3306, "--port", help="Database port."),
    apply: bool = typer.Option(False, "--apply", help="Apply restore operation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    password = os.getenv(password_env, "")

    try:
        preview = build_restore_command(
            backup_file=backup_file,
            database=database,
            user=user,
            password="***",
            host=host,
            port=port,
        )
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"DB restore plan prepared for {domain}",
        domain=domain,
        backup_file=str(backup_file),
        command=preview,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    if not password:
        app_ctx.emit_output("error", f"Password env var is empty: {password_env}")
        raise typer.Exit(code=2)

    command = build_restore_command(
        backup_file=backup_file,
        database=database,
        user=user,
        password=password,
        host=host,
        port=port,
    )
    try:
        with CommandLock("db-restore"):
            output = run_restore(command)
    except (CommandLockError, ShellCommandError, DbServiceError) as exc:
        _emit(app_ctx, "error", "db.restore.failed", "DB restore failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "db.restore.completed", "DB restore completed.", {"domain": domain, "file": str(backup_file)})
    app_ctx.emit_output("ok", f"DB restore completed for {domain}", output=output)


@db_app.command("list-backups")
def list_backup_files(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    target_dir: Path | None = typer.Option(None, "--target-dir", help="Backup directory.", file_okay=False),
) -> None:
    app_ctx: AppContext = ctx.obj
    backup_dir = target_dir or default_backup_dir(Path(app_ctx.config.state_path), domain)
    files = list_backups(backup_dir)
    app_ctx.emit_output(
        "ok",
        f"Backup list for {domain}",
        domain=domain,
        backup_dir=str(backup_dir),
        backups=files,
        count=len(files),
    )

