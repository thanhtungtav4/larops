from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.app_lifecycle import AppLifecycleError, get_app_paths, load_metadata


class RuntimeProcessError(RuntimeError):
    pass


def _sanitize_domain(domain: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", domain).strip("-")


def _runtime_dir(state_path: Path, domain: str) -> Path:
    return state_path / "runtime" / domain


def _spec_path(state_path: Path, domain: str, process_type: str) -> Path:
    return _runtime_dir(state_path, domain) / f"{process_type}.json"


def _unit_path(state_path: Path, domain: str, process_type: str) -> Path:
    return _runtime_dir(state_path, domain) / "units" / f"larops-{_sanitize_domain(domain)}-{process_type}.service"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_app_registered(base_releases_path: Path, state_path: Path, domain: str) -> dict[str, Any]:
    paths = get_app_paths(base_releases_path, state_path, domain)
    try:
        metadata = load_metadata(paths.metadata)
    except AppLifecycleError as exc:
        raise RuntimeProcessError(f"Application is not registered: {domain}") from exc
    return metadata


def _read_spec(state_path: Path, domain: str, process_type: str) -> dict[str, Any] | None:
    path = _spec_path(state_path, domain, process_type)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_spec(state_path: Path, domain: str, process_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _spec_path(state_path, domain, process_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _exec_start_command(process_type: str, app_current_path: Path, options: dict[str, Any]) -> str:
    app_path = str(app_current_path)
    if process_type == "worker":
        queue = options.get("queue", "default")
        concurrency = int(options.get("concurrency", 1))
        tries = int(options.get("tries", 3))
        timeout = int(options.get("timeout", 90))
        return (
            f"bash -lc \"cd {app_path} && php artisan queue:work"
            f" --queue={queue}"
            f" --tries={tries}"
            f" --timeout={timeout}"
            " --sleep=1 --max-jobs=0 --max-time=0 --verbose\""
            f" # concurrency={concurrency}"
        )
    if process_type == "scheduler":
        command = options.get("command", "php artisan schedule:run")
        return f"bash -lc \"cd {app_path} && while true; do {command}; sleep 60; done\""
    if process_type == "horizon":
        return f"bash -lc \"cd {app_path} && php artisan horizon\""
    raise RuntimeProcessError(f"Unsupported process type: {process_type}")


def render_systemd_unit(domain: str, process_type: str, app_current_path: Path, options: dict[str, Any]) -> str:
    exec_start = _exec_start_command(process_type, app_current_path, options)
    safe_domain = _sanitize_domain(domain)
    description = f"LarOps {process_type} process for {safe_domain}"
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            "User=www-data",
            "Restart=always",
            "RestartSec=5",
            f"ExecStart={exec_start}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def enable_process(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    process_type: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    app_paths = get_app_paths(base_releases_path, state_path, domain)
    if not app_paths.current.exists():
        raise RuntimeProcessError(
            f"App current release is missing for {domain}. Deploy app before enabling {process_type}."
        )

    existing = _read_spec(state_path, domain, process_type) or {}
    spec = {
        **existing,
        "domain": domain,
        "process_type": process_type,
        "enabled": True,
        "autostart": True,
        "options": options,
        "updated_at": _utc_now(),
    }
    if "created_at" not in spec:
        spec["created_at"] = _utc_now()
    spec.setdefault("restart_count", 0)
    spec.setdefault("terminate_count", 0)
    spec.setdefault("run_count", 0)

    _write_spec(state_path, domain, process_type, spec)
    unit = render_systemd_unit(domain, process_type, app_paths.current, options)
    unit_path = _unit_path(state_path, domain, process_type)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit, encoding="utf-8")
    return spec


def disable_process(*, state_path: Path, domain: str, process_type: str) -> dict[str, Any]:
    existing = _read_spec(state_path, domain, process_type)
    if existing is None:
        spec = {
            "domain": domain,
            "process_type": process_type,
            "enabled": False,
            "autostart": False,
            "updated_at": _utc_now(),
            "created_at": _utc_now(),
            "restart_count": 0,
            "terminate_count": 0,
            "run_count": 0,
            "options": {},
        }
    else:
        spec = {
            **existing,
            "enabled": False,
            "autostart": False,
            "updated_at": _utc_now(),
        }
    return _write_spec(state_path, domain, process_type, spec)


def restart_process(*, state_path: Path, domain: str, process_type: str) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    if spec is None or not spec.get("enabled"):
        raise RuntimeProcessError(f"{process_type} is not enabled for {domain}.")
    spec["restart_count"] = int(spec.get("restart_count", 0)) + 1
    spec["last_restart_at"] = _utc_now()
    spec["updated_at"] = _utc_now()
    return _write_spec(state_path, domain, process_type, spec)


def terminate_process(*, state_path: Path, domain: str, process_type: str) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    if spec is None or not spec.get("enabled"):
        raise RuntimeProcessError(f"{process_type} is not enabled for {domain}.")
    spec["terminate_count"] = int(spec.get("terminate_count", 0)) + 1
    spec["last_terminate_at"] = _utc_now()
    spec["updated_at"] = _utc_now()
    return _write_spec(state_path, domain, process_type, spec)


def status_process(*, state_path: Path, domain: str, process_type: str) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    unit_path = _unit_path(state_path, domain, process_type)
    if spec is None:
        return {
            "domain": domain,
            "process_type": process_type,
            "enabled": False,
            "exists": False,
            "unit_path": str(unit_path),
        }
    return {
        **spec,
        "exists": True,
        "unit_path": str(unit_path),
    }


def run_scheduler_once(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    command: str,
    execute: bool,
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    app_paths = get_app_paths(base_releases_path, state_path, domain)
    if not app_paths.current.exists():
        raise RuntimeProcessError(f"App current release is missing for {domain}. Deploy app before run-once.")

    result: dict[str, Any] = {
        "domain": domain,
        "process_type": "scheduler",
        "command": command,
        "executed": execute,
    }

    if execute:
        try:
            completed = run_command(["bash", "-lc", f"cd {app_paths.current} && {command}"], check=True)
        except ShellCommandError as exc:
            raise RuntimeProcessError(str(exc)) from exc
        result["stdout"] = completed.stdout.strip()
        result["stderr"] = completed.stderr.strip()

    existing = _read_spec(state_path, domain, "scheduler") or {
        "domain": domain,
        "process_type": "scheduler",
        "enabled": False,
        "autostart": False,
        "created_at": _utc_now(),
        "options": {"command": command},
    }
    existing["run_count"] = int(existing.get("run_count", 0)) + 1
    existing["last_run_at"] = _utc_now()
    existing["updated_at"] = _utc_now()
    _write_spec(state_path, domain, "scheduler", existing)
    return result
