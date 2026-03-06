from __future__ import annotations

import shutil
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path

from larops.config import DoctorAppCommandCheckConfig
from larops.core.shell import run_command
from larops.services.app_lifecycle import get_app_paths
from larops.services.db_service import manifest_path, restore_verify_report_path
from larops.services.db_systemd import db_backup_service_name, db_backup_timer_name


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def _check_command(command: str) -> DoctorCheck:
    return DoctorCheck(
        name=f"command:{command}",
        status="ok" if shutil.which(command) else "warn",
        detail="available" if shutil.which(command) else "missing",
    )


def _check_disk() -> DoctorCheck:
    usage = shutil.disk_usage("/")
    used_pct = int((usage.used / usage.total) * 100)
    status = "ok" if used_pct < 80 else "warn" if used_pct < 90 else "error"
    return DoctorCheck(name="disk:/", status=status, detail=f"{used_pct}% used")


def _check_path_writable(path: Path) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".doctor-write-check"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return DoctorCheck(name=f"path:{path}", status="ok", detail="writable")
    except Exception as exc:  # pragma: no cover
        return DoctorCheck(name=f"path:{path}", status="error", detail=str(exc))


def _check_file_presence(path: Path, *, label: str, missing_status: str = "warn") -> DoctorCheck:
    if path.exists():
        return DoctorCheck(name=label, status="ok", detail=str(path))
    return DoctorCheck(name=label, status=missing_status, detail=f"missing: {path}")


def _check_systemd_unit(unit: str) -> DoctorCheck:
    try:
        active = run_command(["systemctl", "is-active", unit], check=False)
        enabled = run_command(["systemctl", "is-enabled", unit], check=False)
    except FileNotFoundError:
        return DoctorCheck(name=f"systemd:{unit}", status="warn", detail="systemctl unavailable")
    active_raw = (active.stdout or active.stderr or "").strip()
    enabled_raw = (enabled.stdout or enabled.stderr or "").strip()
    if active_raw == "active" and enabled_raw == "enabled":
        status = "ok"
    elif active_raw in {"active", "activating"} or enabled_raw == "enabled":
        status = "warn"
    else:
        status = "warn"
    return DoctorCheck(name=f"systemd:{unit}", status=status, detail=f"active={active_raw}, enabled={enabled_raw}")


def _check_latest_backup(state_path: Path, domain: str) -> DoctorCheck:
    backup_dir = state_path / "backups" / domain
    backups = sorted([item for item in backup_dir.glob("*.sql.gz") if item.is_file()])
    if not backups:
        return DoctorCheck(name=f"backup:{domain}", status="warn", detail=f"no backups in {backup_dir}")
    latest = backups[-1]
    age_hours = (datetime.now(UTC).timestamp() - latest.stat().st_mtime) / 3600
    manifest = manifest_path(latest)
    status = "ok" if age_hours <= 24 else "warn" if age_hours <= 72 else "error"
    if not manifest.exists() and status == "ok":
        status = "warn"
    detail = f"{latest.name} age={int(age_hours)}h"
    if manifest.exists():
        detail += f", manifest={manifest.name}"
    else:
        detail += ", manifest=missing"
    return DoctorCheck(name=f"backup:{domain}", status=status, detail=detail)


def _check_restore_verify_report(state_path: Path, domain: str) -> DoctorCheck:
    report_path = restore_verify_report_path(state_path, domain)
    if not report_path.exists():
        return DoctorCheck(
            name=f"backup-verify:{domain}",
            status="warn",
            detail=f"missing: {report_path}",
        )
    age_hours = (datetime.now(UTC).timestamp() - report_path.stat().st_mtime) / 3600
    status = "ok" if age_hours <= 168 else "warn"
    return DoctorCheck(
        name=f"backup-verify:{domain}",
        status=status,
        detail=f"{report_path.name} age={int(age_hours)}h",
    )


