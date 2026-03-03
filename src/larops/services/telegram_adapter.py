from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class TelegramAdapterError(RuntimeError):
    pass


SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2, "critical": 3}


def _normalize_severity(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized == "warning":
        return "warn"
    return normalized


@dataclass(slots=True)
class TelegramAdapterConfig:
    events_path: Path
    state_file: Path
    bot_token: str
    chat_id: str
    min_severity: str = "error"
    batch_size: int = 20


def _default_state() -> dict:
    return {"offset": 0, "sent_ids": []}


def load_state(path: Path) -> dict:
    if not path.exists():
        return _default_state()
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["sent_ids"] = state.get("sent_ids", [])[-1000:]
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _severity_allowed(event: dict, min_severity: str) -> bool:
    event_severity = _normalize_severity(str(event.get("severity", "info")))
    normalized_min = _normalize_severity(min_severity)
    return SEVERITY_ORDER.get(event_severity, 0) >= SEVERITY_ORDER.get(normalized_min, 2)


def _format_message(event: dict) -> str:
    severity = _normalize_severity(str(event.get("severity", "info"))).upper()
    event_type = str(event.get("event_type", "unknown"))
    host = str(event.get("host", "unknown-host"))
    app = event.get("app")
    message = str(event.get("message", "No message"))
    stamp = str(event.get("timestamp", ""))
    metadata = event.get("metadata", {})
    lines = [
        f"[{severity}] {event_type}",
        f"host: {host}",
    ]
    if app:
        lines.append(f"app: {app}")
    if stamp:
        lines.append(f"time: {stamp}")
    lines.append(f"message: {message}")
    if metadata:
        lines.append("metadata: " + json.dumps(metadata, ensure_ascii=True))
    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        raise TelegramAdapterError("Telegram bot token or chat_id is missing.")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise TelegramAdapterError(f"Telegram API returned status {response.status}")
        body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise TelegramAdapterError(f"Telegram API error: {body}")


def _read_events(events_path: Path, start_offset: int, batch_size: int) -> tuple[list[dict], int]:
    if not events_path.exists():
        return [], start_offset

    with events_path.open("r", encoding="utf-8") as handle:
        handle.seek(start_offset)
        events: list[dict] = []
        while len(events) < batch_size:
            line = handle.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
        end_offset = handle.tell()
    return events, end_offset


def dispatch_once(
    config: TelegramAdapterConfig,
    *,
    sender: Callable[[str, str, str], None] = send_telegram_message,
    apply: bool,
) -> dict:
    state = load_state(config.state_file)
    offset = int(state.get("offset", 0))
    sent_ids = list(state.get("sent_ids", []))
    sent_set = set(sent_ids)

    events, end_offset = _read_events(config.events_path, offset, max(1, config.batch_size))
    considered = 0
    delivered = 0
    skipped = 0

    for event in events:
        considered += 1
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in sent_set:
            skipped += 1
            continue
        if not _severity_allowed(event, config.min_severity):
            skipped += 1
            continue
        text = _format_message(event)
        if apply:
            sender(config.bot_token, config.chat_id, text)
        sent_ids.append(event_id)
        sent_set.add(event_id)
        delivered += 1

    state["offset"] = end_offset
    state["sent_ids"] = sent_ids[-1000:]
    save_state(config.state_file, state)
    return {
        "considered": considered,
        "delivered": delivered,
        "skipped": skipped,
        "offset": end_offset,
    }


def watch(
    config: TelegramAdapterConfig,
    *,
    sender: Callable[[str, str, str], None] = send_telegram_message,
    apply: bool,
    interval_seconds: int,
    iterations: int,
) -> list[dict]:
    reports: list[dict] = []
    loops = 0
    while True:
        report = dispatch_once(config, sender=sender, apply=apply)
        reports.append(report)
        loops += 1
        if iterations > 0 and loops >= iterations:
            break
        time.sleep(max(1, interval_seconds))
    return reports
