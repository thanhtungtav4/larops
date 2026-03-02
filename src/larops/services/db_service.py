from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import run_command


class DbServiceError(RuntimeError):
    pass


def backup_filename(domain: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_domain = domain.replace(".", "_")
    return f"{safe_domain}_{stamp}.sql.gz"


def default_backup_dir(state_path: Path, domain: str) -> Path:
    return state_path / "backups" / domain


def list_backups(backup_dir: Path) -> list[str]:
    if not backup_dir.exists():
        return []
    return sorted([item.name for item in backup_dir.glob("*.sql.gz") if item.is_file()])


def build_backup_command(
    *,
    backup_file: Path,
    database: str,
    user: str,
    password: str,
    host: str,
    port: int,
) -> list[str]:
    shell = (
        "set -euo pipefail; "
        f"MYSQL_PWD='{password}' "
        f"mysqldump --host={host} --port={port} --user={user} --databases {database} "
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
    user: str,
    password: str,
    host: str,
    port: int,
) -> list[str]:
    if not backup_file.exists():
        raise DbServiceError(f"Backup file not found: {backup_file}")
    shell = (
        "set -euo pipefail; "
        f"MYSQL_PWD='{password}' "
        f"gunzip -c '{backup_file}' | mysql --host={host} --port={port} --user={user} {database}"
    )
    return ["bash", "-lc", shell]


def run_restore(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()

