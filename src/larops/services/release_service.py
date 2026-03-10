from __future__ import annotations

import json
import os
import re
import shutil
import ssl
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
_LARAVEL_RUNTIME_DIRS = (
    "storage/app/public",
    "storage/framework/cache/data",
    "storage/framework/sessions",
    "storage/framework/views",
    "storage/logs",
    "bootstrap/cache",
)
_VITE_CONFIG_FILES = (
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "vite.config.cjs",
)
_SUPPORTED_AUTO_FRONTEND_PACKAGE_MANAGER = "npm"
_FRONTEND_BUILD_HINT_RE = re.compile(
    r"(?:\b(?:npm|pnpm|yarn|bun)\b.*\bbuild\b)|(?:\bvite\b.*\bbuild\b)|build-assets|asset-build|frontend-build"
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


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


def _ensure_laravel_runtime_paths(release_dir: Path) -> None:
    if not (release_dir / "artisan").exists():
        return
    for relative_path in _LARAVEL_RUNTIME_DIRS:
        (release_dir / relative_path).mkdir(parents=True, exist_ok=True)


def _clear_laravel_bootstrap_cache(release_dir: Path) -> None:
    if not (release_dir / "artisan").exists():
        return
    cache_dir = release_dir / "bootstrap" / "cache"
    if not cache_dir.exists():
        return
    for cache_file in cache_dir.glob("*.php"):
        if cache_file.is_file() or cache_file.is_symlink():
            cache_file.unlink(missing_ok=True)


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
    _ensure_laravel_runtime_paths(release_dir)
    _clear_laravel_bootstrap_cache(release_dir)
    return release_id, release_dir


def _normalized_timeout(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return None
    return timeout_seconds if timeout_seconds > 0 else None


def _composer_install_command(config: DeployConfig) -> str:
    flags = ["install", "--no-interaction", "--no-progress", "--no-scripts"]
    if config.composer_no_dev:
        flags.append("--no-dev")
    if config.composer_optimize_autoloader:
        flags.append("--optimize-autoloader")
    return " ".join(["COMPOSER_ALLOW_SUPERUSER=1", config.composer_binary, *flags])


def _npm_install_command(release_dir: Path) -> str:
    if (release_dir / "package-lock.json").exists() or (release_dir / "npm-shrinkwrap.json").exists():
        return "npm ci --no-audit --no-fund"
    return "npm install --no-audit --no-fund"


def _binary_available(binary: str) -> bool:
    normalized = binary.strip()
    if not normalized:
        return False
    if "/" in normalized:
        path = Path(normalized)
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(normalized) is not None


def _read_package_json(release_dir: Path) -> dict[str, Any] | None:
    package_json = release_dir / "package.json"
    if not package_json.exists():
        return None
    return json.loads(package_json.read_text(encoding="utf-8"))


def _detect_frontend_package_manager(release_dir: Path, package_json: dict[str, Any] | None) -> str:
    package_manager_raw = ""
    if isinstance(package_json, dict):
        package_manager_raw = str(package_json.get("packageManager") or "").strip().lower()
    if package_manager_raw:
        return package_manager_raw.split("@", 1)[0]
    if (release_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (release_dir / "yarn.lock").exists():
        return "yarn"
    if (release_dir / "bun.lockb").exists() or (release_dir / "bun.lock").exists():
        return "bun"
    return "npm"


def _should_auto_build_frontend(release_dir: Path) -> bool:
    if not (release_dir / "package.json").exists():
        return False
    if not any((release_dir / config_name).exists() for config_name in _VITE_CONFIG_FILES):
        return False
    return not (release_dir / "public" / "build" / "manifest.json").exists()


def _has_explicit_frontend_build(commands: list[str]) -> bool:
    normalized = [command.strip().lower() for command in commands if command.strip()]
    for command in normalized:
        if _FRONTEND_BUILD_HINT_RE.search(command):
            return True
    return False


def _parse_semver_tuple(raw: str) -> tuple[int, int, int]:
    cleaned = raw.strip().lstrip("v")
    parts = cleaned.split(".")
    numbers: list[int] = []
    for part in parts[:3]:
        match = re.match(r"(\d+)", part)
        if not match:
            break
        numbers.append(int(match.group(1)))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def _compare_semver(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _token_satisfies_semver(version: tuple[int, int, int], token: str) -> bool:
    normalized = token.strip()
    if not normalized:
        return True
    if normalized.startswith("^"):
        floor = _parse_semver_tuple(normalized[1:])
        ceil = (floor[0] + 1, 0, 0)
        return _compare_semver(version, floor) >= 0 and _compare_semver(version, ceil) < 0
    if normalized.startswith("~"):
        floor = _parse_semver_tuple(normalized[1:])
        ceil = (floor[0], floor[1] + 1, 0)
        return _compare_semver(version, floor) >= 0 and _compare_semver(version, ceil) < 0
    for operator in (">=", "<=", ">", "<", "="):
        if normalized.startswith(operator):
            target = _parse_semver_tuple(normalized[len(operator) :])
            comparison = _compare_semver(version, target)
            if operator == ">=":
                return comparison >= 0
            if operator == "<=":
                return comparison <= 0
            if operator == ">":
                return comparison > 0
            if operator == "<":
                return comparison < 0
            return comparison == 0
    wildcard = normalized.replace("*", "x")
    if "x" in wildcard:
        version_parts = normalized.split(".")
        actual_parts = [version[0], version[1], version[2]]
        for index, part in enumerate(version_parts[:3]):
            cleaned = part.strip().lower()
            if cleaned in {"x", "*"} or cleaned == "":
                return True
            if actual_parts[index] != int(cleaned):
                return False
        return True
    target = _parse_semver_tuple(normalized)
    if normalized.count(".") < 2:
        prefix = tuple(int(part) for part in normalized.split(".") if part)
        return version[: len(prefix)] == prefix
    return version == target


def _node_version_satisfies(version_raw: str, requirement: str) -> bool:
    requirement_text = requirement.strip()
    if not requirement_text:
        return True
    version = _parse_semver_tuple(version_raw)
    for clause in requirement_text.split("||"):
        tokens = [token for token in clause.strip().split() if token]
        if tokens and all(_token_satisfies_semver(version, token) for token in tokens):
            return True
    return False


def validate_release_build_requirements_for_release(
    *,
    config: DeployConfig,
    release_dir: Path,
    commands: list[str],
) -> None:
    composer_install_command = _composer_install_command(config)
    if any(command.strip() == composer_install_command for command in commands):
        if not _binary_available(config.composer_binary):
            raise ReleaseServiceError(
                f"Configured composer_binary is unavailable: {config.composer_binary}. "
                "Install Composer on the host or set deploy.composer_binary to the correct absolute path."
            )

    if not _should_auto_build_frontend(release_dir):
        return
    if _has_explicit_frontend_build(commands):
        return
    package_json = _read_package_json(release_dir)
    package_manager = _detect_frontend_package_manager(release_dir, package_json)
    if package_manager != _SUPPORTED_AUTO_FRONTEND_PACKAGE_MANAGER:
        raise ReleaseServiceError(
            "Frontend auto-build currently supports npm-managed projects only. "
            f"Detected package manager: {package_manager}. Configure deploy.asset_commands explicitly."
        )
    if not _binary_available("npm"):
        raise ReleaseServiceError(
            "npm is required for frontend auto-build but is unavailable. "
            "Install npm/nodejs on the host or configure deploy.asset_commands explicitly."
        )
    node_requirement = ""
    if isinstance(package_json, dict):
        engines = package_json.get("engines")
        if isinstance(engines, dict):
            node_requirement = str(engines.get("node") or "").strip()
    if not node_requirement:
        return
    try:
        completed = run_command(["node", "--version"], check=True)
    except ShellCommandError as exc:
        raise ReleaseServiceError(f"Node.js is required for frontend auto-build but is unavailable: {exc}") from exc
    node_version = (completed.stdout or "").strip()
    if not _node_version_satisfies(node_version, node_requirement):
        raise ReleaseServiceError(
            f"Node.js {node_version or 'unknown'} does not satisfy package.json engines.node requirement "
            f"'{node_requirement}'. Install a compatible Node runtime or configure deploy.asset_commands explicitly."
        )


def resolve_build_commands_for_release(*, config: DeployConfig, release_dir: Path, commands: list[str]) -> list[str]:
    resolved = list(commands)
    auto_commands: list[str] = []
    if not any(command.strip().startswith(f"{config.composer_binary} install") for command in resolved):
        if (release_dir / "composer.json").exists() and not (release_dir / "vendor" / "autoload.php").exists():
            auto_commands.append(_composer_install_command(config))
    if _should_auto_build_frontend(release_dir) and not _has_explicit_frontend_build(resolved):
        auto_commands.extend([_npm_install_command(release_dir), "npm run build"])
    return [*auto_commands, *resolved]


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


def probe_http_endpoint(
    *,
    url: str,
    timeout_seconds: int,
    host_header: str | None = None,
    verify_tls: bool = True,
) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    if host_header:
        request.add_header("Host", host_header)

    handlers: list[urllib.request.BaseHandler] = [_NoRedirectHandler()]
    if url.startswith("https://") and not verify_tls:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(request, timeout=max(1, timeout_seconds)) as response:
            return {
                "checked": True,
                "status": "ok",
                "url": url,
                "http_status": int(response.status),
            }
    except urllib.error.HTTPError as exc:
        return {
            "checked": True,
            "status": "ok",
            "url": url,
            "http_status": int(exc.code),
        }
    except urllib.error.URLError as exc:
        return {
            "checked": True,
            "status": "failed",
            "url": url,
            "detail": str(exc.reason),
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
