from __future__ import annotations

from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.monitor_systemd import render_monitor_service, render_monitor_timer


class DoctorMetricsSystemdError(RuntimeError):
    pass


def doctor_metrics_service_name() -> str:
    return "larops-doctor-metrics.service"


def doctor_metrics_timer_name() -> str:
    return "larops-doctor-metrics.timer"


def _service_unit_path(unit_dir: Path) -> Path:
    return unit_dir / doctor_metrics_service_name()


def _timer_unit_path(unit_dir: Path) -> Path:
    return unit_dir / doctor_metrics_timer_name()


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
        raise DoctorMetricsSystemdError("--on-calendar cannot be empty.")
    if randomized_delay_seconds < 0:
        raise DoctorMetricsSystemdError("--randomized-delay must be >= 0.")


def enable_doctor_metrics_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    output_file: Path,
    quick: bool,
    include_checks: bool,
    include_host: bool,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    command = [
        larops_bin,
        "--config",
        str(config_path),
        "doctor",
        "metrics",
        "run",
        "--output-file",
        str(output_file),
        "--apply",
    ]
    if quick:
        command.append("--quick")
    if include_checks:
        command.append("--include-checks")
    if not include_host:
        command.append("--skip-host")

    service_path = _service_unit_path(unit_dir)
    timer_path = _timer_unit_path(unit_dir)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_monitor_service(
            description="LarOps doctor metrics export service",
            exec_command=command,
            user=user,
        ),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_monitor_timer(
            description="LarOps doctor metrics export timer",
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
            unit_name=doctor_metrics_service_name(),
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", doctor_metrics_timer_name()], check=True)
        except ShellCommandError as exc:
            raise DoctorMetricsSystemdError(str(exc)) from exc

    return {
        "service_name": doctor_metrics_service_name(),
        "timer_name": doctor_metrics_timer_name(),
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "systemd_managed": systemd_manage,
        "output_file": str(output_file),
        "command": command,
    }


def disable_doctor_metrics_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = doctor_metrics_service_name()
    timer_name = doctor_metrics_timer_name()
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir), _timer_unit_path(unit_dir)):
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


def status_doctor_metrics_timer(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service_name = doctor_metrics_service_name()
    timer_name = doctor_metrics_timer_name()
    service_path = _service_unit_path(unit_dir)
    timer_path = _timer_unit_path(unit_dir)
    return {
        "service_name": service_name,
        "timer_name": timer_name,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service_name) if systemd_manage else {"active": "unknown", "enabled": "unknown"},
        "timer": _systemd_status(timer_name) if systemd_manage else {"active": "unknown", "enabled": "unknown"},
    }
