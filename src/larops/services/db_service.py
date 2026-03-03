from __future__ import annotations

import os
import re
import shlex
import stat
from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import run_command


class DbServiceError(RuntimeError):
    pass


_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
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


def list_backups(backup_dir: Path) -> list[str]:
    if not backup_dir.exists():
        return []
    return sorted([item.name for item in backup_dir.glob("*.sql.gz") if item.is_file()])


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
