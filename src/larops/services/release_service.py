from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from larops.config import DeployConfig
from larops.core.shell import ShellCommandError, run_command
from larops.services.app_lifecycle import AppLifecycleError, AppPaths, activate_release, copy_release


class ReleaseServiceError(RuntimeError):
    pass


_RELEASE_MANIFEST = ".larops-deploy-manifest.json"


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _ensure_shared_dir(paths: AppPaths, release_dir: Path, relative_path: str) -> None:
    rel = Path(relative_path)
    shared_target = paths.shared / rel
    shared_target.mkdir(parents=True, exist_ok=True)
    release_target = release_dir / rel
    release_target.parent.mkdir(parents=True, exist_ok=True)
    if release_target.exists() or release_target.is_symlink():
        _remove_path(release_target)
    release_target.symlink_to(shared_target)


def _ensure_shared_file(paths: AppPaths, release_dir: Path, relative_path: str) -> None:
    rel = Path(relative_path)
    shared_target = paths.shared / rel
    shared_target.parent.mkdir(parents=True, exist_ok=True)
    release_target = release_dir / rel
    if not shared_target.exists():
        if release_target.is_file():
            shared_target.write_bytes(release_target.read_bytes())
        else:
            shared_target.touch()
    release_target.parent.mkdir(parents=True, exist_ok=True)
    if release_target.exists() or release_target.is_symlink():
        _remove_path(release_target)
    release_target.symlink_to(shared_target)


def prepare_release_candidate(
    *,
    paths: AppPaths,
    source_path: Path,
    ref: str,
    shared_dirs: list[str],
    shared_files: list[str],
) -> tuple[str, Path]:
    release_id, release_dir = copy_release(paths, source_path, ref)
    for relative_path in shared_dirs:
        cleaned = relative_path.strip()
        if cleaned:
            _ensure_shared_dir(paths, release_dir, cleaned)
    for relative_path in shared_files:
        cleaned = relative_path.strip()
        if cleaned:
            _ensure_shared_file(paths, release_dir, cleaned)
    return release_id, release_dir


