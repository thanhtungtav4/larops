from __future__ import annotations

import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import run_command


class DbServiceError(RuntimeError):
    pass


_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def backup_filename(domain: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_domain = domain.replace(".", "_")
    return f"{safe_domain}_{stamp}.sql.gz"


def default_backup_dir(state_path: Path, domain: str) -> Path:
    return state_path / "backups" / domain


def default_credential_file(state_path: Path, domain: str) -> Path:
    return state_path / "secrets" / "db" / f"{domain}.cnf"


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


def build_backup_command(
    *,
    backup_file: Path,
    database: str,
    credential_file: Path,
) -> list[str]:
    ensure_secure_credential_file(credential_file)
    db_name = _validate_database_name(database)
    shell = (
        "set -euo pipefail; "
        f"mysqldump --defaults-extra-file='{credential_file}' --databases {db_name} "
        f"| gzip > '{backup_file}'"
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
) -> list[str]:
    if not backup_file.exists():
        raise DbServiceError(f"Backup file not found: {backup_file}")
    ensure_secure_credential_file(credential_file)
    db_name = _validate_database_name(database)
    shell = (
        "set -euo pipefail; "
        f"gunzip -c '{backup_file}' | mysql --defaults-extra-file='{credential_file}' {db_name}"
    )
    return ["bash", "-lc", shell]


def run_restore(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()
