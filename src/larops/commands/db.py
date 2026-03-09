from __future__ import annotations

import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.db_service import (
    backup_status,
    DbServiceError,
    backup_filename,
    build_backup_command,
    build_restore_command,
    default_password_file,
    default_backup_dir,
    default_credential_file,
    generate_database_password,
    list_backups,
    manifest_path,
    normalize_db_engine,
    normalize_database_name,
    normalize_database_user,
    prune_backups,
    provision_database,
    read_backup_manifest,
    restore_verify_backup,
    restore_verify_report_path,
    failed_restore_verify_report,
    run_backup,
    run_restore,
    verify_backup,
    write_restore_verify_report,
    write_backup_manifest,
    write_mysql_credentials,
    write_postgres_credentials,
)
from larops.services.db_offsite_service import (
    DbOffsiteError,
    offsite_restore_verify,
    offsite_status,
    upload_offsite_backup,
)
from larops.services.db_systemd import (
    DbAutoBackupError,
    disable_db_backup_timer,
    enable_db_backup_timer,
    status_db_backup_timer,
)

db_app = typer.Typer(help="Manage database backup and restore.")
credential_app = typer.Typer(help="Manage secure DB credentials.")
auto_backup_app = typer.Typer(help="Manage DB auto-backup timer.")
offsite_app = typer.Typer(help="Manage encrypted offsite backups.")
db_app.add_typer(credential_app, name="credential")
db_app.add_typer(auto_backup_app, name="auto-backup")
db_app.add_typer(offsite_app, name="offsite")


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


def _resolve_password_file(
    app_ctx: AppContext,
    domain: str,
    password_file: Path | None,
    *,
    engine: str,
) -> Path:
    return password_file or default_password_file(Path(app_ctx.config.state_path), domain, engine=engine)


def _resolve_cli_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


def _emit_db_provision_summary(app_ctx: AppContext, *, provision: dict) -> None:
    if app_ctx.json_output:
        return
    app_ctx.emit_output("ok", f"  engine: {provision['engine']}")
    app_ctx.emit_output("ok", f"  database: {provision['database']}")
    app_ctx.emit_output("ok", f"  user: {provision['user']}")
    app_ctx.emit_output("ok", f"  host: {provision['host']}:{provision['port']}")
    app_ctx.emit_output("ok", f"  credential file: {provision['credential_file']}")
    app_ctx.emit_output("ok", f"  password file: {provision['password_file']}")


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


