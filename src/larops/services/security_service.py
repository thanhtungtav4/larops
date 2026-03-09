from __future__ import annotations

import re
import shutil
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from larops.core.shell import run_command
from larops.services.app_lifecycle import list_registered_apps
from larops.services.secure_service import nginx_root_include_status, resolve_nginx_hardening_paths
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux
from larops.services.stack_service import StackServiceError, detect_stack_platform
from larops.services.monitor_systemd import (
    status_monitor_app_timer,
    status_monitor_fim_timer,
    status_monitor_scan_timer,
    status_monitor_service_timer,
)
from larops.services.notify_systemd import status_telegram_daemon


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


class SecurityServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class SecurityInstallPlan:
    firewall_backend: str
    firewall_commands: list[list[str]]
    fail2ban_jail_path: Path
    fail2ban_filter_path: Path
    fail2ban_jail_body: str
    fail2ban_filter_body: str
    fail2ban_service: str = "fail2ban"
    notes: list[str] | None = None


def _normalize_ufw_logging(level: str) -> str:
    normalized = level.strip().lower()
    if normalized in {"off", "on", "low", "medium", "high", "full"}:
        return normalized
    return "low"


def _parse_sshd_port_from_jail(fail2ban_jail_path: Path) -> int:
    if fail2ban_jail_path.exists() and fail2ban_jail_path.is_file():
        try:
            body = fail2ban_jail_path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        matched = re.search(r"^\s*port\s*=\s*(\d+)\s*$", body, flags=re.MULTILINE)
        if matched:
            try:
                parsed = int(matched.group(1))
            except ValueError:
                parsed = 22
            if 1 <= parsed <= 65535:
                return parsed
    return 22


def _parse_firewalld_active_zones(output: str) -> list[str]:
    zones: list[str] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith((" ", "\t")):
            continue
        zone = raw_line.strip()
        if ":" in zone:
            continue
        zones.append(zone)
    return list(dict.fromkeys(zones))


def _parse_firewalld_tokens(output: str) -> set[str]:
    return {token.strip() for token in output.split() if token.strip()}


def _collect_firewalld_zone_report(zone: str, *, ssh_port: int) -> dict[str, Any]:
    services_result = run_command(["firewall-cmd", "--zone", zone, "--list-services"], check=False)
    ports_result = run_command(["firewall-cmd", "--zone", zone, "--list-ports"], check=False)
    services = _parse_firewalld_tokens((services_result.stdout or services_result.stderr or "").strip())
    ports = _parse_firewalld_tokens((ports_result.stdout or ports_result.stderr or "").strip())
    required_services = {"http", "https"}
    required_ports = {f"{ssh_port}/tcp"}
    return {
        "zone": zone,
        "services": sorted(services),
        "ports": sorted(ports),
        "required_services": sorted(required_services),
        "required_ports": sorted(required_ports),
        "missing_services": sorted(required_services - services),
        "missing_ports": sorted(required_ports - ports),
        "exit_code": max(services_result.returncode, ports_result.returncode),
        "rules_ok": services_result.returncode == 0
        and ports_result.returncode == 0
        and required_services.issubset(services)
        and required_ports.issubset(ports),
    }


