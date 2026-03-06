from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command


class DbAutoBackupError(RuntimeError):
    pass


def _sanitize_domain(domain: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", domain).strip("-")


def db_backup_service_name(domain: str) -> str:
    return f"larops-db-backup-{_sanitize_domain(domain)}.service"


def db_backup_timer_name(domain: str) -> str:
    return f"larops-db-backup-{_sanitize_domain(domain)}.timer"


def _service_unit_path(unit_dir: Path, domain: str) -> Path:
    return unit_dir / db_backup_service_name(domain)


def _timer_unit_path(unit_dir: Path, domain: str) -> Path:
    return unit_dir / db_backup_timer_name(domain)


def render_db_backup_service(*, domain: str, exec_command: list[str], user: str) -> str:
    exec_start = shlex.join(exec_command)
    return "\n".join(
        [
            "[Unit]",
            f"Description=LarOps DB backup service for {domain}",
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


def render_db_backup_timer(
    *,
    domain: str,
    on_calendar: str,
    randomized_delay_seconds: int,
) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description=LarOps DB backup timer for {domain}",
            "",
            "[Timer]",
            f"OnCalendar={on_calendar}",
            f"RandomizedDelaySec={randomized_delay_seconds}",
            "Persistent=true",
            f"Unit={db_backup_service_name(domain)}",
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
        raise DbAutoBackupError("--on-calendar cannot be empty.")
    if randomized_delay_seconds < 0:
        raise DbAutoBackupError("--randomized-delay must be >= 0.")


def enable_db_backup_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    user: str,
    domain: str,
    on_calendar: str,
    randomized_delay_seconds: int,
    larops_bin: str,
    config_path: Path,
    engine: str,
    database: str,
    credential_file: Path | None,
    target_dir: Path | None,
    retain_count: int,
) -> dict[str, Any]:
    _validate_timer_inputs(on_calendar=on_calendar, randomized_delay_seconds=randomized_delay_seconds)
    if retain_count < 1:
        raise DbAutoBackupError("--retain-count must be >= 1.")

    command = [
        larops_bin,
        "--config",
        str(config_path),
        "db",
        "backup",
        domain,
        "--engine",
        engine,
        "--database",
        database,
        "--retain-count",
        str(retain_count),
        "--apply",
    ]
    if credential_file is not None:
        command.extend(["--credential-file", str(credential_file)])
    if target_dir is not None:
        command.extend(["--target-dir", str(target_dir)])

    service_path = _service_unit_path(unit_dir, domain)
    timer_path = _timer_unit_path(unit_dir, domain)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_db_backup_service(domain=domain, exec_command=command, user=user),
        encoding="utf-8",
    )
    timer_path.write_text(
        render_db_backup_timer(
            domain=domain,
            on_calendar=on_calendar,
            randomized_delay_seconds=randomized_delay_seconds,
        ),
        encoding="utf-8",
    )

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", db_backup_timer_name(domain)], check=True)
        except ShellCommandError as exc:
            raise DbAutoBackupError(str(exc)) from exc

    return {
        "domain": domain,
        "service_name": db_backup_service_name(domain),
        "timer_name": db_backup_timer_name(domain),
        "service_unit_path": str(service_path),
        "timer_unit_path": str(timer_path),
        "on_calendar": on_calendar,
        "randomized_delay_seconds": randomized_delay_seconds,
        "systemd_managed": systemd_manage,
        "command": command,
    }


def disable_db_backup_timer(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
    remove_units: bool,
) -> dict[str, Any]:
    service_name = db_backup_service_name(domain)
    timer_name = db_backup_timer_name(domain)
    removed_paths: list[str] = []
    if systemd_manage:
        run_command(["systemctl", "disable", "--now", timer_name], check=False)

    if remove_units:
        for path in (_service_unit_path(unit_dir, domain), _timer_unit_path(unit_dir, domain)):
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


def status_db_backup_timer(*, unit_dir: Path, systemd_manage: bool, domain: str) -> dict[str, Any]:
    service_name = db_backup_service_name(domain)
    timer_name = db_backup_timer_name(domain)
    service_path = _service_unit_path(unit_dir, domain)
    timer_path = _timer_unit_path(unit_dir, domain)
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
