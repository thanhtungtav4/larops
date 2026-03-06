from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from larops.services.security_service import SUSPICIOUS_HTTP_STATUSES, SUSPICIOUS_PATH_PATTERNS


class MonitorScanError(RuntimeError):
    pass


def _parse_nginx_line(line: str) -> tuple[str | None, str | None, str | None]:
    matched = re.search(r'^(\S+) .* "(?:GET|POST|HEAD|OPTIONS|PUT|DELETE|PATCH) (\S+) HTTP/\d\.\d" (\d{3}) ', line)
    if not matched:
        return None, None, None
    return matched.group(1), matched.group(2), matched.group(3)


def _is_suspicious(path: str) -> bool:
    normalized = path.lower()
    return any(pattern in normalized for pattern in SUSPICIOUS_PATH_PATTERNS)


def _load_state(path: Path) -> dict[str, Any]:
    default = {"offset": 0, "inode": None, "updated_at": None, "recent_hits": {}}
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    if not isinstance(payload, dict):
        return default
    recent_hits = payload.get("recent_hits")
    if not isinstance(recent_hits, dict):
        recent_hits = {}
    return {
        "offset": int(payload.get("offset") or 0),
        "inode": payload.get("inode"),
        "updated_at": payload.get("updated_at"),
        "recent_hits": recent_hits,
    }


def _parse_nginx_timestamp(line: str) -> datetime | None:
    matched = re.search(r"\[(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]", line)
    if not matched:
        return None
    try:
        return datetime.strptime(matched.group(1), "%d/%b/%Y:%H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None


def _prune_recent_hits(raw_recent_hits: dict[str, Any], *, cutoff: datetime) -> dict[str, list[str]]:
    recent_hits: dict[str, list[str]] = {}
    for ip, values in raw_recent_hits.items():
        if not isinstance(ip, str) or not isinstance(values, list):
            continue
        kept: list[str] = []
        for raw_value in values:
            if not isinstance(raw_value, str):
                continue
            try:
                parsed = datetime.fromisoformat(raw_value)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
            if parsed >= cutoff:
                kept.append(parsed.isoformat())
        if kept:
            recent_hits[ip] = kept
    return recent_hits


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def scan_nginx_incremental(
    *,
    log_path: Path,
    state_path: Path,
    threshold_hits: int,
    window_seconds: int,
    max_lines: int,
    top: int,
) -> dict[str, Any]:
    if threshold_hits < 1:
        raise MonitorScanError("threshold_hits must be >= 1.")
    if window_seconds < 1:
        raise MonitorScanError("window_seconds must be >= 1.")
    if max_lines < 1:
        raise MonitorScanError("max_lines must be >= 1.")
    if top < 1:
        raise MonitorScanError("top must be >= 1.")

    state = _load_state(state_path)
    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(seconds=window_seconds)
    recent_hits = _prune_recent_hits(state.get("recent_hits", {}), cutoff=cutoff)
    if not log_path.exists():
        state["updated_at"] = now_utc.isoformat()
        state["recent_hits"] = recent_hits
        _save_state(state_path, state)
        return {
            "log_path": str(log_path),
            "state_path": str(state_path),
            "window_seconds": window_seconds,
            "window_cutoff_utc": cutoff.isoformat(),
            "lines_read": 0,
            "suspicious_total": 0,
            "alerts": [],
            "top_paths": [],
            "top_ips": [],
            "state": state,
        }

    stat = log_path.stat()
    current_inode = f"{stat.st_dev}:{stat.st_ino}"
    offset = int(state.get("offset") or 0)
    previous_inode = state.get("inode")
    if previous_inode != current_inode or offset > stat.st_size or offset < 0:
        offset = 0

    lines: list[str] = []
    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(offset)
        while len(lines) < max_lines:
            line = handle.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
        end_offset = handle.tell()

    suspicious_by_ip: Counter[str] = Counter()
    suspicious_by_path: Counter[str] = Counter()
    suspicious_total = 0
    for line in lines:
        ip, path, status = _parse_nginx_line(line)
        if not ip or not path or not status:
            continue
        if status not in SUSPICIOUS_HTTP_STATUSES:
            continue
        if not _is_suspicious(path):
            continue
        timestamp = _parse_nginx_timestamp(line) or now_utc
        if timestamp < cutoff:
            continue
        suspicious_total += 1
        suspicious_by_ip.update([ip])
        suspicious_by_path.update([path])
        recent_hits.setdefault(ip, []).append(timestamp.isoformat())

    alerts = [
        {
            "ip": ip,
            "hits": len(recent_hits.get(ip, [])),
            "threshold": threshold_hits,
            "window_seconds": window_seconds,
        }
        for ip in suspicious_by_ip
        if len(recent_hits.get(ip, [])) >= threshold_hits
    ]
    alerts.sort(key=lambda item: (-int(item["hits"]), str(item["ip"])))

    state = {
        "offset": end_offset,
        "inode": current_inode,
        "updated_at": now_utc.isoformat(),
        "recent_hits": _prune_recent_hits(recent_hits, cutoff=cutoff),
    }
    _save_state(state_path, state)

    return {
        "log_path": str(log_path),
        "state_path": str(state_path),
        "window_seconds": window_seconds,
        "window_cutoff_utc": cutoff.isoformat(),
        "lines_read": len(lines),
        "suspicious_total": suspicious_total,
        "alerts": alerts,
        "top_paths": [{"path": path, "count": count} for path, count in suspicious_by_path.most_common(top)],
        "top_ips": [{"ip": ip, "count": count} for ip, count in suspicious_by_ip.most_common(top)],
        "state": state,
    }