@db_app.command("provision")
def provision(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    database: str | None = typer.Option(None, "--database", help="Database name (default: derived from domain)."),
    user: str | None = typer.Option(None, "--user", help="Database user (default: derived from domain)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Application DB host."),
    port: int | None = typer.Option(None, "--port", help="Application DB port (default: mysql=3306, postgres=5432)."),
    password_env: str = typer.Option(
        "",
        "--password-env",
        help="Optional env var containing the application DB password. If omitted, LarOps generates one.",
    ),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Application credential file path.",
        dir_okay=False,
    ),
    password_file: Path | None = typer.Option(
        None,
        "--password-file",
        help="Application password file path.",
        dir_okay=False,
    ),
    admin_credential_file: Path | None = typer.Option(
        None,
        "--admin-credential-file",
        help="Optional admin credential file for provisioning. MySQL falls back to local root socket auth when omitted.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply database provisioning."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
        resolved_database = normalize_database_name(database or domain)
        resolved_user = normalize_database_user(user or domain, engine=normalized_engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    resolved_port = port if port is not None else (5432 if normalized_engine == "postgres" else 3306)
    if resolved_port < 1:
        app_ctx.emit_output("error", "Database port must be >= 1.")
        raise typer.Exit(code=2)

    target_credential_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    target_password_file = _resolve_password_file(app_ctx, domain, password_file, engine=normalized_engine)
    supplied_password = os.getenv(password_env, "").strip() if password_env else ""
    generated_password = not bool(supplied_password)

    app_ctx.emit_output(
        "ok",
        f"DB provision plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        database=resolved_database,
        user=resolved_user,
        host=host,
        port=resolved_port,
        credential_file=str(target_credential_file),
        password_file=str(target_password_file),
        admin_credential_file=str(admin_credential_file) if admin_credential_file is not None else None,
        password_source="generated" if generated_password else password_env,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    password = supplied_password or generate_database_password()
    try:
        with CommandLock("db-provision"):
            result = provision_database(
                engine=normalized_engine,
                database=resolved_database,
                user=resolved_user,
                password=password,
                app_host=host,
                app_port=resolved_port,
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                credential_file=target_credential_file,
                password_file=target_password_file,
                admin_credential_file=admin_credential_file,
            )
    except (CommandLockError, DbServiceError) as exc:
        _emit(app_ctx, "error", "db.provision.failed", "DB provision failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "db.provision.completed",
        "DB provision completed.",
        {"domain": domain, "database": resolved_database, "user": resolved_user, "engine": normalized_engine},
    )
    app_ctx.emit_output(
        "ok",
        f"DB provision completed for {domain}",
        provision=result,
    )
    _emit_db_provision_summary(app_ctx, provision=result)


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
    retain_count: int = typer.Option(10, "--retain-count", help="Keep at most this many backups per domain."),
    skip_offsite_upload: bool = typer.Option(
        False,
        "--skip-offsite-upload",
        help="Skip configured offsite upload for this backup run.",
    ),
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
        retain_count=retain_count,
        offsite_enabled=app_ctx.config.backups.offsite.enabled and not skip_offsite_upload,
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

    offsite_result = None
    try:
        with CommandLock("db-backup"):
            output = run_backup(command)
            manifest = write_backup_manifest(
                backup_file=backup_file,
                domain=domain,
                engine=normalized_engine,
                database=database,
            )
            deleted = prune_backups(backup_dir=backup_dir, retain_count=retain_count)
            offsite_result = None
            if app_ctx.config.backups.offsite.enabled and not skip_offsite_upload:
                offsite_result = upload_offsite_backup(
                    domain=domain,
                    backup_file=backup_file,
                    encryption_config=app_ctx.config.backups.encryption,
                    offsite_config=app_ctx.config.backups.offsite,
                )
    except (CommandLockError, ShellCommandError, DbServiceError) as exc:
        _emit(app_ctx, "error", "db.backup.failed", "DB backup failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    except DbOffsiteError as exc:
        _emit(
            app_ctx,
            "error",
            "db.backup.offsite_failed",
            "DB backup offsite upload failed.",
            {"error": str(exc), "domain": domain, "file": str(backup_file)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "db.backup.completed",
        "DB backup completed.",
        {
            "domain": domain,
            "file": str(backup_file),
            "manifest": str(manifest),
            "deleted": deleted,
            "offsite": offsite_result,
        },
    )
    app_ctx.emit_output(
        "ok",
        f"DB backup completed for {domain}",
        backup_file=str(backup_file),
        manifest_file=str(manifest),
        deleted_backups=deleted,
        offsite=offsite_result,
        output=output,
    )


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


@db_app.command("status")
def status(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    target_dir: Path | None = typer.Option(None, "--target-dir", help="Backup directory.", file_okay=False),
    stale_hours: int = typer.Option(24, "--stale-hours", help="Warn if latest backup is older than this many hours."),
    offsite_stale_hours: int = typer.Option(
        24,
        "--offsite-stale-hours",
        help="Warn if latest offsite backup is older than this many hours.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    if stale_hours < 1:
        app_ctx.emit_output("error", "--stale-hours must be >= 1.")
        raise typer.Exit(code=2)
    backup_dir = target_dir or default_backup_dir(Path(app_ctx.config.state_path), domain)
    summary = backup_status(backup_dir=backup_dir, stale_hours=stale_hours)
    if summary["latest_backup"]:
        latest_backup = Path(summary["latest_backup"])
        summary["manifest"] = read_backup_manifest(latest_backup)
        summary["manifest_file"] = str(manifest_path(latest_backup))
    if app_ctx.config.backups.offsite.enabled:
        try:
            summary["offsite"] = offsite_status(
                domain=domain,
                offsite_config=app_ctx.config.backups.offsite,
                stale_hours=offsite_stale_hours,
            )
            if summary["status"] == "ok" and summary["offsite"]["status"] != "ok":
                summary["status"] = summary["offsite"]["status"]
        except DbOffsiteError as exc:
            summary["offsite"] = {"status": "error", "error": str(exc)}
            summary["status"] = "error"
    app_ctx.emit_output(summary["status"], f"DB backup status for {domain}", domain=domain, status_report=summary)


@db_app.command("verify")
def verify(
    ctx: typer.Context,
    backup_file: Path = typer.Option(..., "--backup-file", help="Backup file path.", dir_okay=False),
    manifest_file: Path | None = typer.Option(None, "--manifest-file", help="Manifest file path.", dir_okay=False),
    check_gzip: bool = typer.Option(True, "--check-gzip/--no-check-gzip", help="Validate gzip stream integrity."),
    require_manifest: bool = typer.Option(
        False,
        "--require-manifest/--allow-missing-manifest",
        help="Fail if the manifest file is missing.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        result = verify_backup(
            backup_file=backup_file,
            manifest_file=manifest_file,
            check_gzip=check_gzip,
            require_manifest=require_manifest,
        )
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    if result["status"] == "error":
        app_ctx.emit_output("error", "DB backup verification failed.", verification=result)
        raise typer.Exit(code=2)
    app_ctx.emit_output(result["status"], "DB backup verification completed.", verification=result)


@db_app.command("restore-verify")
def restore_verify(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    backup_file: Path = typer.Option(..., "--backup-file", help="Backup file path.", dir_okay=False),
    database: str = typer.Option(..., "--database", help="Source database name."),
    verify_database: str | None = typer.Option(
        None,
        "--verify-database",
        help="Temporary database name override used for restore verification.",
    ),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
        help="Credential file path.",
        dir_okay=False,
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply restore verification."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    secret_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    report_file = restore_verify_report_path(Path(app_ctx.config.state_path), domain)
    app_ctx.emit_output(
        "ok",
        f"DB restore-verify plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        backup_file=str(backup_file),
        credential_file=str(secret_file),
        database=database,
        verify_database=verify_database,
        report_file=str(report_file),
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("db-restore-verify"):
            result = restore_verify_backup(
                backup_file=backup_file,
                database=database,
                credential_file=secret_file,
                engine=normalized_engine,
                verify_database=verify_database,
            )
            report = write_restore_verify_report(
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                payload=result,
            )
    except (CommandLockError, DbServiceError, ShellCommandError) as exc:
        report = write_restore_verify_report(
            state_path=Path(app_ctx.config.state_path),
            domain=domain,
            payload=failed_restore_verify_report(
                error=str(exc),
                context={
                    "engine": normalized_engine,
                    "backup_file": str(backup_file),
                    "database": database,
                    "verify_database": verify_database,
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            ),
        )
        _emit(
            app_ctx,
            "error",
            "db.restore_verify.failed",
            "DB restore verify failed.",
            {"error": str(exc), "domain": domain, "report_file": str(report)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "db.restore_verify.completed",
        "DB restore verify completed.",
        {"domain": domain, "backup_file": str(backup_file), "report_file": str(report)},
    )
    app_ctx.emit_output("ok", f"DB restore verify completed for {domain}", verification=result, report_file=str(report))


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


@auto_backup_app.command("enable")
def auto_backup_enable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    database: str = typer.Option(..., "--database", help="Database name."),
    on_calendar: str = typer.Option("*-*-* 02:00:00", "--on-calendar", help="systemd OnCalendar schedule."),
    randomized_delay: int = typer.Option(900, "--randomized-delay", help="RandomizedDelaySec in seconds."),
    user: str = typer.Option("root", "--user", help="System user used by backup service."),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    credential_file: Path | None = typer.Option(None, "--credential-file", help="Credential file path.", dir_okay=False),
    target_dir: Path | None = typer.Option(None, "--target-dir", help="Backup directory.", file_okay=False),
    retain_count: int = typer.Option(10, "--retain-count", help="Keep at most this many backups per domain."),
    apply: bool = typer.Option(False, "--apply", help="Apply auto-backup setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    resolved_credential_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    resolved_target_dir = target_dir or default_backup_dir(Path(app_ctx.config.state_path), domain)
    config_path = _resolve_cli_config_path(app_ctx)

    app_ctx.emit_output(
        "ok",
        f"DB auto-backup enable plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        database=database,
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        user=user,
        larops_bin=larops_bin,
        config_path=str(config_path),
        credential_file=str(resolved_credential_file),
        target_dir=str(resolved_target_dir),
        retain_count=retain_count,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"db-auto-backup-enable-{domain}"):
            result = enable_db_backup_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                user=user,
                domain=domain,
                on_calendar=on_calendar,
                randomized_delay_seconds=randomized_delay,
                larops_bin=larops_bin,
                config_path=config_path,
                engine=normalized_engine,
                database=database,
                credential_file=resolved_credential_file,
                target_dir=resolved_target_dir,
                retain_count=retain_count,
            )
    except (CommandLockError, DbAutoBackupError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"DB auto-backup enabled for {domain}", auto_backup=result)


@auto_backup_app.command("disable")
def auto_backup_disable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    remove_units: bool = typer.Option(False, "--remove-units", help="Remove timer/service unit files after disable."),
    apply: bool = typer.Option(False, "--apply", help="Apply auto-backup disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"DB auto-backup disable plan prepared for {domain}",
        domain=domain,
        remove_units=remove_units,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"db-auto-backup-disable-{domain}"):
            result = disable_db_backup_timer(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                remove_units=remove_units,
            )
    except (CommandLockError, DbAutoBackupError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output("ok", f"DB auto-backup disabled for {domain}", auto_backup=result)


@auto_backup_app.command("status")
def auto_backup_status(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_db_backup_timer(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
        domain=domain,
    )
    app_ctx.emit_output("ok", f"DB auto-backup status for {domain}", auto_backup=result)


@offsite_app.command("upload")
def offsite_upload(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    backup_file: Path = typer.Option(..., "--backup-file", help="Local backup file path.", dir_okay=False),
    apply: bool = typer.Option(False, "--apply", help="Upload backup to offsite storage."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        f"DB offsite upload plan prepared for {domain}",
        domain=domain,
        backup_file=str(backup_file),
        bucket=app_ctx.config.backups.offsite.bucket,
        prefix=app_ctx.config.backups.offsite.prefix,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"db-offsite-upload-{domain}"):
            result = upload_offsite_backup(
                domain=domain,
                backup_file=backup_file,
                encryption_config=app_ctx.config.backups.encryption,
                offsite_config=app_ctx.config.backups.offsite,
            )
    except (CommandLockError, DbOffsiteError) as exc:
        _emit(app_ctx, "error", "db.offsite.upload.failed", "DB offsite upload failed.", {"error": str(exc), "domain": domain})
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(app_ctx, "info", "db.offsite.upload.completed", "DB offsite upload completed.", {"domain": domain, **result})
    app_ctx.emit_output("ok", f"DB offsite upload completed for {domain}", offsite=result)


@offsite_app.command("status")
def offsite_status_cmd(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    stale_hours: int = typer.Option(24, "--stale-hours", help="Warn if latest offsite backup is older than this many hours."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        result = offsite_status(
            domain=domain,
            offsite_config=app_ctx.config.backups.offsite,
            stale_hours=stale_hours,
        )
    except DbOffsiteError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(result["status"], f"DB offsite status for {domain}", offsite=result)


@offsite_app.command("restore-verify")
def offsite_restore_verify_cmd(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    engine: str = typer.Option("mysql", "--engine", help="Database engine (mysql|postgres)."),
    database: str = typer.Option(..., "--database", help="Source database name."),
    object_key: str | None = typer.Option(None, "--object-key", help="Explicit offsite object key to restore."),
    verify_database: str | None = typer.Option(None, "--verify-database", help="Temporary database name for restore verification."),
    credential_file: Path | None = typer.Option(None, "--credential-file", help="Credential file path.", dir_okay=False),
    apply: bool = typer.Option(False, "--apply", help="Download, decrypt, and restore-verify from offsite storage."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        normalized_engine = normalize_db_engine(engine)
    except DbServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    secret_file = _resolve_credential_path(app_ctx, domain, credential_file, engine=normalized_engine)
    report_file = restore_verify_report_path(Path(app_ctx.config.state_path), domain)
    app_ctx.emit_output(
        "ok",
        f"DB offsite restore-verify plan prepared for {domain}",
        domain=domain,
        engine=normalized_engine,
        database=database,
        object_key=object_key,
        verify_database=verify_database,
        credential_file=str(secret_file),
        bucket=app_ctx.config.backups.offsite.bucket,
        report_file=str(report_file),
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(f"db-offsite-restore-verify-{domain}"):
            result = offsite_restore_verify(
                domain=domain,
                database=database,
                credential_file=secret_file,
                engine=normalized_engine,
                encryption_config=app_ctx.config.backups.encryption,
                offsite_config=app_ctx.config.backups.offsite,
                object_key=object_key,
                verify_database=verify_database,
            )
            report = write_restore_verify_report(
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                payload=result,
            )
    except (CommandLockError, DbOffsiteError, DbServiceError, ShellCommandError) as exc:
        report = write_restore_verify_report(
            state_path=Path(app_ctx.config.state_path),
            domain=domain,
            payload=failed_restore_verify_report(
                error=str(exc),
                context={
                    "engine": normalized_engine,
                    "database": database,
                    "verify_database": verify_database,
                    "object_key": object_key,
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            ),
        )
        _emit(
            app_ctx,
            "error",
            "db.offsite.restore_verify.failed",
            "DB offsite restore verify failed.",
            {"error": str(exc), "domain": domain, "report_file": str(report)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        "info",
        "db.offsite.restore_verify.completed",
        "DB offsite restore verify completed.",
        {"domain": domain, "report_file": str(report), "object_key": result.get("offsite_object_key")},
    )
    app_ctx.emit_output("ok", f"DB offsite restore verify completed for {domain}", verification=result, report_file=str(report))
