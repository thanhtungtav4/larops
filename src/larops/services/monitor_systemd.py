from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.monitor_service_watch import MonitorServiceWatchError, resolve_service_targets


class MonitorSystemdError(RuntimeError):
    pass


def monitor_scan_service_name() -> str:
    return "larops-monitor-scan.service"


def monitor_scan_timer_name() -> str:
    return "larops-monitor-scan.timer"


def monitor_fim_service_name() -> str:
    return "larops-monitor-fim.service"


def monitor_fim_timer_name() -> str:
    return "larops-monitor-fim.timer"


def monitor_service_watch_service_name() -> str:
    return "larops-monitor-service.service"


def monitor_service_watch_timer_name() -> str:
    return "larops-monitor-service.timer"


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-") or "app"


def monitor_app_service_name(domain: str) -> str:
    return f"larops-monitor-app-{_sanitize_name(domain)}.service"


def monitor_app_timer_name(domain: str) -> str:
    return f"larops-monitor-app-{_sanitize_name(domain)}.timer"


def _service_unit_path(unit_dir: Path, service_name: str) -> Path:
    return unit_dir / service_name


def _timer_unit_path(unit_dir: Path, timer_name: str) -> Path:
    return unit_dir / timer_name


def render_monitor_service(*, description: str, exec_command: list[str], user: str) -> str:
    exec_start = shlex.join(exec_command)
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            f"User={user}",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectHome=read-only",
            "ProtectSystem=full",
            "ProtectControlGroups=true",
            "ProtectKernelModules=true",
            "ProtectKernelTunables=true",
            "RestrictSUIDSGID=true",
            "LockPersonality=true",
            "UMask=0077",
            f"ExecStart={exec_start}",
            "",
        ]
    )


def render_monitor_timer(
    *,
    description: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    unit_name: str,
) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
            "",
            "[Timer]",
            f"OnCalendar={on_calendar}",
            f"RandomizedDelaySec={randomized_delay_seconds}",
            "Persistent=true",
            f"Unit={unit_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _run_systemctl(args: list[str], *, check: bool = True) -> str:
    completed = run_command(["systemctl", *args], check=check)
    return (completed.stdout or completed.stderr or "").strip()


def _systemd_status(unit: str) -> dict[str, Any]:
    active = run_command(["systemctl", "is-active", unit], check=False)
    enabled = run_command(["systemctl", "is-enabled", unit], check=False)
    return {
        "active": (active.stdout or active.stderr or "").strip(),
        "enabled": (enabled.stdout or enabled.stderr or "").strip(),
    }


def _validate_timer_inputs(*, on_calendar: str, randomized_delay_seconds: int) -> None:
    if not on_calendar.strip():
        raise MonitorSystemdError("--on-calendar cannot be empty.")
    if randomized_delay_seconds < 0:
        raise MonitorSystemdError("--randomized-delay must be >= 0.")


def enable_monitor_scan_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    nginx_log_path: Path,
    state_file: Path,
    threshold_hits: int,
    window_seconds: int,
    max_lines: int,
    top: int,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    if threshold_hits < 1:
        raise MonitorSystemdError("--threshold-hits must be >= 1.")
    if window_seconds < 1:
        raise MonitorSystemdError("--window-seconds must be >= 1.")
    if max_lines < 1:
        raise MonitorSystemdError("--max-lines must be >= 1.")
    if top < 1:
        raise MonitorSystemdError("--top must be >= 1.")

    service_name = monitor_scan_service_name()
    timer_name = monitor_scan_timer_name()
    command = [
        larops_bin,
        "--config",
        str(config_path),
        "monitor",
        "scan",
        "run",
        "--nginx-log-path",
        str(nginx_log_path),
        "--state-file",
        str(state_file),
        "--threshold-hits",
        str(threshold_hits),
        "--window-seconds",
        str(window_seconds),
        "--max-lines",
        str(max_lines),
        "--top",
        str(top),
        "--apply",
    ]

    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_monitor_service(
            description="LarOps monitor scan service",
            exec_command=command,
            user=user,
        ),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_monitor_timer(
            description="LarOps monitor scan timer",
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
            unit_name=service_name,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", timer_name], check=True)
        except ShellCommandError as exc:
            raise MonitorSystemdError(str(exc)) from exc

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "command": command,
        "systemd_managed": systemd_manage,
    }


