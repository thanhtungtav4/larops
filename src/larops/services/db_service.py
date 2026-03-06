from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import ShellCommandError, run_command


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