def render_fail2ban_jail(
    *,
    ssh_port: int,
    nginx_log_path: Path,
    fail2ban_log_path: Path,
    banaction: str,
) -> str:
    return "\n".join(
        [
            "[DEFAULT]",
            f"banaction = {banaction}",
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
    try:
        platform = detect_stack_platform()
    except StackServiceError as exc:
        raise SecurityServiceError(str(exc)) from exc

    normalized_logging = _normalize_ufw_logging(ufw_logging)
    notes: list[str] = []

    if platform.family == "debian":
        firewall_backend = "ufw"
        commands: list[list[str]] = [
            ["ufw", "allow", f"{ssh_port}/tcp"],
            ["ufw", "allow", "80/tcp"],
            ["ufw", "allow", "443/tcp"],
        ]
        if limit_ssh:
            commands.append(["ufw", "limit", f"{ssh_port}/tcp"])
        if normalized_logging != "off":
            commands.append(["ufw", "logging", normalized_logging])
        else:
            commands.append(["ufw", "logging", "off"])
        commands.append(["ufw", "--force", "enable"])
        banaction = "ufw"
    elif platform.family == "el9":
        firewall_backend = "firewalld"
        commands = [
            ["systemctl", "enable", "--now", "firewalld"],
            ["firewall-cmd", "--permanent", "--add-port", f"{ssh_port}/tcp"],
            ["firewall-cmd", "--permanent", "--add-service", "http"],
            ["firewall-cmd", "--permanent", "--add-service", "https"],
            ["firewall-cmd", "--reload"],
        ]
        if limit_ssh:
            notes.append("SSH rate limiting is not yet applied automatically on firewalld hosts; rely on Fail2ban.")
        if normalized_logging != "off":
            notes.append("Firewall logging level is currently only applied automatically for UFW hosts.")
        if platform.os_id == "rhel":
            notes.append(
                "RHEL 9 still requires Fail2ban to be available from an enabled repository before applying the security baseline."
            )
        banaction = "firewallcmd-rich-rules"
    else:
        raise SecurityServiceError(f"Unsupported security platform family: {platform.family}")

    return SecurityInstallPlan(
        firewall_backend=firewall_backend,
        firewall_commands=commands,
        fail2ban_jail_path=fail2ban_jail_path,
        fail2ban_filter_path=fail2ban_filter_path,
        fail2ban_jail_body=render_fail2ban_jail(
            ssh_port=ssh_port,
            nginx_log_path=nginx_log_path,
            fail2ban_log_path=fail2ban_log_path,
            banaction=banaction,
        ),
        fail2ban_filter_body=render_fail2ban_filter(),
        notes=notes or None,
    )


def apply_security_install_plan(plan: SecurityInstallPlan) -> dict[str, Any]:
    previous_jail = plan.fail2ban_jail_path.read_text(encoding="utf-8") if plan.fail2ban_jail_path.exists() else None
    previous_filter = (
        plan.fail2ban_filter_path.read_text(encoding="utf-8") if plan.fail2ban_filter_path.exists() else None
    )

    plan.fail2ban_jail_path.parent.mkdir(parents=True, exist_ok=True)
    plan.fail2ban_filter_path.parent.mkdir(parents=True, exist_ok=True)
    plan.fail2ban_jail_path.write_text(plan.fail2ban_jail_body, encoding="utf-8")
    plan.fail2ban_filter_path.write_text(plan.fail2ban_filter_body, encoding="utf-8")

    try:
        relabel_managed_paths_for_selinux(
            [plan.fail2ban_jail_path, plan.fail2ban_filter_path],
            run_command=run_command,
            which=shutil.which,
            roots=[Path("/etc")],
        )
        for command in plan.firewall_commands:
            run_command(command, check=True)

        run_command(["systemctl", "enable", "--now", plan.fail2ban_service], check=True)
        run_command(["systemctl", "restart", plan.fail2ban_service], check=True)
    except (Exception, SelinuxServiceError):
        if previous_jail is None:
            plan.fail2ban_jail_path.unlink(missing_ok=True)
        else:
            plan.fail2ban_jail_path.write_text(previous_jail, encoding="utf-8")
        if previous_filter is None:
            plan.fail2ban_filter_path.unlink(missing_ok=True)
        else:
            plan.fail2ban_filter_path.write_text(previous_filter, encoding="utf-8")
        raise

    return {
        "firewall_backend": plan.firewall_backend,
        "firewall_commands_executed": plan.firewall_commands,
        "fail2ban_jail_path": str(plan.fail2ban_jail_path),
        "fail2ban_filter_path": str(plan.fail2ban_filter_path),
        "fail2ban_service": plan.fail2ban_service,
        "notes": plan.notes or [],
    }


def collect_security_status(
    *,
    fail2ban_jail_path: Path,
    fail2ban_filter_path: Path,
) -> dict[str, Any]:
    try:
        platform = detect_stack_platform()
    except StackServiceError as exc:
        raise SecurityServiceError(str(exc)) from exc

    if platform.family == "debian":
        firewall_status = run_command(["ufw", "status"], check=False)
        firewall_report = {
            "backend": "ufw",
            "raw": (firewall_status.stdout or firewall_status.stderr or "").strip(),
            "exit_code": firewall_status.returncode,
        }
    elif platform.family == "el9":
        ssh_port = _parse_sshd_port_from_jail(fail2ban_jail_path)
        firewalld_active = run_command(["systemctl", "is-active", "firewalld"], check=False)
        firewalld_enabled = run_command(["systemctl", "is-enabled", "firewalld"], check=False)
        firewalld_state = run_command(["firewall-cmd", "--state"], check=False)
        active_zones_result = run_command(["firewall-cmd", "--get-active-zones"], check=False)
        default_zone_result = run_command(["firewall-cmd", "--get-default-zone"], check=False)
        active_zones = _parse_firewalld_active_zones((active_zones_result.stdout or active_zones_result.stderr or "").strip())
        default_zone = (default_zone_result.stdout or default_zone_result.stderr or "").strip()
        effective_zones = active_zones or ([default_zone] if default_zone else [])
        zone_reports = [_collect_firewalld_zone_report(zone, ssh_port=ssh_port) for zone in effective_zones]
        firewall_report = {
            "backend": "firewalld",
            "active": (firewalld_active.stdout or firewalld_active.stderr or "").strip(),
            "enabled": (firewalld_enabled.stdout or firewalld_enabled.stderr or "").strip(),
            "state": (firewalld_state.stdout or firewalld_state.stderr or "").strip(),
            "ssh_port": ssh_port,
            "default_zone": default_zone,
            "active_zones": active_zones,
            "effective_zones": effective_zones,
            "zones": zone_reports,
            "exit_code": max(
                firewalld_active.returncode,
                firewalld_enabled.returncode,
                firewalld_state.returncode,
                active_zones_result.returncode,
                default_zone_result.returncode,
                *(zone_report["exit_code"] for zone_report in zone_reports),
            ),
        }
    else:
        raise SecurityServiceError(f"Unsupported security platform family: {platform.family}")

    fail2ban_status = run_command(["fail2ban-client", "status"], check=False)
    sshd_status = run_command(["fail2ban-client", "status", "sshd"], check=False)
    nginx_scan_status = run_command(["fail2ban-client", "status", "larops-nginx-scan"], check=False)

    def _output(raw) -> str:
        return (raw.stdout or raw.stderr or "").strip()

    return {
        "firewall": firewall_report,
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


def determine_security_status_level(report: dict[str, Any]) -> str:
    firewall = report.get("firewall", {})
    backend = str(firewall.get("backend", "")).lower()
    if int(firewall.get("exit_code", 1)) != 0:
        return "error"
    if backend == "ufw":
        firewall_raw = str(firewall.get("raw", "")).lower()
        if "status: inactive" in firewall_raw:
            return "error"
    elif backend == "firewalld":
        if str(firewall.get("active", "")).strip().lower() != "active":
            return "error"
        if str(firewall.get("enabled", "")).strip().lower() not in {"enabled", "static"}:
            return "error"
        if str(firewall.get("state", "")).strip().lower() != "running":
            return "error"
        effective_zones = firewall.get("effective_zones", [])
        if not isinstance(effective_zones, list) or not effective_zones:
            return "error"
        zone_reports = firewall.get("zones", [])
        if not isinstance(zone_reports, list) or any(not bool(item.get("rules_ok")) for item in zone_reports):
            return "error"
    else:
        return "error"

    if int(report["fail2ban"]["exit_code"]) != 0:
        return "error"
    fail2ban_raw = str(report["fail2ban"]["raw"]).lower()
    if re.search(r"number of jail:\s*0\b", fail2ban_raw):
        return "error"

    files = report.get("files", {})
    if any(not bool(item.get("exists")) for item in files.values() if isinstance(item, dict)):
        return "error"

    jails = report.get("jails", {})
    if any(int(item.get("exit_code", 1)) != 0 for item in jails.values() if isinstance(item, dict)):
        return "error"

    return "ok"


def _managed_file_status(path: Path) -> dict[str, Any]:
    exists = path.exists()
    managed = False
    if exists and path.is_file():
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        managed = "Managed by LarOps" in body
    return {"path": str(path), "exists": exists, "managed": managed}


def _nginx_include_status(server_config_file: Path | None, snippet_file: Path) -> dict[str, Any]:
    if server_config_file is None:
        return {"path": None, "include_present": None}
    exists = server_config_file.exists() and server_config_file.is_file()
    include_present = False
    if exists:
        try:
            body = server_config_file.read_text(encoding="utf-8")
        except OSError:
            body = ""
        include_present = f"include {snippet_file};" in body
    return {"path": str(server_config_file), "exists": exists, "include_present": include_present}


def _timer_configured(status: dict[str, Any], *, systemd_manage: bool) -> bool:
    if not bool(status.get("service_unit_exists")) or not bool(status.get("timer_unit_exists")):
        return False
    if not systemd_manage:
        return True
    timer = status.get("timer", {})
    return str(timer.get("active")) == "active" and str(timer.get("enabled")) in {"enabled", "static"}


def _service_configured(status: dict[str, Any], *, systemd_manage: bool) -> bool:
    if not bool(status.get("unit_exists")):
        return False
    if not systemd_manage:
        return True
    systemd = status.get("systemd", {})
    return str(systemd.get("active")) == "active" and str(systemd.get("enabled")) in {"enabled", "static"}


def _secure_nginx_check(secure_nginx: dict[str, Any]) -> str:
    if not (
        secure_nginx["http_config"]["exists"]
        and secure_nginx["http_config"]["managed"]
        and secure_nginx["server_snippet"]["exists"]
        and secure_nginx["server_snippet"]["managed"]
    ):
        return "error"

    include_status = secure_nginx["server_include"]
    if include_status["path"] is None:
        root_include = secure_nginx["root_include"]
        if root_include["verification_applicable"] is True:
            return "ok" if root_include["loads_snippet"] is True else "error"
        return "warn"
    if not include_status.get("exists"):
        return "error"
    return "ok" if include_status.get("include_present") is True else "error"


def collect_security_posture(
    *,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    fail2ban_jail_path: Path,
    fail2ban_filter_path: Path,
    sshd_drop_in_file: Path,
    nginx_http_config_file: Path | None,
    nginx_server_snippet_file: Path | None,
    nginx_server_config_file: Path | None,
    nginx_root_config_file: Path | None,
) -> dict[str, Any]:
    baseline = collect_security_status(
        fail2ban_jail_path=fail2ban_jail_path,
        fail2ban_filter_path=fail2ban_filter_path,
    )
    baseline_level = determine_security_status_level(baseline)
    resolved_nginx_paths = resolve_nginx_hardening_paths(
        http_config_file=nginx_http_config_file,
        server_snippet_file=nginx_server_snippet_file,
        root_config_file=nginx_root_config_file,
    )

    secure_ssh = _managed_file_status(sshd_drop_in_file)
    secure_nginx = {
        "http_config": _managed_file_status(resolved_nginx_paths["http_config_file"]),
        "server_snippet": _managed_file_status(resolved_nginx_paths["server_snippet_file"]),
        "server_include": _nginx_include_status(nginx_server_config_file, resolved_nginx_paths["server_snippet_file"]),
        "root_include": nginx_root_include_status(
            root_config_file=resolved_nginx_paths["root_config_file"],
            snippet_file=resolved_nginx_paths["server_snippet_file"],
        ),
    }
    scan_timer = status_monitor_scan_timer(unit_dir=unit_dir, systemd_manage=systemd_manage)
    fim_timer = status_monitor_fim_timer(unit_dir=unit_dir, systemd_manage=systemd_manage)
    service_timer = status_monitor_service_timer(unit_dir=unit_dir, systemd_manage=systemd_manage)
    notifier = status_telegram_daemon(unit_dir=unit_dir, systemd_manage=systemd_manage)
    registered_apps = list_registered_apps(state_path)
    app_timers = [status_monitor_app_timer(unit_dir=unit_dir, systemd_manage=systemd_manage, domain=domain) for domain in registered_apps]

    checks = {
        "baseline": baseline_level,
        "secure_ssh": "ok" if secure_ssh["exists"] and secure_ssh["managed"] else "error",
        "secure_nginx": _secure_nginx_check(secure_nginx),
        "scan_timer": "ok" if _timer_configured(scan_timer, systemd_manage=systemd_manage) else "error",
        "fim_timer": "ok" if _timer_configured(fim_timer, systemd_manage=systemd_manage) else "error",
        "service_watchdog_timer": "ok" if _timer_configured(service_timer, systemd_manage=systemd_manage) else "error",
        "telegram_notifier": "ok" if _service_configured(notifier, systemd_manage=systemd_manage) else "warn",
        "app_timers": "ok"
        if all(_timer_configured(item, systemd_manage=systemd_manage) for item in app_timers)
        else ("warn" if app_timers else "ok"),
    }

    level = "ok"
    if any(value == "error" for value in checks.values()):
        level = "error"
    elif any(value == "warn" for value in checks.values()):
        level = "warn"

    return {
        "level": level,
        "checks": checks,
        "systemd_managed": systemd_manage,
        "baseline": baseline,
        "secure_ssh": secure_ssh,
        "secure_nginx": secure_nginx,
        "monitoring": {
            "scan_timer": scan_timer,
            "fim_timer": fim_timer,
            "service_timer": service_timer,
            "app_timers": app_timers,
        },
        "notifier": notifier,
        "registered_apps": registered_apps,
    }


def _tail_lines(path: Path, *, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return list(deque(handle, maxlen=max(1, max_lines)))


def _select_lines_for_window(
    path: Path,
    *,
    cutoff: datetime | None,
    max_lines: int,
    timestamp_extractor: Any,
) -> tuple[list[str], int]:
    if not path.exists():
        return [], 0
    if cutoff is None:
        lines = _tail_lines(path, max_lines=max_lines)
        return lines, len(lines)

    selected: list[str] = []
    scanned = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            scanned += 1
            line = raw_line.rstrip("\n")
            timestamp = timestamp_extractor(line)
            if timestamp is None or timestamp < cutoff:
                continue
            selected.append(line)
    return selected, scanned


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
    fail2ban_lines, fail2ban_scanned = _select_lines_for_window(
        fail2ban_log_path,
        cutoff=cutoff,
        max_lines=max_lines,
        timestamp_extractor=_extract_fail2ban_timestamp,
    )
    fail2ban_in_window = 0
    for line in fail2ban_lines:
        fail2ban_in_window += 1
        matched = re.search(r"\bBan\s+(\S+)\b", line)
        if matched:
            ban_counter.update([matched.group(1)])

    path_counter: Counter[str] = Counter()
    ip_counter: Counter[str] = Counter()
    suspicious_total = 0
    nginx_lines, nginx_scanned = _select_lines_for_window(
        nginx_log_path,
        cutoff=cutoff,
        max_lines=max_lines,
        timestamp_extractor=_extract_nginx_timestamp,
    )
    nginx_in_window = 0
    for line in nginx_lines:
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
            "lines_scanned": fail2ban_scanned,
            "top_banned_ips": [{"ip": ip, "count": count} for ip, count in ban_counter.most_common(max(1, top))],
        },
        "nginx_scan": {
            "log_path": str(nginx_log_path),
            "lines_scanned": nginx_scanned,
            "suspicious_404_total": suspicious_total,
            "top_paths": [{"path": path, "count": count} for path, count in path_counter.most_common(max(1, top))],
            "top_ips": [{"ip": ip, "count": count} for ip, count in ip_counter.most_common(max(1, top))],
        },
    }
