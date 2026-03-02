from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from larops.services.app_lifecycle import get_app_paths


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


def run_host_checks(*, state_path: Path, events_path: Path, quick: bool) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [
        _check_path_writable(state_path),
        _check_path_writable(events_path.parent),
        _check_disk(),
    ]
    command_set = ["python3", "bash", "openssl"]
    if not quick:
        command_set += ["nginx", "php", "mysqldump", "certbot"]
    for command in command_set:
        checks.append(_check_command(command))
    return checks


def run_app_checks(*, base_releases_path: Path, state_path: Path, domain: str) -> list[DoctorCheck]:
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

