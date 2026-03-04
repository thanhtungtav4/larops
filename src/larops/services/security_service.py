from __future__ import annotations

import re
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from larops.core.shell import run_command


SUSPICIOUS_PATH_PATTERNS = (
    "/.env",
    "/.git",
    "/.svn",
    "/wp-login.php",
    "/xmlrpc.php",
    "/phpmyadmin",
    "/?xdebug_session_start=",
    "../",
)

SUSPICIOUS_HTTP_STATUSES = {"403", "404", "444"}


class SecurityReportError(RuntimeError):
    pass


@dataclass(slots=True)
class SecurityInstallPlan:
    ufw_commands: list[list[str]]
    fail2ban_jail_path: Path
    fail2ban_filter_path: Path
    fail2ban_jail_body: str
    fail2ban_filter_body: str
    fail2ban_service: str = "fail2ban"


def _normalize_ufw_logging(level: str) -> str:
    normalized = level.strip().lower()
    if normalized in {"off", "on", "low", "medium", "high", "full"}:
        return normalized
    return "low"


def render_fail2ban_jail(*, ssh_port: int, nginx_log_path: Path, fail2ban_log_path: Path) -> str:
    return "\n".join(
        [
            "[DEFAULT]",
            "banaction = ufw",
            "findtime = 10m",
            "maxretry = 5",
            "bantime = 1h",
            f"logtarget = {fail2ban_log_path}",
            "",
            "[sshd]",
            "enabled = true",
            "backend = systemd",
            f"port = {ssh_port}",
            "",
            "[larops-nginx-scan]",
            "enabled = true",
            "backend = auto",
            "port = http,https",
            "filter = larops-nginx-scan",
            f"logpath = {nginx_log_path}",
            "maxretry = 8",
            "findtime = 5m",
            "bantime = 1h",
            "",
        ]
    )


def render_fail2ban_filter() -> str:
    # Match probes for sensitive files/endpoints and traversal attempts.
    failregex = (
        r'^<HOST> - .* "(GET|POST|HEAD|OPTIONS) '
        r'(\S*\.env\S*|\S*\.git\S*|\S*\.svn\S*|\S*wp-login\.php\S*|'
        r'\S*xmlrpc\.php\S*|\S*phpmyadmin\S*|\S*XDEBUG_SESSION_START=\S*|\S*\.\./\S*) '
        r'HTTP/\d\.\d" (403|404|444) .*'
    )
    return "\n".join(
        [
            "[Definition]",
            f"failregex = {failregex}",
            "ignoreregex =",
            "",
        ]
    )


def build_security_install_plan(
    *,
    ssh_port: int,
    limit_ssh: bool,
    ufw_logging: str,
    fail2ban_jail_path: Path,
    fail2ban_filter_path: Path,
    nginx_log_path: Path,
    fail2ban_log_path: Path,
) -> SecurityInstallPlan:
    commands: list[list[str]] = [
        ["ufw", "allow", f"{ssh_port}/tcp"],
        ["ufw", "allow", "80/tcp"],
        ["ufw", "allow", "443/tcp"],
    ]
    if limit_ssh:
        commands.append(["ufw", "limit", f"{ssh_port}/tcp"])

    normalized_logging = _normalize_ufw_logging(ufw_logging)
    if normalized_logging != "off":
        commands.append(["ufw", "logging", normalized_logging])
    else:
        commands.append(["ufw", "logging", "off"])
    commands.append(["ufw", "--force", "enable"])

    return SecurityInstallPlan(
        ufw_commands=commands,
        fail2ban_jail_path=fail2ban_jail_path,
        fail2ban_filter_path=fail2ban_filter_path,
        fail2ban_jail_body=render_fail2ban_jail(
            ssh_port=ssh_port,
            nginx_log_path=nginx_log_path,
            fail2ban_log_path=fail2ban_log_path,
        ),
        fail2ban_filter_body=render_fail2ban_filter(),
    )


def apply_security_install_plan(plan: SecurityInstallPlan) -> dict[str, Any]:
    plan.fail2ban_jail_path.parent.mkdir(parents=True, exist_ok=True)
    plan.fail2ban_filter_path.parent.mkdir(parents=True, exist_ok=True)
    plan.fail2ban_jail_path.write_text(plan.fail2ban_jail_body, encoding="utf-8")
    plan.fail2ban_filter_path.write_text(plan.fail2ban_filter_body, encoding="utf-8")

    for command in plan.ufw_commands:
        run_command(command, check=True)

    run_command(["systemctl", "enable", "--now", plan.fail2ban_service], check=True)
    run_command(["systemctl", "restart", plan.fail2ban_service], check=True)
    return {
        "ufw_commands_executed": plan.ufw_commands,
        "fail2ban_jail_path": str(plan.fail2ban_jail_path),
        "fail2ban_filter_path": str(plan.fail2ban_filter_path),
        "fail2ban_service": plan.fail2ban_service,
    }