def _run_app_command_check(
    *,
    current_path: Path,
    check_config: DoctorAppCommandCheckConfig,
) -> DoctorCheck:
    script = f'cd "{str(current_path)}" && {check_config.command}'
    try:
        completed = run_command(
            ["bash", "-lc", script],
            check=True,
            timeout_seconds=check_config.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck(name=f"app-check:{check_config.name}", status="error", detail=str(exc))
    stdout = (completed.stdout or "").strip()
    detail = stdout if stdout else "ok"
    return DoctorCheck(name=f"app-check:{check_config.name}", status="ok", detail=detail)


def run_host_checks(*, state_path: Path, events_path: Path, quick: bool, unit_dir: Path, systemd_manage: bool) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [
        _check_path_writable(state_path),
        _check_path_writable(events_path.parent),
        _check_file_presence(events_path, label=f"events:{events_path}", missing_status="warn"),
        _check_file_presence(state_path / "security" / "fim_baseline.json", label="security:fim-baseline", missing_status="warn"),
        _check_disk(),
    ]
    command_set = ["python3", "bash", "openssl"]
    if not quick:
        command_set += ["nginx", "php", "mysqldump", "certbot"]
    for command in command_set:
        checks.append(_check_command(command))
    if systemd_manage and not quick:
        for unit in (
            "larops-notify-telegram.service",
            "larops-ssl-renew.timer",
            "larops-monitor-scan.timer",
            "larops-monitor-fim.timer",
        ):
            checks.append(_check_systemd_unit(unit))
    return checks


def run_app_checks(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    unit_dir: Path,
    systemd_manage: bool,
    app_command_checks: list[DoctorAppCommandCheckConfig],
) -> list[DoctorCheck]:
    paths = get_app_paths(base_releases_path, state_path, domain)
    checks = []
    checks.append(
        DoctorCheck(
            name=f"app:{domain}:metadata",
            status="ok" if paths.metadata.exists() else "error",
            detail=str(paths.metadata),
        )
    )
    checks.append(
        DoctorCheck(
            name=f"app:{domain}:current",
            status="ok" if paths.current.exists() else "warn",
            detail=str(paths.current),
        )
    )
    checks.append(
        DoctorCheck(
            name=f"app:{domain}:releases",
            status="ok" if paths.releases.exists() else "warn",
            detail=str(paths.releases),
        )
    )
    checks.append(_check_latest_backup(state_path, domain))
    checks.append(_check_restore_verify_report(state_path, domain))
    timer_unit_path = unit_dir / db_backup_timer_name(domain)
    service_unit_path = unit_dir / db_backup_service_name(domain)
    if service_unit_path.exists() or timer_unit_path.exists():
        if systemd_manage:
            checks.append(_check_systemd_unit(db_backup_service_name(domain)))
            checks.append(_check_systemd_unit(db_backup_timer_name(domain)))
        else:
            checks.append(
                DoctorCheck(
                    name=f"backup-timer:{domain}",
                    status="warn",
                    detail="unit exists but systemd management is disabled",
                )
            )
    else:
        checks.append(
            DoctorCheck(
                name=f"backup-timer:{domain}",
                status="warn",
                detail="auto backup timer not configured",
            )
        )
    if paths.current.exists():
        for check_config in app_command_checks:
            checks.append(_run_app_command_check(current_path=paths.current, check_config=check_config))
    return checks


def summarize(checks: list[DoctorCheck]) -> dict:
    if any(check.status == "error" for check in checks):
        overall = "error"
    elif any(check.status == "warn" for check in checks):
        overall = "warn"
    else:
        overall = "ok"
    return {
        "overall": overall,
        "checks": [{"name": check.name, "status": check.status, "detail": check.detail} for check in checks],
        "counts": {
            "ok": len([check for check in checks if check.status == "ok"]),
            "warn": len([check for check in checks if check.status == "warn"]),
            "error": len([check for check in checks if check.status == "error"]),
        },
    }
