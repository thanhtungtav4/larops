from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from larops.core.shell import ShellCommandError, run_command
from larops.services.stack_service import StackServiceError, detect_stack_platform


class MonitorServiceWatchError(RuntimeError):
    pass


_SERVICE_PROFILES = {
    "laravel-host": ["nginx", "php-fpm", "mariadb", "redis"],
    "laravel-postgres-host": ["nginx", "php-fpm", "postgresql", "redis"],
}

_SERVICE_ALIASES = {
    "sql": "mariadb",
    "mysql": "mariadb",
    "mariadb": "mariadb",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "nginx": "nginx",
}

_PHP_FPM_CANDIDATES = [
    "php8.4-fpm",
    "php8.3-fpm",
    "php8.2-fpm",
    "php8.1-fpm",
    "php8.0-fpm",
    "php-fpm",
]

_HEALTHY_ACTIVE_STATES = {"active", "activating", "reloading"}
_KNOWN_ACTIVE_STATES = _HEALTHY_ACTIVE_STATES | {"inactive", "failed", "deactivating"}
_KNOWN_ENABLED_STATES = {"enabled", "disabled", "static", "indirect", "generated", "masked"}


def supported_service_profiles() -> list[str]:
    return sorted(_SERVICE_PROFILES)


def _systemctl_text(args: list[str]) -> str:
    try:
        completed = run_command(["systemctl", *args], check=False)
    except FileNotFoundError:
        return ""
    return (completed.stdout or completed.stderr or "").strip()


def _service_exists(service: str) -> bool:
    active = _systemctl_text(["is-active", service])
    enabled = _systemctl_text(["is-enabled", service])
    return active in _KNOWN_ACTIVE_STATES or enabled in _KNOWN_ENABLED_STATES


def is_service_healthy(active_state: str) -> bool:
    return active_state.strip().lower() in _HEALTHY_ACTIVE_STATES


def _platform_family() -> str | None:
    try:
        return detect_stack_platform().family
    except StackServiceError:
        return None


def _resolve_php_fpm_service() -> str:
    for candidate in _PHP_FPM_CANDIDATES:
        if _service_exists(candidate):
            return candidate
    return "php-fpm" if _platform_family() == "el9" else "php8.3-fpm"


def _resolve_redis_service(*, requested_name: str | None = None) -> str:
    candidates = []
    if requested_name:
        candidates.append(requested_name)
    candidates.extend(["redis-server", "redis"])
    for candidate in dict.fromkeys(candidates):
        if _service_exists(candidate):
            return candidate
    return "redis" if _platform_family() == "el9" else "redis-server"


def normalize_service_name(name: str) -> str:
    raw = name.strip()
    if not raw:
        raise MonitorServiceWatchError("Service name cannot be empty.")
    lowered = raw.lower()
    if lowered in {"php", "php-fpm", "phpfpm"}:
        return _resolve_php_fpm_service()
    if lowered in {"redis", "redis-server"}:
        return _resolve_redis_service(requested_name=lowered)
    normalized = _SERVICE_ALIASES.get(lowered, raw)
    return normalized


def resolve_service_targets(*, services: list[str], profiles: list[str]) -> list[str]:
    requested: list[str] = []
    for profile in profiles:
        normalized_profile = profile.strip().lower()
        if not normalized_profile:
            raise MonitorServiceWatchError("Profile name cannot be empty.")
        if normalized_profile not in _SERVICE_PROFILES:
            supported = ", ".join(supported_service_profiles())
            raise MonitorServiceWatchError(
                f"Unsupported service profile: {profile}. Supported profiles: {supported}"
            )
        requested.extend(_SERVICE_PROFILES[normalized_profile])
    requested.extend(services)
    normalized_services = list(dict.fromkeys(normalize_service_name(service) for service in requested))
    if not normalized_services:
        raise MonitorServiceWatchError("At least one --service or --profile is required.")
    return normalized_services


def load_watch_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"services": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MonitorServiceWatchError(f"Invalid monitor service state file: {path}") from exc
    if not isinstance(payload, dict):
        raise MonitorServiceWatchError(f"Invalid monitor service state payload: {path}")
    if not isinstance(payload.get("services"), dict):
        payload["services"] = {}
    return payload


def save_watch_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _systemctl_state(service: str) -> dict[str, str]:
    active = run_command(["systemctl", "is-active", service], check=False)
    enabled = run_command(["systemctl", "is-enabled", service], check=False)
    return {
        "active": (active.stdout or active.stderr or "").strip(),
        "enabled": (enabled.stdout or enabled.stderr or "").strip(),
    }


def watch_services(
    *,
    services: list[str],
    profiles: list[str] | None,
    state_file: Path,
    restart_down_services: bool,
    restart_cooldown_seconds: int,
) -> dict[str, Any]:
    if restart_cooldown_seconds < 0:
        raise MonitorServiceWatchError("--restart-cooldown must be >= 0.")
    normalized_services = resolve_service_targets(services=services, profiles=profiles or [])

    state = load_watch_state(state_file)
    service_state: dict[str, Any] = state.setdefault("services", {})
    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []

    for service in normalized_services:
        previous = service_state.get(service, {})
        before = _systemctl_state(service)
        before_active = before["active"]
        action = "none"
        transition = "steady"
        after = dict(before)
        was_active = previous.get("active") == "active"
        last_restart_attempt_at = None
        last_restart_at_raw = previous.get("last_restart_attempt_at")
        if last_restart_at_raw:
            try:
                last_restart_attempt_at = datetime.fromisoformat(str(last_restart_at_raw))
                if last_restart_attempt_at.tzinfo is None:
                    last_restart_attempt_at = last_restart_attempt_at.replace(tzinfo=UTC)
                else:
                    last_restart_attempt_at = last_restart_attempt_at.astimezone(UTC)
            except ValueError:
                last_restart_attempt_at = None

        if not is_service_healthy(before_active):
            should_restart = restart_down_services
            if should_restart and last_restart_attempt_at is not None:
                elapsed = (now - last_restart_attempt_at).total_seconds()
                if elapsed < restart_cooldown_seconds:
                    should_restart = False
                    action = "cooldown"

            if should_restart:
                action = "restart"
                try:
                    run_command(["systemctl", "restart", service], check=True)
                except ShellCommandError:
                    pass
                after = _systemctl_state(service)
                if is_service_healthy(after["active"]):
                    transition = "restarted"
                else:
                    transition = "restart_failed"
                previous["last_restart_attempt_at"] = now.isoformat()
            elif was_active:
                transition = "down"
            elif action == "cooldown":
                transition = "steady"
            else:
                transition = "steady"
        elif previous.get("active") and not is_service_healthy(str(previous.get("active"))):
            transition = "recovered"

        previous["active"] = after["active"]
        previous["enabled"] = after["enabled"]
        previous["last_checked_at"] = now.isoformat()
        previous["last_transition"] = transition
        service_state[service] = previous

        results.append(
            {
                "service": service,
                "before": before,
                "after": after,
                "action": action,
                "transition": transition,
            }
        )

    save_watch_state(state_file, state)
    return {
        "checked_at": now.isoformat(),
        "services": results,
        "restart_down_services": restart_down_services,
        "restart_cooldown_seconds": restart_cooldown_seconds,
    }
