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
    default_credential_file,
    list_backups,
    normalize_db_engine,
    run_backup,
    run_restore,
    write_mysql_credentials,
    write_postgres_credentials,
)

db_app = typer.Typer(help="Manage database backup and restore.")
credential_app = typer.Typer(help="Manage secure DB credentials.")
db_app.add_typer(credential_app, name="credential")


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


def _resolve_credential_path(
    app_ctx: AppContext,
    domain: str,
    credential_file: Path | None,
    *,
    engine: str,
) -> Path:
    return credential_file or default_credential_file(Path(app_ctx.config.state_path), domain, engine=engine)


@credential_app.command("set")
def credential_set(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    user: str = typer.Option(..., "--user", help="Database user."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    password_env: str = typer.Option("LAROPS_DB_PASSWORD", "--password-env", help="Env var containing DB password."),
    host: str = typer.Option("127.0.0.1", "--host", help="Database host."),
    port: int | None = typer.Option(None, "--port", help="Database port (default: mysql=3306, postgres=5432)."),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Credential file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply credential write."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    resolved_port = port if port is not None else (5432 if normalized_engine == "postgres" else 3306)
    if resolved_port < 1:
        app_ctx.emit_output("error", "Database port must be >= 1.")
        raise typer.Exit(code=2)

    target = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    password = os.getenv(password_env, "")

    app_ctx.emit_output(
        "ok",
        f"Credential set plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        credential_file=str(target),
        user=user,
        host=host,
        port=resolved_port,
        password_env=password_env,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    if not password:
        app_ctx.emit_output("error", f"Password env var is empty: {password_env}")
        raise typer.Exit(code=2)

    try:
        with CommandLock("db-credential-set"):
            if normalized_engine == "mysql":
                write_mysql_credentials(
                    credential_file=target,
                    user=user,
                    password=password,
                    host=host,
                    port=resolved_port,
                )
            else:
                write_postgres_credentials(
                    credential_file=target,
                    user=user,
                    password=password,
                    host=host,
                    port=resolved_port,
                )
    except (CommandLockError, DbServiceError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "db.credential.set", "DB credential file updated.", {"domain": domain, "file": str(target)})
    app_ctx.emit_output("ok", f"Credential file updated for {domain}", credential_file=str(target), engine=normalized_engine)


@credential_app.command("show")
def credential_show(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Credential file path.",
        dir_okay=False,
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    target = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    exists = target.exists()
    mode = oct(target.stat().st_mode & 0o777) if exists else None
    app_ctx.emit_output(
        "ok",
        f"Credential file status for {domain}",
        domain=domain,
        engine=normalized_engine,
        credential_file=str(target),
        exists=exists,
        mode=mode,
    )


@db_app.command("backup")
def backup(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    database: str = typer.Option(..., "--database", help="Database name."),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Credential file path.",
        dir_okay=False,
    ),
    target_dir: Path | None = typer.Option(None, "--target-dir", help="Backup directory.", file_okay=False),
    apply: bool = typer.Option(False, "--apply", help="Apply backup operation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    backup_dir = target_dir or default_backup_dir(Path(app_ctx.config.state_path), domain)
    backup_file = backup_dir / backup_filename(domain)
    secret_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)

    try:
        command_preview = build_backup_command(
            backup_file=backup_file,
            database=database,
            credential_file=secret_file,
            engine=normalized_engine,
        )
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"DB backup plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        backup_file=str(backup_file),
        credential_file=str(secret_file),
        command=command_preview,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    command = build_backup_command(
        backup_file=backup_file,
        database=database,
        credential_file=secret_file,
        engine=normalized_engine,
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
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    backup_file: Path = typer.Option(..., "--backup-file", help="Backup file path.", dir_okay=False),
    database: str = typer.Option(..., "--database", help="Database name."),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Credential file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply restore operation."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    secret_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)

    try:
        preview = build_restore_command(
            backup_file=backup_file,
            database=database,
            credential_file=secret_file,
            engine=normalized_engine,
        )
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"DB restore plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        backup_file=str(backup_file),
        credential_file=str(secret_file),
        command=preview,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    command = build_restore_command(
        backup_file=backup_file,
        database=database,
        credential_file=secret_file,
        engine=normalized_engine,
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