def collect_security_status(
    *,
    fail2ban_jail_path: Path,
    fail2ban_filter_path: Path,
) -> dict[str, Any]:
    ufw_status = run_command(["ufw", "status"], check=False)
    fail2ban_status = run_command(["fail2ban-client", "status"], check=False)
    sshd_status = run_command(["fail2ban-client", "status", "sshd"], check=False)
    nginx_scan_status = run_command(["fail2ban-client", "status", "larops-nginx-scan"], check=False)

    def _output(raw) -> str:
        return (raw.stdout or raw.stderr or "").strip()

    return {
        "ufw": {"raw": _output(ufw_status), "exit_code": ufw_status.returncode},
        "fail2ban": {"raw": _output(fail2ban_status), "exit_code": fail2ban_status.returncode},
        "jails": {
            "sshd": {"raw": _output(sshd_status), "exit_code": sshd_status.returncode},
            "larops-nginx-scan": {
                "raw": _output(nginx_scan_status),
                "exit_code": nginx_scan_status.returncode,
            },
        },
        "files": {
            "jail": {"path": str(fail2ban_jail_path), "exists": fail2ban_jail_path.exists()},
            "filter": {"path": str(fail2ban_filter_path), "exists": fail2ban_filter_path.exists()},
        },
    }


def _tail_lines(path: Path, *, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return list(deque(handle, maxlen=max(1, max_lines)))


def _extract_nginx_line(line: str) -> tuple[str | None, str | None, str | None]:
    # Common log format: IP - - [time] "METHOD /path HTTP/1.1" status ...
    matched = re.search(r'^(\S+) .* "(?:GET|POST|HEAD|OPTIONS|PUT|DELETE|PATCH) (\S+) HTTP/\d\.\d" (\d{3}) ', line)
    if not matched:
        return None, None, None
    return matched.group(1), matched.group(2), matched.group(3)


def _extract_nginx_timestamp(line: str) -> datetime | None:
    matched = re.search(r"\[(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]", line)
    if not matched:
        return None
    try:
        return datetime.strptime(matched.group(1), "%d/%b/%Y:%H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None


def _extract_fail2ban_timestamp(line: str) -> datetime | None:
    matched = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if not matched:
        return None
    try:
        return datetime.strptime(matched.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_since_window(since: str | None) -> tuple[str, timedelta | None]:
    if since is None or not since.strip():
        return "all", None
    raw = since.strip().lower()
    matched = re.fullmatch(r"(\d+)\s*([smhdw])", raw)
    if not matched:
        raise SecurityReportError("Invalid --since format. Use values like 15m, 6h, 2d, 1w.")
    amount = int(matched.group(1))
    unit = matched.group(2)
    if amount < 1:
        raise SecurityReportError("--since value must be >= 1.")
    seconds_map = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return raw, timedelta(seconds=amount * seconds_map[unit])


def _is_suspicious_path(path: str) -> bool:
    normalized = path.lower()
    return any(pattern in normalized for pattern in SUSPICIOUS_PATH_PATTERNS)


def build_security_report(
    *,
    fail2ban_log_path: Path,
    nginx_log_path: Path,
    max_lines: int,
    top: int,
    since: str | None = None,
) -> dict[str, Any]:
    since_label, window = _parse_since_window(since)
    now_utc = datetime.now(UTC)
    cutoff = now_utc - window if window is not None else None

    ban_counter: Counter[str] = Counter()
    fail2ban_lines = _tail_lines(fail2ban_log_path, max_lines=max_lines)
    fail2ban_in_window = 0
    for line in fail2ban_lines:
        if cutoff is not None:
            timestamp = _extract_fail2ban_timestamp(line)
            if timestamp is None or timestamp < cutoff:
                continue
        fail2ban_in_window += 1
        matched = re.search(r"\bBan\s+(\S+)\b", line)
        if matched:
            ban_counter.update([matched.group(1)])

    path_counter: Counter[str] = Counter()
    ip_counter: Counter[str] = Counter()
    suspicious_total = 0
    nginx_lines = _tail_lines(nginx_log_path, max_lines=max_lines)
    nginx_in_window = 0
    for line in nginx_lines:
        if cutoff is not None:
            timestamp = _extract_nginx_timestamp(line)
            if timestamp is None or timestamp < cutoff:
                continue
        nginx_in_window += 1
        ip, path, status = _extract_nginx_line(line)
        if not ip or not path or not status:
            continue
        if status not in SUSPICIOUS_HTTP_STATUSES:
            continue
        if not _is_suspicious_path(path):
            continue
        suspicious_total += 1
        path_counter.update([path])
        ip_counter.update([ip])

    return {
        "window": {
            "since": since_label,
            "cutoff_utc": cutoff.isoformat() if cutoff is not None else None,
            "now_utc": now_utc.isoformat(),
            "fail2ban_lines_in_window": fail2ban_in_window,
            "nginx_lines_in_window": nginx_in_window,
        },
        "fail2ban": {
            "log_path": str(fail2ban_log_path),
            "lines_scanned": len(fail2ban_lines),
            "top_banned_ips": [{"ip": ip, "count": count} for ip, count in ban_counter.most_common(max(1, top))],
        },
        "nginx_scan": {
            "log_path": str(nginx_log_path),
            "lines_scanned": len(nginx_lines),
            "suspicious_404_total": suspicious_total,
            "top_paths": [{"path": path, "count": count} for path, count in path_counter.most_common(max(1, top))],
            "top_ips": [{"ip": ip, "count": count} for ip, count in ip_counter.most_common(max(1, top))],
        },
    }
