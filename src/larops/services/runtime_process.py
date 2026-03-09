from __future__ import annotations

import json
import re
import shlex
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.app_lifecycle import AppLifecycleError, get_app_paths, load_metadata
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux


class RuntimeProcessError(RuntimeError):
    pass


_DEFAULT_POLICY: dict[str, Any] = {
    "max_restarts": 5,
    "window_seconds": 300,
    "cooldown_seconds": 120,
    "auto_heal": True,
}


def _sanitize_domain(domain: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", domain).strip("-")


def _runtime_dir(state_path: Path, domain: str) -> Path:
    return state_path / "runtime" / domain


def _spec_path(state_path: Path, domain: str, process_type: str) -> Path:
    return _runtime_dir(state_path, domain) / f"{process_type}.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_utc(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    src = policy or {}

    def _positive_int(key: str, default: int) -> int:
        try:
            value = int(src.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(1, value)

    return {
        "max_restarts": _positive_int("max_restarts", int(_DEFAULT_POLICY["max_restarts"])),
        "window_seconds": _positive_int("window_seconds", int(_DEFAULT_POLICY["window_seconds"])),
        "cooldown_seconds": _positive_int("cooldown_seconds", int(_DEFAULT_POLICY["cooldown_seconds"])),
        "auto_heal": bool(src.get("auto_heal", _DEFAULT_POLICY["auto_heal"])),
    }


def _prune_restart_history(spec: dict[str, Any], *, now: datetime, window_seconds: int) -> list[datetime]:
    history: list[datetime] = []
    raw_items = spec.get("restart_history", [])
    if not isinstance(raw_items, list):
        raw_items = []
    for raw in raw_items:
        parsed = _parse_iso_utc(str(raw))
        if parsed is None:
            continue
        if (now - parsed).total_seconds() <= window_seconds:
            history.append(parsed)
    return history


def _check_restart_policy(spec: dict[str, Any], policy: dict[str, Any], *, now: datetime) -> list[datetime]:
    cooldown_until_raw = spec.get("cooldown_until")
    if cooldown_until_raw:
        cooldown_until = _parse_iso_utc(str(cooldown_until_raw))
        if cooldown_until is not None and now < cooldown_until:
            raise RuntimeProcessError(f"Restart cooldown active until {cooldown_until.isoformat()}.")

    history = _prune_restart_history(spec, now=now, window_seconds=int(policy["window_seconds"]))
    if len(history) >= int(policy["max_restarts"]):
        raise RuntimeProcessError(
            "Restart rate limit exceeded: "
            f"{len(history)} restarts in {policy['window_seconds']}s (max {policy['max_restarts']})."
        )
    return history


def _record_restart(
    spec: dict[str, Any],
    policy: dict[str, Any],
    history: list[datetime],
    *,
    now: datetime,
    source: str,
) -> None:
    history.append(now)
    spec["restart_history"] = [item.isoformat() for item in history]
    if len(history) >= int(policy["max_restarts"]):
        cooldown_until = now + timedelta(seconds=int(policy["cooldown_seconds"]))
        spec["cooldown_until"] = cooldown_until.isoformat()
    else:
        spec.pop("cooldown_until", None)
    spec["restart_count"] = int(spec.get("restart_count", 0)) + 1
    spec["last_restart_at"] = now.isoformat()
    spec["last_restart_source"] = source
    spec["updated_at"] = _utc_now()


def _shell_double_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _worker_replicas(options: dict[str, Any]) -> int:
    try:
        replicas = int(options.get("concurrency", 1))
    except (TypeError, ValueError):
        replicas = 1
    return max(1, replicas)


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
    app_path_arg = _shell_double_quote(app_path)
    if process_type == "worker":
        queue = options.get("queue", "default")
        tries = int(options.get("tries", 3))
        timeout = int(options.get("timeout", 90))
        max_jobs = max(1, int(options.get("max_jobs", 500)))
        max_time = max(1, int(options.get("max_time", 3600)))
        worker_cmd = (
            "php artisan queue:work"
            f" --queue={shlex.quote(str(queue))}"
            f" --tries={tries}"
            f" --timeout={timeout}"
            f" --max-jobs={max_jobs}"
            f" --max-time={max_time}"
            " --sleep=1 --verbose"
        )
        script = f"cd {app_path_arg} && exec {worker_cmd}"
        return f"bash -lc {shlex.quote(script)}"
    if process_type == "scheduler":
        command = str(options.get("command", "php artisan schedule:work")).strip()
        if not command:
            raise RuntimeProcessError("Scheduler command cannot be empty.")
        script = f"cd {app_path_arg} && exec {command}"
        return f"bash -lc {shlex.quote(script)}"
    if process_type == "horizon":
        script = f"cd {app_path_arg} && exec php artisan horizon"
        return f"bash -lc {shlex.quote(script)}"
    raise RuntimeProcessError(f"Unsupported process type: {process_type}")


def render_systemd_unit(
    domain: str,
    process_type: str,
    app_current_path: Path,
    options: dict[str, Any],
    *,
    user: str,
) -> str:
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
            f"User={user}",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=read-only",
            "ProtectControlGroups=true",
            "ProtectKernelModules=true",
            "ProtectKernelTunables=true",
            "RestrictSUIDSGID=true",
            "LockPersonality=true",
            "RestrictRealtime=true",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "UMask=0027",
            "Restart=always",
            "RestartSec=5",
            f"ExecStart={exec_start}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


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


def _service_names(domain: str, process_type: str, options: dict[str, Any] | None = None, spec: dict[str, Any] | None = None) -> list[str]:
    if spec and isinstance(spec.get("service_names"), list):
        return [str(item) for item in spec["service_names"] if str(item).strip()]
    if process_type == "worker":
        replicas = _worker_replicas(spec.get("options", {}) if spec else options or {})
        if replicas > 1:
            return [service_name(domain, process_type, replica=index) for index in range(1, replicas + 1)]
    return [service_name(domain, process_type)]


def service_name(domain: str, process_type: str, *, replica: int | None = None) -> str:
    base = f"larops-{_sanitize_domain(domain)}-{process_type}"
    if replica is not None:
        return f"{base}-{replica}.service"
    return f"{base}.service"


def _unit_paths(unit_dir: Path, domain: str, process_type: str, options: dict[str, Any] | None = None, spec: dict[str, Any] | None = None) -> list[Path]:
    return [unit_dir / name for name in _service_names(domain, process_type, options=options, spec=spec)]


def _relabel_systemd_units(unit_paths: list[Path]) -> None:
    try:
        relabel_managed_paths_for_selinux(
            unit_paths,
            run_command=run_command,
            which=shutil.which,
            roots=[Path("/etc/systemd/system"), Path("/usr/lib/systemd/system")],
        )
    except SelinuxServiceError as exc:
        raise RuntimeProcessError(str(exc)) from exc


def enable_process(
    *,
    base_releases_path: Path,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    service_user: str,
    domain: str,
    process_type: str,
    options: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    app_paths = get_app_paths(base_releases_path, state_path, domain)
    if not app_paths.current.exists():
        raise RuntimeProcessError(
            f"App current release is missing for {domain}. Deploy app before enabling {process_type}."
        )

    existing = _read_spec(state_path, domain, process_type) or {}
    service_names = _service_names(domain, process_type, options=options)
    primary_service = service_names[0]
    normalized_policy = _normalize_policy(policy or existing.get("policy"))
    spec = {
        **existing,
        "domain": domain,
        "process_type": process_type,
        "enabled": True,
        "autostart": True,
        "service_name": primary_service,
        "service_names": service_names,
        "systemd_managed": systemd_manage,
        "options": options,
        "policy": normalized_policy,
        "replicas": len(service_names),
        "updated_at": _utc_now(),
    }
    if "created_at" not in spec:
        spec["created_at"] = _utc_now()
    spec.setdefault("restart_count", 0)
    spec.setdefault("restart_history", [])
    spec.setdefault("cooldown_until", None)
    spec.setdefault("terminate_count", 0)
    spec.setdefault("run_count", 0)
    spec.setdefault("auto_heal_count", 0)

    _write_spec(state_path, domain, process_type, spec)
    unit_paths = _unit_paths(unit_dir, domain, process_type, options=options)
    for unit_path in unit_paths:
        unit = render_systemd_unit(domain, process_type, app_paths.current, options, user=service_user)
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit, encoding="utf-8")
    _relabel_systemd_units(unit_paths)

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            for service in service_names:
                _run_systemctl(["enable", "--now", service], check=True)
        except ShellCommandError as exc:
            raise RuntimeProcessError(str(exc)) from exc

    return spec


def disable_process(
    *,
    base_releases_path: Path,
    state_path: Path,
    systemd_manage: bool,
    domain: str,
    process_type: str,
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    existing = _read_spec(state_path, domain, process_type)
    service_names = _service_names(domain, process_type, spec=existing)
    primary_service = service_names[0]
    if existing is None:
        spec = {
            "domain": domain,
            "process_type": process_type,
            "enabled": False,
            "autostart": False,
            "service_name": primary_service,
            "service_names": service_names,
            "updated_at": _utc_now(),
            "created_at": _utc_now(),
            "restart_count": 0,
            "terminate_count": 0,
            "run_count": 0,
            "options": {},
            "replicas": len(service_names),
        }
    else:
        spec = {
            **existing,
            "enabled": False,
            "autostart": False,
            "updated_at": _utc_now(),
        }

    if systemd_manage:
        for service in spec.get("service_names", service_names):
            run_command(["systemctl", "disable", "--now", str(service)], check=False)
    return _write_spec(state_path, domain, process_type, spec)


def restart_process(
    *,
    state_path: Path,
    systemd_manage: bool,
    domain: str,
    process_type: str,
    policy: dict[str, Any] | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    if spec is None or not spec.get("enabled"):
        raise RuntimeProcessError(f"{process_type} is not enabled for {domain}.")

    normalized_policy = _normalize_policy(policy or spec.get("policy"))
    now = datetime.now(UTC)
    history = _check_restart_policy(spec, normalized_policy, now=now)

    if systemd_manage:
        services = _service_names(domain, process_type, spec=spec)
        try:
            for service in services:
                _run_systemctl(["restart", service], check=True)
        except ShellCommandError as exc:
            raise RuntimeProcessError(str(exc)) from exc

    spec["policy"] = normalized_policy
    _record_restart(spec, normalized_policy, history, now=now, source=source)
    return _write_spec(state_path, domain, process_type, spec)


def reconcile_process(
    *,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
    process_type: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    if spec is None or not spec.get("enabled"):
        raise RuntimeProcessError(f"{process_type} is not enabled for {domain}.")

    before = status_process(
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=systemd_manage,
        domain=domain,
        process_type=process_type,
        policy=policy or spec.get("policy"),
    )
    if not systemd_manage:
        return {
            "domain": domain,
            "process_type": process_type,
            "action": "skipped",
            "reason": "systemd management disabled",
            "before": before,
            "after": before,
        }
    if before["systemd"]["active"] == "active":
        return {
            "domain": domain,
            "process_type": process_type,
            "action": "noop",
            "before": before,
            "after": before,
        }

    updated_spec = restart_process(
        state_path=state_path,
        systemd_manage=systemd_manage,
        domain=domain,
        process_type=process_type,
        policy=policy or spec.get("policy"),
        source="reconcile",
    )
    after = status_process(
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=systemd_manage,
        domain=domain,
        process_type=process_type,
        policy=policy or updated_spec.get("policy"),
    )
    return {
        "domain": domain,
        "process_type": process_type,
        "action": "restart",
        "before": before,
        "after": after,
        "spec": updated_spec,
    }


def terminate_process(
    *,
    state_path: Path,
    systemd_manage: bool,
    domain: str,
    process_type: str,
) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    if spec is None or not spec.get("enabled"):
        raise RuntimeProcessError(f"{process_type} is not enabled for {domain}.")

    if systemd_manage:
        service = spec.get("service_name") or service_name(domain, process_type)
        try:
            _run_systemctl(["kill", "-s", "SIGTERM", service], check=True)
        except ShellCommandError as exc:
            raise RuntimeProcessError(str(exc)) from exc

    spec["terminate_count"] = int(spec.get("terminate_count", 0)) + 1
    spec["last_terminate_at"] = _utc_now()
    spec["updated_at"] = _utc_now()
    return _write_spec(state_path, domain, process_type, spec)


def status_process(
    *,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
    process_type: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = _read_spec(state_path, domain, process_type)
    service = service_name(domain, process_type)
    normalized_policy = _normalize_policy(policy)
    if spec is None:
        unit_paths = _unit_paths(unit_dir, domain, process_type)
        return {
            "domain": domain,
            "process_type": process_type,
            "enabled": False,
            "exists": False,
            "service_name": service,
            "service_names": [service],
            "unit_path": str(unit_paths[0]),
            "unit_paths": [str(path) for path in unit_paths],
            "policy": normalized_policy,
            "systemd": _systemd_status(service) if systemd_manage else {"active": "unmanaged", "enabled": "unmanaged"},
        }

    policy_payload = _normalize_policy(policy or spec.get("policy"))
    spec["policy"] = policy_payload
    services = _service_names(domain, process_type, spec=spec)
    unit_paths = _unit_paths(unit_dir, domain, process_type, spec=spec)
    systemd_services = []
    if systemd_manage:
        for service_name_value in services:
            systemd_services.append({"service_name": service_name_value, **_systemd_status(service_name_value)})
    else:
        for service_name_value in services:
            systemd_services.append({"service_name": service_name_value, "active": "unmanaged", "enabled": "unmanaged"})

    if systemd_manage:
        active_states = {item["active"] for item in systemd_services}
        enabled_states = {item["enabled"] for item in systemd_services}
        overall_active = "active" if active_states <= {"active"} else "degraded" if "active" in active_states else next(iter(active_states), "unknown")
        overall_enabled = "enabled" if enabled_states <= {"enabled"} else "mixed" if "enabled" in enabled_states else next(iter(enabled_states), "unknown")
    else:
        overall_active = "unmanaged"
        overall_enabled = "unmanaged"

    auto_heal_status = "disabled"
    if bool(policy_payload["auto_heal"]) and spec.get("enabled"):
        auto_heal_status = "healthy" if overall_active == "active" else "degraded"
    auto_heal: dict[str, Any] = {
        "enabled": bool(policy_payload["auto_heal"]),
        "attempted": False,
        "status": auto_heal_status,
    }

    return {
        **spec,
        "exists": True,
        "service_name": service,
        "service_names": services,
        "unit_path": str(unit_paths[0]),
        "unit_paths": [str(path) for path in unit_paths],
        "policy": policy_payload,
        "auto_heal": auto_heal,
        "systemd": {"active": overall_active, "enabled": overall_enabled, "services": systemd_services},
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
            script = f"cd {_shell_double_quote(str(app_paths.current))} && {command}"
            completed = run_command(["bash", "-lc", script], check=True)
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
