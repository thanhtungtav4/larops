from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from larops.config import DoctorAppCommandCheckConfig, DoctorHeartbeatCheckConfig
from larops.config import DoctorFailedJobCheckConfig, DoctorQueueBacklogCheckConfig
from larops.config import BackupOffsiteConfig
from larops.services.doctor_service import run_app_checks, summarize


class MonitorAppError(RuntimeError):
    pass


def load_monitor_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"checks": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MonitorAppError(f"Invalid app monitor state file: {path}") from exc
    if not isinstance(payload, dict):
        raise MonitorAppError(f"Invalid app monitor state payload: {path}")
    if not isinstance(payload.get("checks"), dict):
        payload["checks"] = {}
    return payload


def save_monitor_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_app_monitor(
    *,
    base_releases_path: Path,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
    app_command_checks: list[DoctorAppCommandCheckConfig],
    heartbeat_checks: list[DoctorHeartbeatCheckConfig],
    queue_backlog_checks: list[DoctorQueueBacklogCheckConfig],
    failed_job_checks: list[DoctorFailedJobCheckConfig],
    runtime_policies: dict[str, dict],
    offsite_config: BackupOffsiteConfig,
    monitor_state_file: Path,
) -> dict[str, Any]:
    report_checks = run_app_checks(
        base_releases_path=base_releases_path,
        state_path=state_path,
        domain=domain,
        unit_dir=unit_dir,
        systemd_manage=systemd_manage,
        app_command_checks=app_command_checks,
        heartbeat_checks=heartbeat_checks,
        queue_backlog_checks=queue_backlog_checks,
        failed_job_checks=failed_job_checks,
        runtime_policies=runtime_policies,
        offsite_config=offsite_config,
    )
    report = summarize(report_checks)
    previous_state = load_monitor_state(monitor_state_file)
    previous_checks = previous_state.setdefault("checks", {})
    transitions: list[dict[str, Any]] = []

    for check in report_checks:
        previous = previous_checks.get(check.name, {})
        previous_status = str(previous.get("status", "")).strip() or None
        transition = "steady"
        if check.status == "ok":
            if previous_status in {"warn", "error"}:
                transition = "recovered"
        elif previous_status != check.status:
            transition = "alert"

        previous_checks[check.name] = {
            "status": check.status,
            "detail": check.detail,
        }
        if transition != "steady":
            transitions.append(
                {
                    "name": check.name,
                    "status": check.status,
                    "detail": check.detail,
                    "previous_status": previous_status,
                    "transition": transition,
                }
            )

    previous_state["domain"] = domain
    previous_state["overall"] = report["overall"]
    save_monitor_state(monitor_state_file, previous_state)

    return {
        "domain": domain,
        "report": report,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
            }
            for check in report_checks
        ],
        "transitions": transitions,
        "monitor_state_file": str(monitor_state_file),
    }