def disable_monitor_scan_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = monitor_scan_service_name()
    timer_name = monitor_scan_timer_name()
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir, service_name), _timer_unit_path(unit_dir, timer_name)):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_monitor_scan_timer(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service_name = monitor_scan_service_name()
    timer_name = monitor_scan_timer_name()
    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
        "timer": _systemd_status(timer_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }


def enable_monitor_fim_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    baseline_file: Path,
    root: Path | None,
    update_baseline: bool,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    service_name = monitor_fim_service_name()
    timer_name = monitor_fim_timer_name()
    command = [
        larops_bin,
        "--config",
        str(config_path),
        "monitor",
        "fim",
        "run",
        "--baseline-file",
        str(baseline_file),
        "--apply",
    ]
    if root is not None:
        command.extend(["--root", str(root)])
    if update_baseline:
        command.append("--update-baseline")

    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_monitor_service(
            description="LarOps monitor FIM service",
            exec_command=command,
            user=user,
        ),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_monitor_timer(
            description="LarOps monitor FIM timer",
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
            unit_name=service_name,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", timer_name], check=True)
        except ShellCommandError as exc:
            raise MonitorSystemdError(str(exc)) from exc

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "command": command,
        "systemd_managed": systemd_manage,
        "update_baseline": update_baseline,
    }


def disable_monitor_fim_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = monitor_fim_service_name()
    timer_name = monitor_fim_timer_name()
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir, service_name), _timer_unit_path(unit_dir, timer_name)):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_monitor_fim_timer(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service_name = monitor_fim_service_name()
    timer_name = monitor_fim_timer_name()
    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
        "timer": _systemd_status(timer_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }


def enable_monitor_service_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    state_file: Path,
    services: list[str],
    profiles: list[str],
    restart_down_services: bool,
    restart_cooldown_seconds: int,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    if restart_cooldown_seconds < 0:
        raise MonitorSystemdError("--restart-cooldown must be >= 0.")
    try:
        resolved_services = resolve_service_targets(services=services, profiles=profiles)
    except MonitorServiceWatchError as exc:
        raise MonitorSystemdError(str(exc)) from exc

    service_name = monitor_service_watch_service_name()
    timer_name = monitor_service_watch_timer_name()
    command = [
        larops_bin,
        "--config",
        str(config_path),
        "monitor",
        "service",
        "run",
        "--state-file",
        str(state_file),
        "--restart-cooldown",
        str(restart_cooldown_seconds),
        "--apply",
    ]
    for profile in profiles:
        command.extend(["--profile", profile])
    for service in services:
        command.extend(["--service", service])
    if restart_down_services:
        command.append("--restart-down-services")
    else:
        command.append("--no-restart-down-services")

    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_monitor_service(
            description="LarOps monitor critical service watchdog",
            exec_command=command,
            user=user,
        ),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_monitor_timer(
            description="LarOps monitor critical service timer",
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
            unit_name=service_name,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", timer_name], check=True)
        except ShellCommandError as exc:
            raise MonitorSystemdError(str(exc)) from exc

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "command": command,
        "services": services,
        "profiles": profiles,
        "resolved_services": resolved_services,
        "restart_down_services": restart_down_services,
        "restart_cooldown_seconds": restart_cooldown_seconds,
        "systemd_managed": systemd_manage,
    }


def disable_monitor_service_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = monitor_service_watch_service_name()
    timer_name = monitor_service_watch_timer_name()
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir, service_name), _timer_unit_path(unit_dir, timer_name)):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_monitor_service_timer(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service_name = monitor_service_watch_service_name()
    timer_name = monitor_service_watch_timer_name()
    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
        "timer": _systemd_status(timer_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }


def enable_monitor_app_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    domain: str,
    state_file: Path,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    if not domain.strip():
        raise MonitorSystemdError("Domain cannot be empty.")

    service_name = monitor_app_service_name(domain)
    timer_name = monitor_app_timer_name(domain)
    command = [
        larops_bin,
        "--config",
        str(config_path),
        "monitor",
        "app",
        "run",
        domain,
        "--state-file",
        str(state_file),
        "--apply",
    ]

    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_monitor_service(
            description=f"LarOps app monitor service for {domain}",
            exec_command=command,
            user=user,
        ),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_monitor_timer(
            description=f"LarOps app monitor timer for {domain}",
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
            unit_name=service_name,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", timer_name], check=True)
        except ShellCommandError as exc:
            raise MonitorSystemdError(str(exc)) from exc

    return {
        "domain": domain,
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "command": command,
        "systemd_managed": systemd_manage,
    }


def disable_monitor_app_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = monitor_app_service_name(domain)
    timer_name = monitor_app_timer_name(domain)
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir, service_name), _timer_unit_path(unit_dir, timer_name)):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "domain": domain,
        "service_name": service_name,
        "timer_name": timer_name,
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_monitor_app_timer(*, unit_dir: Path, systemd_manage: bool, domain: str) -> dict[str, Any]:
    service_name = monitor_app_service_name(domain)
    timer_name = monitor_app_timer_name(domain)
    service_path = _service_unit_path(unit_dir, service_name)
    timer_path = _timer_unit_path(unit_dir, timer_name)
    return {
        "domain": domain,
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
        "timer": _systemd_status(timer_name)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }
