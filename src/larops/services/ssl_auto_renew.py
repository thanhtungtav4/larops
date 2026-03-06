from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command


class SslAutoRenewError(RuntimeError):
    pass


def ssl_renew_service_name() -> str:
    return "larops-ssl-renew.service"


def ssl_renew_timer_name() -> str:
    return "larops-ssl-renew.timer"


def _service_unit_path(unit_dir: Path) -> Path:
    return unit_dir / ssl_renew_service_name()


def _timer_unit_path(unit_dir: Path) -> Path:
    return unit_dir / ssl_renew_timer_name()


def render_ssl_renew_service(*, renew_command: list[str], user: str) -> str:
    exec_start = shlex.join(renew_command)
    return "\n".join(
        [
            "[Unit]",
            "Description=LarOps SSL auto-renew service",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            f"User={user}",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectHome=read-only",
            "RestrictSUIDSGID=true",
            "LockPersonality=true",
            "UMask=0077",
            f"ExecStart={exec_start}",
            "",
        ]
    )


def render_ssl_renew_timer(
    *,
    on_calendar: str,
    randomized_delay_seconds: int,
) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=LarOps SSL auto-renew timer",
            "",
            "[Timer]",
            f"OnCalendar={on_calendar}",
            f"RandomizedDelaySec={randomized_delay_seconds}",
            "Persistent=true",
            f"Unit={ssl_renew_service_name()}",
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


def _systemd_unit_known(unit: str) -> bool:
    status = _systemd_status(unit)
    return status["active"] not in {"unknown", "not-found", ""} or status["enabled"] not in {"unknown", "not-found", ""}


def enable_ssl_auto_renew(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    renew_command: list[str],
) -> dict[str, Any]:
    if not on_calendar.strip():
        raise SslAutoRenewError("--on-calendar cannot be empty.")
    if randomized_delay_seconds < 0:
        raise SslAutoRenewError("--randomized-delay must be >= 0.")

    service_path = _service_unit_path(unit_dir)
    timer_path = _timer_unit_path(unit_dir)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_ssl_renew_service(renew_command=renew_command, user=user),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_ssl_renew_timer(
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", ssl_renew_timer_name()], check=True)
        except ShellCommandError as exc:
            raise SslAutoRenewError(str(exc)) from exc

    return {
        "service_name": ssl_renew_service_name(),
        "timer_name": ssl_renew_timer_name(),
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "systemd_managed": systemd_manage,
        "renew_command": renew_command,
    }


def disable_ssl_auto_renew(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_units: bool,
) -> dict[str, Any]:
    removed_paths: list[str] = []
    if systemd_manage:
        try:
            if _systemd_unit_known(ssl_renew_timer_name()):
                _run_systemctl(["disable", "--now", ssl_renew_timer_name()], check=True)
        except ShellCommandError as exc:
            raise SslAutoRenewError(str(exc)) from exc

    if remove_units:
        for path in (_service_unit_path(unit_dir), _timer_unit_path(unit_dir)):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "service_name": ssl_renew_service_name(),
        "timer_name": ssl_renew_timer_name(),
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_ssl_auto_renew(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service = ssl_renew_service_name()
    timer = ssl_renew_timer_name()
    service_path = _service_unit_path(unit_dir)
    timer_path = _timer_unit_path(unit_dir)
    return {
        "service_name": service,
        "timer_name": timer,
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "service_unit_exists": service_path.exists(),
        "timer_unit_exists": timer_path.exists(),
        "service": _systemd_status(service)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
        "timer": _systemd_status(timer)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }
