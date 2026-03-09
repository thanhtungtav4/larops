from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import string
from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import ShellCommandError, run_command


class DbServiceError(RuntimeError):
    pass


_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_DB_USER_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SUPPORTED_ENGINES = ("mysql", "postgres")


def backup_filename(domain: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_domain = domain.replace(".", "_")
    return f"{safe_domain}_{stamp}.sql.gz"


def default_backup_dir(state_path: Path, domain: str) -> Path:
    return state_path / "backups" / domain


def normalize_db_engine(engine: str) -> str:
    normalized = engine.strip().lower()
    if normalized == "postgresql":
        normalized = "postgres"
    if normalized not in _SUPPORTED_ENGINES:
        supported = ", ".join(_SUPPORTED_ENGINES)
        raise DbServiceError(f"Unsupported DB engine: {engine}. Supported: {supported}.")
    return normalized


def default_credential_file(state_path: Path, domain: str, *, engine: str = "mysql") -> Path:
    normalized = normalize_db_engine(engine)
    extension = "cnf" if normalized == "mysql" else "pgpass"
    return state_path / "secrets" / "db" / f"{domain}.{extension}"


def default_password_file(state_path: Path, domain: str, *, engine: str = "mysql") -> Path:
    normalize_db_engine(engine)
    return state_path / "secrets" / "db" / f"{domain}.txt"


def list_backups(backup_dir: Path) -> list[str]:
    if not backup_dir.exists():
        return []
    return sorted([item.name for item in backup_dir.glob("*.sql.gz") if item.is_file()])


def manifest_path(backup_file: Path) -> Path:
    return backup_file.with_name(f"{backup_file.name}.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_backup_manifest(*, backup_file: Path, domain: str, engine: str, database: str) -> Path:
    payload = {
        "domain": domain,
        "engine": normalize_db_engine(engine),
        "database": database,
        "backup_file": str(backup_file),
        "size_bytes": backup_file.stat().st_size,
        "sha256": _sha256_file(backup_file),
        "created_at": datetime.now(UTC).isoformat(),
    }
    path = manifest_path(backup_file)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_backup_manifest(backup_file: Path) -> dict | None:
    path = manifest_path(backup_file)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def latest_backup(backup_dir: Path) -> Path | None:
    backups = [backup_dir / name for name in list_backups(backup_dir)]
    if not backups:
        return None
    return backups[-1]


def prune_backups(*, backup_dir: Path, retain_count: int) -> list[str]:
    if retain_count < 1:
        return []
    backups = [backup_dir / name for name in list_backups(backup_dir)]
    if len(backups) <= retain_count:
        return []
    deleted: list[str] = []
    for backup in backups[:-retain_count]:
        manifest = manifest_path(backup)
        backup.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        deleted.append(backup.name)
    return deleted


def write_mysql_credentials(
    *,
    credential_file: Path,
    user: str,
    password: str,
    host: str,
    port: int,
) -> None:
    if not password:
        raise DbServiceError("Database password is empty.")
    credential_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "[client]",
            f"user={user}",
            f"password={password}",
            f"host={host}",
            f"port={port}",
            "",
        ]
    )
    fd = os.open(str(credential_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)


def _escape_pgpass_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def write_postgres_credentials(
    *,
    credential_file: Path,
    user: str,
    password: str,
    host: str,
    port: int,
) -> None:
    if not password:
        raise DbServiceError("Database password is empty.")
    if port < 1:
        raise DbServiceError("Database port must be >= 1.")
    credential_file.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{_escape_pgpass_value(host)}:"
        f"{port}:"
        f"*:"
        f"{_escape_pgpass_value(user)}:"
        f"{_escape_pgpass_value(password)}"
    )
    fd = os.open(str(credential_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def ensure_secure_credential_file(credential_file: Path) -> None:
    if not credential_file.exists():
        raise DbServiceError(f"Credential file not found: {credential_file}")
    mode = stat.S_IMODE(credential_file.stat().st_mode)
    if mode & 0o077:
        raise DbServiceError(
            f"Insecure credential file permissions ({oct(mode)}). Expected owner-only (0600)."
        )


def _validate_database_name(database: str) -> str:
    if not _DB_NAME_RE.fullmatch(database):
        raise DbServiceError("Invalid database name. Allowed pattern: [A-Za-z0-9_]+")
    return database


def normalize_database_name(name: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not candidate:
        raise DbServiceError("Unable to derive a database name from the provided value.")
    return _validate_database_name(candidate[:64])


def normalize_database_user(name: str, *, engine: str = "mysql") -> str:
    normalized_engine = normalize_db_engine(engine)
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not candidate:
        raise DbServiceError("Unable to derive a database user from the provided value.")
    limit = 32 if normalized_engine == "mysql" else 63
    user = candidate[:limit]
    if not _DB_USER_RE.fullmatch(user):
        raise DbServiceError("Invalid database user. Allowed pattern: [A-Za-z0-9_]+")
    return user


def generate_database_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(16, length)))


def write_password_secret(*, password_file: Path, password: str) -> None:
    if not password:
        raise DbServiceError("Database password is empty.")
    password_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(password_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{password}\n")


def _split_pgpass_line(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":" and len(parts) < 4:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    parts.append("".join(current))
    if len(parts) != 5:
        raise DbServiceError("Invalid PostgreSQL credential file format.")
    return parts


def _read_postgres_connection_info(*, credential_file: Path, database: str) -> tuple[str, str, str]:
    for raw_line in credential_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        host, port, database_pattern, user, _password = _split_pgpass_line(raw_line)
        if database_pattern not in {"*", database}:
            continue
        if not host or not user or not port:
            raise DbServiceError("PostgreSQL credential entry must include host, port, and user.")
        return host, port, user
    raise DbServiceError("No PostgreSQL credential entry found for requested database.")


def build_backup_command(
    *,
    backup_file: Path,
    database: str,
    credential_file: Path,
    engine: str = "mysql",
) -> list[str]:
    normalized_engine = normalize_db_engine(engine)
    ensure_secure_credential_file(credential_file)
    db_name = _validate_database_name(database)
    credential_file_q = shlex.quote(str(credential_file))
    backup_file_q = shlex.quote(str(backup_file))
    db_name_q = shlex.quote(db_name)

    if normalized_engine == "mysql":
        shell = (
            "set -euo pipefail; umask 077; "
            f"mysqldump --defaults-extra-file={credential_file_q} --databases {db_name_q} "
            f"| gzip > {backup_file_q}"
        )
    else:
        host, port, user = _read_postgres_connection_info(credential_file=credential_file, database=db_name)
        host_q = shlex.quote(host)
        port_q = shlex.quote(port)
        user_q = shlex.quote(user)
        shell = (
            "set -euo pipefail; umask 077; "
            f"PGPASSFILE={credential_file_q} pg_dump --clean --if-exists --no-owner --no-privileges "
            f"--no-password --host={host_q} --port={port_q} "
            f"--username={user_q} --dbname={db_name_q} | gzip > {backup_file_q}"
        )
    return ["bash", "-lc", shell]


def run_backup(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()


def build_restore_command(
    *,
    backup_file: Path,
    database: str,
    credential_file: Path,
    engine: str = "mysql",
) -> list[str]:
    normalized_engine = normalize_db_engine(engine)
    if not backup_file.exists():
        raise DbServiceError(f"Backup file not found: {backup_file}")
    ensure_secure_credential_file(credential_file)
    db_name = _validate_database_name(database)
    credential_file_q = shlex.quote(str(credential_file))
    backup_file_q = shlex.quote(str(backup_file))
    db_name_q = shlex.quote(db_name)

    if normalized_engine == "mysql":
        shell = (
            "set -euo pipefail; "
            f"gunzip -c {backup_file_q} | mysql --defaults-extra-file={credential_file_q} {db_name_q}"
        )
    else:
        host, port, user = _read_postgres_connection_info(credential_file=credential_file, database=db_name)
        host_q = shlex.quote(host)
        port_q = shlex.quote(port)
        user_q = shlex.quote(user)
        shell = (
            "set -euo pipefail; "
            f"gunzip -c {backup_file_q} | PGPASSFILE={credential_file_q} psql --no-password "
            f"--host={host_q} --port={port_q} --username={user_q} --dbname={db_name_q}"
        )
    return ["bash", "-lc", shell]


def run_restore(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()


def backup_status(*, backup_dir: Path, stale_hours: int) -> dict:
    latest = latest_backup(backup_dir)
    backups = list_backups(backup_dir)
    if latest is None:
        return {
            "status": "warn",
            "backup_dir": str(backup_dir),
            "count": 0,
            "latest_backup": None,
            "latest_manifest": None,
            "age_hours": None,
            "manifest_present": False,
        }

    age_hours = (datetime.now(UTC).timestamp() - latest.stat().st_mtime) / 3600
    manifest = read_backup_manifest(latest)
    status = "ok"
    if age_hours > max(1, stale_hours):
        status = "warn"
    if manifest is None:
        status = "warn"
    return {
        "status": status,
        "backup_dir": str(backup_dir),
        "count": len(backups),
        "latest_backup": str(latest),
        "latest_manifest": str(manifest_path(latest)) if manifest is not None else None,
        "age_hours": round(age_hours, 2),
        "manifest_present": manifest is not None,
        "latest_name": latest.name,
    }


def verify_backup(
    *,
    backup_file: Path,
    manifest_file: Path | None = None,
    check_gzip: bool = True,
    require_manifest: bool = False,
) -> dict:
    if not backup_file.exists():
        raise DbServiceError(f"Backup file not found: {backup_file}")

    resolved_manifest = manifest_file or manifest_path(backup_file)
    manifest_payload: dict | None = None
    if resolved_manifest.exists():
        try:
            manifest_payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DbServiceError(f"Invalid backup manifest: {resolved_manifest}") from exc
    elif require_manifest:
        raise DbServiceError(f"Backup manifest not found: {resolved_manifest}")

    actual_size = int(backup_file.stat().st_size)
    actual_sha256 = _sha256_file(backup_file)
    gzip_status = "skipped"
    if check_gzip:
        try:
            run_command(["gzip", "-t", str(backup_file)], check=True)
        except ShellCommandError as exc:
            raise DbServiceError(str(exc)) from exc
        gzip_status = "ok"

    sha256_match = None
    size_match = None
    if manifest_payload is not None:
        sha256_match = str(manifest_payload.get("sha256", "")) == actual_sha256
        try:
            size_match = int(manifest_payload.get("size_bytes", -1)) == actual_size
        except (TypeError, ValueError):
            size_match = False

    status = "ok"
    if manifest_payload is None:
        status = "warn"
    if sha256_match is False or size_match is False:
        status = "error"

    return {
        "status": status,
        "backup_file": str(backup_file),
        "manifest_file": str(resolved_manifest),
        "manifest_present": manifest_payload is not None,
        "size_bytes": actual_size,
        "sha256": actual_sha256,
        "sha256_match": sha256_match,
        "size_match": size_match,
        "gzip_check": gzip_status,
    }


def restore_verify_report_path(state_path: Path, domain: str) -> Path:
    return default_backup_dir(state_path, domain) / "last_restore_verify.json"


def write_restore_verify_report(*, state_path: Path, domain: str, payload: dict) -> Path:
    path = restore_verify_report_path(state_path, domain)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def failed_restore_verify_report(*, error: str, context: dict | None = None) -> dict:
    payload = {
        "status": "error",
        "verified_at": datetime.now(UTC).isoformat(),
        "error": error,
    }
    if context:
        payload.update(context)
    return payload


def _temporary_verify_database_name(database: str) -> str:
    safe_database = _validate_database_name(database)
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    candidate = f"{safe_database}_verify_{suffix}"
    return candidate[:63]


def _mysql_admin_command(*, credential_file: Path, sql: str, scalar: bool = False) -> list[str]:
    ensure_secure_credential_file(credential_file)
    credential_file_q = shlex.quote(str(credential_file))
    sql_q = shlex.quote(sql)
    flags = "-N -B " if scalar else ""
    return ["bash", "-lc", f"set -euo pipefail; mysql --defaults-extra-file={credential_file_q} {flags}-e {sql_q}"]


def _mysql_local_root_command(*, sql: str, scalar: bool = False) -> list[str]:
    sql_q = shlex.quote(sql)
    flags = "-N -B " if scalar else ""
    return ["bash", "-lc", f"set -euo pipefail; mysql {flags}-e {sql_q}"]


def _postgres_admin_command(*, credential_file: Path, database: str, sql: str) -> list[str]:
    ensure_secure_credential_file(credential_file)
    host, port, user = _read_postgres_connection_info(credential_file=credential_file, database=database)
    credential_file_q = shlex.quote(str(credential_file))
    host_q = shlex.quote(host)
    port_q = shlex.quote(port)
    user_q = shlex.quote(user)
    sql_q = shlex.quote(sql)
    return [
        "bash",
        "-lc",
        (
            "set -euo pipefail; "
            f"PGPASSFILE={credential_file_q} psql --no-password --host={host_q} --port={port_q} "
            f"--username={user_q} --dbname=postgres -c {sql_q}"
        ),
    ]


def _postgres_local_superuser_command(*, sql: str, scalar: bool = False) -> list[str]:
    sql_q = shlex.quote(sql)
    flags = "-Atc" if scalar else "-c"
    return ["bash", "-lc", f"set -euo pipefail; runuser -u postgres -- psql -d postgres {flags} {sql_q}"]


def _postgres_scalar_query(*, credential_file: Path, database: str, sql: str) -> int:
    ensure_secure_credential_file(credential_file)
    host, port, user = _read_postgres_connection_info(credential_file=credential_file, database=database)
    credential_file_q = shlex.quote(str(credential_file))
    host_q = shlex.quote(host)
    port_q = shlex.quote(port)
    user_q = shlex.quote(user)
    sql_q = shlex.quote(sql)
    command = [
        "bash",
        "-lc",
        (
            "set -euo pipefail; "
            f"PGPASSFILE={credential_file_q} psql --no-password --host={host_q} --port={port_q} "
            f"--username={user_q} --dbname=postgres -Atc {sql_q}"
        ),
    ]
    completed = run_command(command, check=True)
    raw = (completed.stdout or "").strip()
    return int(raw) if raw else 0


def _postgres_local_scalar_query(*, sql: str) -> int:
    command = _postgres_local_superuser_command(sql=sql, scalar=True)
    completed = run_command(command, check=True)
    raw = (completed.stdout or "").strip()
    return int(raw) if raw else 0


def _mysql_scalar_query(*, credential_file: Path, sql: str) -> int:
    command = _mysql_admin_command(credential_file=credential_file, sql=sql, scalar=True)
    completed = run_command(command, check=True)
    raw = (completed.stdout or "").strip()
    return int(raw) if raw else 0


def _mysql_local_scalar_query(*, sql: str) -> int:
    command = _mysql_local_root_command(sql=sql, scalar=True)
    completed = run_command(command, check=True)
    raw = (completed.stdout or "").strip()
    return int(raw) if raw else 0


def _mysql_command_for_admin(*, admin_credential_file: Path | None, sql: str) -> list[str]:
    if admin_credential_file is not None:
        return _mysql_admin_command(credential_file=admin_credential_file, sql=sql)
    return _mysql_local_root_command(sql=sql)


def _mysql_scalar_for_admin(*, admin_credential_file: Path | None, sql: str) -> int:
    if admin_credential_file is not None:
        return _mysql_scalar_query(credential_file=admin_credential_file, sql=sql)
    return _mysql_local_scalar_query(sql=sql)


def _postgres_command_for_admin(*, admin_credential_file: Path | None, database: str, sql: str) -> list[str]:
    if admin_credential_file is not None:
        return _postgres_admin_command(credential_file=admin_credential_file, database=database, sql=sql)
    return _postgres_local_superuser_command(sql=sql)


def _postgres_scalar_for_admin(*, admin_credential_file: Path | None, database: str, sql: str) -> int:
    if admin_credential_file is not None:
        return _postgres_scalar_query(credential_file=admin_credential_file, database=database, sql=sql)
    return _postgres_local_scalar_query(sql=sql)


def _sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _ensure_db_provision_binaries(*, engine: str, admin_credential_file: Path | None) -> None:
    normalized_engine = normalize_db_engine(engine)
    if normalized_engine == "mysql":
        if shutil.which("mysql") is None:
            raise DbServiceError(
                "MySQL client binary not found (`mysql`). Local DB provisioning requires the data stack. "
                "If you used `bootstrap init --profile small-vps`, rerun it with `--data --apply`, "
                "or skip `--with-db` and use an existing database."
            )
        return

    if shutil.which("psql") is None:
        raise DbServiceError(
            "PostgreSQL client binary not found (`psql`). Local DB provisioning requires the postgres stack "
            "or a host with PostgreSQL client tools installed."
        )
    if admin_credential_file is None and shutil.which("runuser") is None:
        raise DbServiceError(
            "Local PostgreSQL provisioning requires `runuser` when no admin credential file is supplied."
        )


def provision_database(
    *,
    engine: str,
    database: str,
    user: str,
    password: str,
    app_host: str,
    app_port: int,
    state_path: Path,
    domain: str,
    credential_file: Path | None = None,
    password_file: Path | None = None,
    admin_credential_file: Path | None = None,
) -> dict:
    normalized_engine = normalize_db_engine(engine)
    db_name = normalize_database_name(database)
    db_user = normalize_database_user(user, engine=normalized_engine)
    if not app_host.strip():
        raise DbServiceError("Application DB host cannot be empty.")
    if app_port < 1:
        raise DbServiceError("Application DB port must be >= 1.")
    if not password:
        raise DbServiceError("Database password is empty.")
    _ensure_db_provision_binaries(engine=normalized_engine, admin_credential_file=admin_credential_file)

    resolved_credential_file = credential_file or default_credential_file(state_path, domain, engine=normalized_engine)
    resolved_password_file = password_file or default_password_file(state_path, domain, engine=normalized_engine)

    try:
        if normalized_engine == "mysql":
            database_exists = _mysql_scalar_for_admin(
                admin_credential_file=admin_credential_file,
                sql=f"SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = '{db_name}';",
            )
            user_exists = _mysql_scalar_for_admin(
                admin_credential_file=admin_credential_file,
                sql=(
                    "SELECT COUNT(*) FROM mysql.user "
                    f"WHERE user = '{db_user}' AND host = '{_sql_string(app_host)}';"
                ),
            )
            if database_exists:
                raise DbServiceError(f"Database already exists: {db_name}")
            if user_exists:
                raise DbServiceError(f"Database user already exists: {db_user}@{app_host}")
            sql = (
                f"CREATE DATABASE `{db_name}`;"
                f" CREATE USER '{db_user}'@'{_sql_string(app_host)}' IDENTIFIED BY '{_sql_string(password)}';"
                f" GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'{_sql_string(app_host)}';"
                " FLUSH PRIVILEGES;"
            )
            run_command(_mysql_command_for_admin(admin_credential_file=admin_credential_file, sql=sql), check=True)
            write_mysql_credentials(
                credential_file=resolved_credential_file,
                user=db_user,
                password=password,
                host=app_host,
                port=app_port,
            )
        else:
            database_exists = _postgres_scalar_for_admin(
                admin_credential_file=admin_credential_file,
                database=db_name,
                sql=f"SELECT COUNT(*) FROM pg_database WHERE datname = '{db_name}';",
            )
            user_exists = _postgres_scalar_for_admin(
                admin_credential_file=admin_credential_file,
                database=db_name,
                sql=f"SELECT COUNT(*) FROM pg_roles WHERE rolname = '{db_user}';",
            )
            if database_exists:
                raise DbServiceError(f"Database already exists: {db_name}")
            if user_exists:
                raise DbServiceError(f"Database user already exists: {db_user}")
            run_command(
                _postgres_command_for_admin(
                    admin_credential_file=admin_credential_file,
                    database=db_name,
                    sql=f"CREATE ROLE \"{db_user}\" LOGIN PASSWORD '{_sql_string(password)}';",
                ),
                check=True,
            )
            run_command(
                _postgres_command_for_admin(
                    admin_credential_file=admin_credential_file,
                    database=db_name,
                    sql=f"CREATE DATABASE \"{db_name}\" OWNER \"{db_user}\";",
                ),
                check=True,
            )
            write_postgres_credentials(
                credential_file=resolved_credential_file,
                user=db_user,
                password=password,
                host=app_host,
                port=app_port,
            )
    except ShellCommandError as exc:
        raise DbServiceError(str(exc)) from exc

    write_password_secret(password_file=resolved_password_file, password=password)
    return {
        "status": "ok",
        "domain": domain,
        "engine": normalized_engine,
        "database": db_name,
        "user": db_user,
        "host": app_host,
        "port": app_port,
        "credential_file": str(resolved_credential_file),
        "password_file": str(resolved_password_file),
        "admin_credential_file": str(admin_credential_file) if admin_credential_file is not None else None,
        "provisioned_at": datetime.now(UTC).isoformat(),
    }


def deprovision_database(
    *,
    engine: str,
    database: str,
    user: str,
    app_host: str,
    admin_credential_file: Path | None = None,
    drop_password_file: Path | None = None,
    drop_credential_file: Path | None = None,
) -> dict:
    normalized_engine = normalize_db_engine(engine)
    db_name = normalize_database_name(database)
    db_user = normalize_database_user(user, engine=normalized_engine)
    removed_files: list[str] = []

    try:
        if normalized_engine == "mysql":
            sql = (
                f"DROP DATABASE IF EXISTS `{db_name}`;"
                f" DROP USER IF EXISTS '{db_user}'@'{_sql_string(app_host)}';"
                " FLUSH PRIVILEGES;"
            )
            run_command(_mysql_command_for_admin(admin_credential_file=admin_credential_file, sql=sql), check=True)
        else:
            run_command(
                _postgres_command_for_admin(
                    admin_credential_file=admin_credential_file,
                    database=db_name,
                    sql=f'DROP DATABASE IF EXISTS "{db_name}";',
                ),
                check=False,
            )
            run_command(
                _postgres_command_for_admin(
                    admin_credential_file=admin_credential_file,
                    database=db_name,
                    sql=f'DROP ROLE IF EXISTS "{db_user}";',
                ),
                check=False,
            )
    except ShellCommandError as exc:
        raise DbServiceError(str(exc)) from exc

    for path in [drop_password_file, drop_credential_file]:
        if path is not None and path.exists():
            path.unlink()
            removed_files.append(str(path))

    return {
        "status": "ok",
        "engine": normalized_engine,
        "database": db_name,
        "user": db_user,
        "host": app_host,
        "removed_files": removed_files,
    }


def restore_verify_backup(
    *,
    backup_file: Path,
    database: str,
    credential_file: Path,
    engine: str = "mysql",
    verify_database: str | None = None,
) -> dict:
    normalized_engine = normalize_db_engine(engine)
    temp_database = _validate_database_name(verify_database or _temporary_verify_database_name(database))
    restore_command = build_restore_command(
        backup_file=backup_file,
        database=temp_database,
        credential_file=credential_file,
        engine=normalized_engine,
    )

    if normalized_engine == "mysql":
        create_command = _mysql_admin_command(credential_file=credential_file, sql=f"CREATE DATABASE `{temp_database}`;")
        drop_command = _mysql_admin_command(
            credential_file=credential_file,
            sql=f"DROP DATABASE IF EXISTS `{temp_database}`;",
        )
        count_sql = (
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema = '{temp_database}';"
        )
    else:
        create_command = _postgres_admin_command(
            credential_file=credential_file,
            database=database,
            sql=f'CREATE DATABASE "{temp_database}";',
        )
        drop_command = _postgres_admin_command(
            credential_file=credential_file,
            database=database,
            sql=f'DROP DATABASE IF EXISTS "{temp_database}";',
        )
        count_sql = (
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_catalog = '{temp_database}' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema');"
        )

    created = False
    try:
        run_command(create_command, check=True)
        created = True
        run_restore(restore_command)
        if normalized_engine == "mysql":
            table_count = _mysql_scalar_query(credential_file=credential_file, sql=count_sql)
        else:
            table_count = _postgres_scalar_query(credential_file=credential_file, database=database, sql=count_sql)
    except ShellCommandError as exc:
        raise DbServiceError(str(exc)) from exc
    finally:
        if created:
            run_command(drop_command, check=False)

    return {
        "status": "ok",
        "engine": normalized_engine,
        "backup_file": str(backup_file),
        "verify_database": temp_database,
        "table_count": table_count,
        "verified_at": datetime.now(UTC).isoformat(),
    }
