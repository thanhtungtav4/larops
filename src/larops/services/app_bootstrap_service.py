from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from larops.services.db_service import DbServiceError, count_database_tables
from larops.services.env_file_service import database_env_updates, upsert_env_values


class AppBootstrapServiceError(RuntimeError):
    pass


_APP_BOOTSTRAP_MODES = {"auto", "eager", "skip"}


def shared_env_has_app_key(env_file: Path) -> bool:
    if not env_file.exists():
        return False
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith("APP_KEY="):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if value:
            return True
    return False


def generate_app_key() -> str:
    return "base64:" + base64.b64encode(os.urandom(32)).decode("ascii")


def ensure_shared_app_key(env_file: Path) -> dict[str, Any] | None:
    if shared_env_has_app_key(env_file):
        return None
    return upsert_env_values(env_file=env_file, updates={"APP_KEY": generate_app_key()})


def normalize_app_bootstrap_mode(raw_mode: str) -> str:
    mode = raw_mode.strip().lower()
    if mode not in _APP_BOOTSTRAP_MODES:
        raise AppBootstrapServiceError(
            f"Unsupported app bootstrap mode: {raw_mode}. Expected one of: auto, eager, skip."
        )
    return mode


def resolve_app_bootstrap_strategy(
    *,
    requested_mode: str,
    current_path: Path,
    database_provision: dict[str, Any] | None,
) -> dict[str, Any]:
    mode = normalize_app_bootstrap_mode(requested_mode)
    if not (current_path / "artisan").exists():
        return {"mode": "skip", "reason": "no-artisan", "table_count": None}
    if mode in {"eager", "skip"}:
        return {"mode": mode, "reason": f"explicit-{mode}", "table_count": None}
    if database_provision is None:
        return {"mode": "skip", "reason": "no-db-context", "table_count": None}

    credential_file_raw = str(database_provision.get("credential_file", "")).strip()
    database = str(database_provision.get("database", "")).strip()
    engine = str(database_provision.get("engine", "")).strip()
    if not credential_file_raw or not database or not engine:
        return {"mode": "skip", "reason": "missing-db-inspection-context", "table_count": None}

    try:
        table_count = count_database_tables(
            engine=engine,
            database=database,
            credential_file=Path(credential_file_raw),
        )
    except DbServiceError:
        return {"mode": "skip", "reason": "db-inspection-failed", "table_count": None}

    if table_count > 0:
        return {"mode": "eager", "reason": "database-has-tables", "table_count": table_count}
    return {"mode": "skip", "reason": "database-empty", "table_count": 0}


def resolve_bootstrap_app_commands(
    *,
    current_path: Path,
    shared_env_file: Path,
    bootstrap_mode: str,
    seed: bool = False,
    seeder_class: str | None = None,
    skip_migrate: bool = False,
    skip_package_discover: bool = False,
    skip_optimize: bool = False,
) -> list[str]:
    if not (current_path / "artisan").exists():
        return []
    if bootstrap_mode != "eager":
        return []

    commands: list[str] = []
    if not shared_env_has_app_key(shared_env_file):
        commands.append("php artisan key:generate --force")
    if not skip_migrate:
        commands.append("php artisan migrate --force")
    if not skip_package_discover:
        commands.append("php artisan package:discover --ansi")
    if seed:
        seed_command = "php artisan db:seed --force"
        if seeder_class:
            seed_command += f" --class={seeder_class}"
        commands.append(seed_command)
    if not skip_optimize:
        commands.extend(["php artisan optimize:clear", "php artisan optimize"])
    return commands


def sync_env_from_database_provision(*, shared_env_file: Path, database_provision: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    password_file_raw = str(database_provision.get("password_file", "")).strip()
    if not password_file_raw:
        return None
    password_file = Path(password_file_raw)
    if not password_file.exists():
        return None
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        return None
    env_sync = upsert_env_values(
        env_file=shared_env_file,
        updates=database_env_updates(
            engine=str(database_provision["engine"]),
            host=str(database_provision["host"]),
            port=int(database_provision["port"]),
            database=str(database_provision["database"]),
            user=str(database_provision["user"]),
            password=password,
        ),
    )
    return env_sync, password
