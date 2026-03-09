from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path


class EnvFileServiceError(RuntimeError):
    pass


_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _format_env_value(value: str) -> str:
    if value == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def upsert_env_values(*, env_file: Path, updates: dict[str, str]) -> dict[str, object]:
    if not updates:
        raise EnvFileServiceError("No env updates provided.")

    env_file.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    remaining = dict(updates)
    rendered_lines: list[str] = []

    for line in existing_lines:
        match = _ENV_LINE_RE.match(line)
        if not match:
            rendered_lines.append(line)
            continue
        key = match.group(1)
        if key in remaining:
            rendered_lines.append(f"{key}={_format_env_value(str(remaining.pop(key)))}")
        else:
            rendered_lines.append(line)

    for key, value in remaining.items():
        rendered_lines.append(f"{key}={_format_env_value(str(value))}")

    body = "\n".join(rendered_lines).rstrip("\n") + "\n"
    env_file.write_text(body, encoding="utf-8")
    return {
        "status": "ok",
        "env_file": str(env_file),
        "updated_keys": list(updates.keys()),
        "synced_at": datetime.now(UTC).isoformat(),
    }


def database_env_updates(*, engine: str, host: str, port: int, database: str, user: str, password: str) -> dict[str, str]:
    connection = "pgsql" if engine.strip().lower() in {"postgres", "postgresql", "pgsql"} else "mysql"
    return {
        "DB_CONNECTION": connection,
        "DB_HOST": host,
        "DB_PORT": str(port),
        "DB_DATABASE": database,
        "DB_USERNAME": user,
        "DB_PASSWORD": password,
    }