def _normalized_timeout(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return None
    return timeout_seconds if timeout_seconds > 0 else None


def _composer_install_command(config: DeployConfig) -> str:
    flags = ["install"]
    if config.composer_no_dev:
        flags.append("--no-dev")
    if config.composer_optimize_autoloader:
        flags.append("--optimize-autoloader")
    return " ".join([config.composer_binary, *flags])


def build_deploy_phase_commands(config: DeployConfig) -> dict[str, list[str]]:
    build_commands: list[str] = []
    if config.composer_install:
        build_commands.append(_composer_install_command(config))
    build_commands.extend([command.strip() for command in config.asset_commands if command.strip()])

    pre_activate_commands = [command.strip() for command in config.pre_activate_commands if command.strip()]
    if config.migrate_enabled and config.migrate_phase.strip().lower() == "pre-activate":
        pre_activate_commands.append(config.migrate_command.strip())

    post_activate_commands = [command.strip() for command in config.post_activate_commands if command.strip()]
    if config.migrate_enabled and config.migrate_phase.strip().lower() == "post-activate":
        post_activate_commands.append(config.migrate_command.strip())
    if config.cache_warm_enabled:
        post_activate_commands.extend([command.strip() for command in config.cache_warm_commands if command.strip()])

    return {
        "build": build_commands,
        "pre_activate": pre_activate_commands,
        "post_activate": post_activate_commands,
        "verify": [command.strip() for command in config.verify_commands if command.strip()],
    }


def build_rollback_phase_commands(config: DeployConfig) -> dict[str, list[str]]:
    post_activate_commands = [command.strip() for command in config.post_activate_commands if command.strip()]
    if config.cache_warm_enabled:
        post_activate_commands.extend([command.strip() for command in config.cache_warm_commands if command.strip()])
    return {
        "post_activate": post_activate_commands,
        "verify": [command.strip() for command in config.verify_commands if command.strip()],
    }


def run_release_commands(
    *,
    workdir: Path,
    phase: str,
    commands: list[str],
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for command in commands:
        raw = command.strip()
        if not raw:
            continue
        script = f'cd "{str(workdir)}" && {raw}'
        try:
            started_at = datetime.now(UTC)
            completed = run_command(
                ["bash", "-lc", script],
                check=True,
                timeout_seconds=_normalized_timeout(timeout_seconds),
            )
        except ShellCommandError as exc:
            raise ReleaseServiceError(f"Release phase '{phase}' failed for command '{raw}': {exc}") from exc
        reports.append(
            {
                "phase": phase,
                "command": raw,
                "started_at": started_at.isoformat(),
                "stdout": (completed.stdout or "").strip(),
                "stderr": (completed.stderr or "").strip(),
            }
        )
    return reports


def release_manifest_path(release_dir: Path) -> Path:
    return release_dir / _RELEASE_MANIFEST


def write_release_manifest(release_dir: Path, payload: dict[str, Any]) -> Path:
    path = release_manifest_path(release_dir)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def activate_release_candidate(*, paths: AppPaths, release_dir: Path) -> None:
    try:
        activate_release(paths, release_dir)
    except AppLifecycleError as exc:
        raise ReleaseServiceError(str(exc)) from exc


def run_http_health_check(
    *,
    domain: str,
    path: str,
    enabled: bool,
    scheme: str,
    host: str,
    timeout_seconds: int,
    retries: int,
    retry_delay_seconds: int,
    expected_status: int,
    use_domain_host_header: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "checked": False, "status": "skipped"}

    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{scheme}://{host}{normalized_path}"
    last_error = ""
    last_status = 0
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, method="GET")
        if use_domain_host_header:
            request.add_header("Host", domain)
        try:
            with urllib.request.urlopen(request, timeout=max(1, timeout_seconds)) as response:
                last_status = int(response.status)
                if last_status == expected_status:
                    return {
                        "enabled": True,
                        "checked": True,
                        "status": "ok",
                        "url": url,
                        "attempt": attempt,
                        "http_status": last_status,
                    }
                last_error = f"unexpected status {last_status}"
        except urllib.error.HTTPError as exc:
            last_status = int(exc.code)
            last_error = f"unexpected status {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
        if attempt < attempts:
            time.sleep(max(0, retry_delay_seconds))
    return {
        "enabled": True,
        "checked": True,
        "status": "failed",
        "url": url,
        "http_status": last_status,
        "detail": last_error,
    }


def refresh_runtime_after_activate(
    *,
    state_path: Path,
    current_path: Path,
    domain: str,
    strategy: str,
    systemd_manage: bool,
) -> dict[str, Any]:
    normalized = strategy.strip().lower() if strategy else "none"
    if normalized not in {"none", "queue-restart", "restart-enabled"}:
        raise ReleaseServiceError(f"Unsupported runtime refresh strategy: {strategy}")

    result: dict[str, Any] = {
        "strategy": normalized,
        "queue_restart": "skipped",
        "services_restarted": [],
    }
    if normalized == "none":
        return result

    script = f'cd "{str(current_path)}" && php artisan queue:restart'
    try:
        run_command(["bash", "-lc", script], check=True)
        result["queue_restart"] = "ok"
    except ShellCommandError as exc:
        raise ReleaseServiceError(str(exc)) from exc

    if normalized != "restart-enabled":
        return result

    runtime_dir = state_path / "runtime" / domain
    if not runtime_dir.exists():
        return result
    if not systemd_manage:
        result["services_restarted"] = []
        return result

    for spec_path in sorted(runtime_dir.glob("*.json")):
        payload = None
        try:
            import json

            payload = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ReleaseServiceError(f"Invalid runtime spec: {spec_path}: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("enabled"):
            continue
        services = payload.get("service_names") or ([payload.get("service_name")] if payload.get("service_name") else [])
        for service in services:
            try:
                run_command(["systemctl", "restart", str(service)], check=True)
            except ShellCommandError as exc:
                raise ReleaseServiceError(str(exc)) from exc
            result["services_restarted"].append(str(service))
    return result


def remove_release_dir(release_dir: Path) -> None:
    if release_dir.exists():
        shutil.rmtree(release_dir, ignore_errors=True)
