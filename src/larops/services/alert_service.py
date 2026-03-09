from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from larops.core.shell import run_command
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux


class AlertServiceError(RuntimeError):
    pass


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.strip(), encoding="utf-8")
    os.chmod(path, 0o600)


def _relabel_managed_etc_paths(paths: list[Path]) -> None:
    try:
        relabel_managed_paths_for_selinux(
            paths,
            run_command=run_command,
            which=shutil.which,
            roots=[Path("/etc")],
        )
    except SelinuxServiceError as exc:
        raise AlertServiceError(str(exc)) from exc


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AlertServiceError(f"Config file must be a YAML object: {path}")
    return raw


def _upsert(payload: dict[str, Any], path: list[str], value: Any) -> None:
    node = payload
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def configure_telegram_alert(
    *,
    config_path: Path,
    telegram_token: str | None,
    telegram_chat_id: str | None,
    telegram_token_file: Path,
    telegram_chat_id_file: Path,
    enabled: bool,
) -> dict[str, Any]:
    if enabled:
        if telegram_token is None and not telegram_token_file.exists():
            raise AlertServiceError(
                "Telegram token is missing. Provide --telegram-token or create --telegram-token-file first."
            )
        if telegram_chat_id is None and not telegram_chat_id_file.exists():
            raise AlertServiceError(
                "Telegram chat id is missing. Provide --telegram-chat-id or create --telegram-chat-id-file first."
            )

    if telegram_token is not None:
        _write_secret(telegram_token_file, telegram_token)
    if telegram_chat_id is not None:
        _write_secret(telegram_chat_id_file, telegram_chat_id)

    payload = _load_yaml(config_path)
    _upsert(payload, ["notifications", "telegram", "enabled"], enabled)
    _upsert(payload, ["notifications", "telegram", "bot_token"], "")
    _upsert(payload, ["notifications", "telegram", "chat_id"], "")
    _upsert(payload, ["notifications", "telegram", "bot_token_file"], str(telegram_token_file))
    _upsert(payload, ["notifications", "telegram", "chat_id_file"], str(telegram_chat_id_file))
    if payload.get("events") is None:
        payload["events"] = {"sink": "jsonl", "path": "/var/log/larops/events.jsonl"}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    _relabel_managed_etc_paths([telegram_token_file, telegram_chat_id_file, config_path])

    return {
        "config_path": str(config_path),
        "enabled": enabled,
        "telegram_token_file": str(telegram_token_file),
        "telegram_chat_id_file": str(telegram_chat_id_file),
    }
