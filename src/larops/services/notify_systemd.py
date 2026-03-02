from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command


class NotifySystemdError(RuntimeError):
    pass


def telegram_service_name() -> str:
    return "larops-notify-telegram.service"


def _unit_path(unit_dir: Path) -> Path:
    return unit_dir / telegram_service_name()


def _quote_systemd(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_telegram_unit(
    *,
    larops_bin: str,
    config_path: Path,
    interval_seconds: int,
    batch_size: int | None,
    user: str,
    env_file: Path | None,
) -> str:
    cmd = [
        larops_bin,
        "--config",
        str(config_path),
        "notify",
        "telegram",
        "watch",
        "--interval",
        str(interval_seconds),
        "--iterations",
        "0",
        "--apply",
    ]
    if batch_size is not None:
        cmd.extend(["--batch-size", str(batch_size)])
    exec_start = shlex.join(cmd)

    lines = [
        "[Unit]",
        "Description=LarOps Telegram event-stream notifier",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        "Restart=always",
        "RestartSec=5",
        "WorkingDirectory=/",
        f"ExecStart={exec_start}",
    ]
    if env_file is not None:
        lines.append(f"EnvironmentFile=-{_quote_systemd(str(env_file))}")
    lines.extend(
        [
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    return "\n".join(lines)


def _run_systemctl(args: list[str], *, check: bool = True) -> str:
    completed = run_command(["systemctl", *args], check=check)
    return (completed.stdout or completed.stderr or "").strip()


def _systemd_status(service: str) -> dict[str, Any]:
    active = run_command(["systemctl", "is-active", service], check=False)
    enabled = run_command(["systemctl", "is-enabled", service], check=False)
    return {
        "active": (active.stdout or active.stderr or "").strip(),
        "enabled": (enabled.stdout or enabled.stderr or "").strip(),
    }


def enable_telegram_daemon(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    larops_bin: str,
    config_path: Path,
    interval_seconds: int,
    batch_size: int | None,
    env_file: Path | None,
) -> dict[str, Any]:
    if interval_seconds < 1:
        raise NotifySystemdError("Interval must be >= 1 second.")
    if batch_size is not None and batch_size < 1:
        raise NotifySystemdError("Batch size must be >= 1.")

    service = telegram_service_name()
    unit_path = _unit_path(unit_dir)
    unit_body = render_telegram_unit(
        larops_bin=larops_bin,
        config_path=config_path,
        interval_seconds=interval_seconds,
        batch_size=batch_size,
        user=user,
        env_file=env_file,
    )
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_body, encoding="utf-8")

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", service], check=True)
        except ShellCommandError as exc:
            raise NotifySystemdError(str(exc)) from exc

    return {
        "service_name": service,
        "unit_path": str(unit_path),
        "interval_seconds": interval_seconds,
        "batch_size": batch_size,
        "systemd_managed": systemd_manage,
    }


def disable_telegram_daemon(*, systemd_manage: bool) -> dict[str, Any]:
    service = telegram_service_name()
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", service], check=False)
    return {
        "service_name": service,
        "enabled": False,
        "systemd_managed": systemd_manage,
    }


def restart_telegram_daemon(*, systemd_manage: bool) -> dict[str, Any]:
    service = telegram_service_name()
    if systemd_manage:
        try:
            _run_systemctl(["restart", service], check=True)
        except ShellCommandError as exc:
            raise NotifySystemdError(str(exc)) from exc
    return {
        "service_name": service,
        "restarted": True,
        "systemd_managed": systemd_manage,
    }


def status_telegram_daemon(*, unit_dir: Path, systemd_manage: bool) -> dict[str, Any]:
    service = telegram_service_name()
    unit_path = _unit_path(unit_dir)
    return {
        "service_name": service,
        "unit_path": str(unit_path),
        "unit_exists": unit_path.exists(),
        "systemd": _systemd_status(service)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }
