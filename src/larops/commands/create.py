from __future__ import annotations

import os
import re
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError, run_command
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.app_lifecycle import (
    AppLifecycleError,
    activate_release,
    get_app_paths,
    initialize_app,
    load_metadata,
    prune_releases,
    save_metadata,
    switch_current_symlink,
)
from larops.services.nginx_site_service import (
    NginxSiteServiceError,
    apply_nginx_site_config,
    capture_nginx_site_snapshot,
    restore_nginx_site_snapshot,
)
from larops.services.env_file_service import (
    EnvFileServiceError,
    database_env_updates,
    upsert_env_values,
)
from larops.services.db_service import (
    DbServiceError,
    default_credential_file,
    default_password_file,
    deprovision_database,
    generate_database_password,
    normalize_database_name,
    normalize_database_user,
    provision_database,
)
from larops.services.release_service import (
    build_deploy_phase_commands,
    ReleaseServiceError,
    prepare_release_candidate,
    run_http_health_check,
    run_release_commands,
    write_release_manifest,
)
from larops.services.runtime_process import RuntimeProcessError, enable_process
from larops.services.runtime_process import disable_process as runtime_disable_process
from larops.services.runtime_process import reconcile_process as runtime_reconcile_process
from larops.services.runtime_process import status_process as runtime_status_process
from larops.services.ssl_service import (
    SslServiceError,
    build_delete_command,
    build_issue_command,
    default_cert_file,
    run_delete,
    run_issue,
)

create_app = typer.Typer(help="WordOps-style create shortcuts.")

_SITE_PROFILES: dict[str, dict[str, Any]] = {
    "small-vps": {
        "site_type": "laravel",
        "cache": "fastcgi",
        "php": "8.3",
        "runtime": {"worker": False},
    }
}

_TYPE_PRESETS: dict[str, dict[str, Any]] = {
    "php": {"db": "none", "ssl": False, "runtime": {"worker": False, "scheduler": False, "horizon": False}},
    "mysql": {"db": "mysql", "ssl": False, "runtime": {"worker": False, "scheduler": False, "horizon": False}},
    "laravel": {"db": "mysql", "ssl": True, "runtime": {"worker": True, "scheduler": True, "horizon": False}},
    "queue": {"db": "mysql", "ssl": True, "runtime": {"worker": True, "scheduler": True, "horizon": False}},
    "horizon": {"db": "mysql", "ssl": True, "runtime": {"worker": False, "scheduler": True, "horizon": True}},
}

_CACHE_PRESETS: dict[str, dict[str, Any]] = {
    "none": {"ssl": False},
    "fastcgi": {"ssl": True},
    "redis": {"ssl": True, "runtime": {"worker": True}},
    "supercache": {"ssl": True},
}

_LARAVEL_SOURCE_BOOTSTRAP_TYPES = {"laravel", "queue", "horizon"}


def _lock_name(domain: str) -> str:
    return f"create-site-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _emit(
    app_ctx: AppContext,
    *,
    severity: str,
    event_type: str,
    domain: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            app=domain,
            message=message,
            metadata=metadata or {},
        )
    )


def _emit_create_site_summary(
    app_ctx: AppContext,
    *,
    paths: Any,
    deploy: bool,
    release_id: str | None,
    runtime_results: dict[str, dict],
    ssl_result: dict | None,
    nginx_result: dict | None,
    db_result: dict | None,
    env_sync_result: dict | None,
) -> None:
    if app_ctx.json_output:
        return

    lines = [
        f"  app root: {paths.root}",
        f"  metadata: {paths.metadata}",
    ]
    if deploy and release_id:
        lines.extend(
            [
                f"  current release: {release_id}",
                f"  current path: {paths.current}",
            ]
        )
    if nginx_result is not None:
        lines.append(f"  nginx config: {nginx_result.get('site_config_file', 'managed')}")
    if ssl_result is not None:
        lines.append("  ssl: letsencrypt issued")
    elif nginx_result is not None and nginx_result.get("https_enabled"):
        lines.append("  ssl: existing certificate bound")
    if runtime_results:
        enabled = ", ".join(sorted(name for name, result in runtime_results.items() if result.get("enabled")))
        lines.append(f"  runtime: {enabled or 'none'}")
    if db_result is not None:
        lines.extend(
            [
                f"  db engine: {db_result['engine']}",
                f"  db name: {db_result['database']}",
                f"  db user: {db_result['user']}",
                f"  db credential file: {db_result['credential_file']}",
                f"  db password file: {db_result['password_file']}",
            ]
        )
    if env_sync_result is not None:
        lines.append(f"  env file: {env_sync_result['env_file']}")
    for line in lines:
        app_ctx.emit_output("ok", line)


def _resolve_targets(worker: bool, scheduler: bool, horizon: bool) -> dict[str, bool]:
    if worker or scheduler or horizon:
        return {
            "worker": worker,
            "scheduler": scheduler,
            "horizon": horizon,
        }
    return {
        "worker": True,
        "scheduler": True,
        "horizon": True,
    }


def _runtime_policy_for(app_ctx: AppContext, process_type: str) -> dict[str, Any]:
    return app_ctx.config.runtime_policy.model_dump().get(process_type, {})


def _apply_profile_patch(profile: dict[str, Any], patch: dict[str, Any]) -> None:
    runtime_patch = patch.get("runtime")
    if isinstance(runtime_patch, dict):
        profile["runtime"].update(runtime_patch)
    for key in ("db", "php", "ssl"):
        if key in patch:
            profile[key] = patch[key]


def _resolve_site_profile(
    *,
    profile_name: str | None,
    site_type: str | None,
    cache: str | None,
    worker: bool | None,
    scheduler: bool | None,
    horizon: bool | None,
    db: str | None,
    php: str | None,
    ssl: bool | None,
) -> dict[str, Any]:
    normalized_profile: str | None = None
    profile_defaults: dict[str, Any] = {}
    if profile_name:
        normalized_profile = profile_name.strip().lower()
        profile_defaults = _SITE_PROFILES.get(normalized_profile, {})
        if not profile_defaults:
            supported = ", ".join(sorted(_SITE_PROFILES))
            raise RuntimeProcessError(f"Unsupported --profile: {profile_name}. Supported: {supported}.")

    effective_type = site_type or profile_defaults.get("site_type")
    effective_cache = cache or profile_defaults.get("cache")
    effective_db = db if db is not None else profile_defaults.get("db")
    effective_php = php if php is not None else profile_defaults.get("php")
    effective_ssl = ssl if ssl is not None else profile_defaults.get("ssl")
    runtime_defaults = dict(profile_defaults.get("runtime", {}))
    effective_worker = worker if worker is not None else runtime_defaults.get("worker")
    effective_scheduler = scheduler if scheduler is not None else runtime_defaults.get("scheduler")
    effective_horizon = horizon if horizon is not None else runtime_defaults.get("horizon")

    profile: dict[str, Any] = {
        "preset": normalized_profile,
        "type": "custom",
        "cache": "none",
        "db": "mysql",
        "php": "8.3",
        "ssl": False,
        "runtime": {"worker": False, "scheduler": False, "horizon": False},
    }
    if effective_type:
        normalized_type = str(effective_type).strip().lower()
        preset = _TYPE_PRESETS.get(normalized_type)
        if preset is None:
            supported = ", ".join(sorted(_TYPE_PRESETS))
            raise RuntimeProcessError(f"Unsupported --type: {effective_type}. Supported: {supported}.")
        _apply_profile_patch(profile, preset)
        profile["type"] = normalized_type

    if effective_cache:
        normalized_cache = str(effective_cache).strip().lower()
        cache_preset = _CACHE_PRESETS.get(normalized_cache)
        if cache_preset is None:
            supported = ", ".join(sorted(_CACHE_PRESETS))
            raise RuntimeProcessError(f"Unsupported --cache: {effective_cache}. Supported: {supported}.")
        _apply_profile_patch(profile, cache_preset)
        profile["cache"] = normalized_cache

    if effective_db is not None:
        profile["db"] = effective_db
    if effective_php is not None:
        profile["php"] = effective_php
    if effective_ssl is not None:
        profile["ssl"] = effective_ssl
    if effective_worker is not None:
        profile["runtime"]["worker"] = effective_worker
    if effective_scheduler is not None:
        profile["runtime"]["scheduler"] = effective_scheduler
    if effective_horizon is not None:
        profile["runtime"]["horizon"] = effective_horizon
    return profile


def _validate_worker_options(*, concurrency: int, tries: int, timeout: int) -> None:
    if concurrency < 1:
        raise RuntimeProcessError("Worker concurrency must be >= 1.")
    if tries < 1:
        raise RuntimeProcessError("Worker tries must be >= 1.")
    if timeout < 1:
        raise RuntimeProcessError("Worker timeout must be >= 1 second.")


def _is_effective_laravel_site(site_profile: dict[str, Any]) -> bool:
    return str(site_profile.get("type", "")).lower() in _LARAVEL_SOURCE_BOOTSTRAP_TYPES


def _source_prepare_plan(
    *,
    source_path: Path,
    git_url: str | None,
    ref: str,
    site_profile: dict[str, Any],
    composer_binary: str,
) -> dict[str, Any]:
    if source_path.exists() and source_path.is_dir():
        return {"mode": "existing", "path": str(source_path)}
    if git_url:
        return {
            "mode": "git-clone",
            "path": str(source_path),
            "git_url": git_url,
            "git_ref": ref,
            "command": ["git", "clone", "--branch", ref, "--single-branch", git_url, str(source_path)],
        }
    if _is_effective_laravel_site(site_profile):
        return {
            "mode": "laravel-init",
            "path": str(source_path),
            "composer_binary": composer_binary,
            "command": [composer_binary, "create-project", "laravel/laravel", str(source_path)],
        }
    return {
        "mode": "missing",
        "path": str(source_path),
        "detail": "Provide --source or --git-url when the source directory does not exist.",
    }


def _ensure_empty_source_target(source_path: Path) -> None:
    if source_path.exists():
        if not source_path.is_dir():
            raise RuntimeProcessError(f"Source path exists but is not a directory: {source_path}")
        if any(source_path.iterdir()):
            raise RuntimeProcessError(f"Source path already exists and is not empty: {source_path}")
        source_path.rmdir()
    source_path.parent.mkdir(parents=True, exist_ok=True)


def _prepare_source(*, plan: dict[str, Any]) -> None:
    mode = str(plan.get("mode", "missing"))
    source_path = Path(str(plan["path"]))
    if mode == "existing":
        if not source_path.exists() or not source_path.is_dir():
            raise RuntimeProcessError(f"Source path does not exist or is not a directory: {source_path}")
        return
    if mode == "git-clone":
        _ensure_empty_source_target(source_path)
        run_command(
            ["git", "clone", "--branch", str(plan["git_ref"]), "--single-branch", str(plan["git_url"]), str(source_path)],
            check=True,
        )
        return
    if mode == "laravel-init":
        _ensure_empty_source_target(source_path)
        run_command([str(plan["composer_binary"]), "create-project", "laravel/laravel", str(source_path)], check=True)
        return
    raise RuntimeProcessError(str(plan.get("detail") or f"Source path does not exist or is not a directory: {source_path}"))


def _runtime_spec_path(state_path: Path, domain: str, process_type: str) -> Path:
    return state_path / "runtime" / domain / f"{process_type}.json"


def _capture_atomic_snapshot(*, paths, state_path: Path) -> dict[str, Any]:
    current_target: str | None = None
    if paths.current.exists() and paths.current.is_symlink():
        try:
            current_target = str(paths.current.resolve(strict=True))
        except FileNotFoundError:
            current_target = None
    metadata_raw = paths.metadata.read_text(encoding="utf-8") if paths.metadata.exists() else None
    return {
        "root_exists": paths.root.exists(),
        "metadata_exists": paths.metadata.exists(),
        "metadata_raw": metadata_raw,
        "current_target": current_target,
        "runtime_dir_exists": (state_path / "runtime" / paths.root.name).exists(),
    }


def _atomic_rollback_create_site(
    *,
    app_ctx: AppContext,
    domain: str,
    paths,
    snapshot: dict[str, Any] | None,
    runtime_results: dict[str, dict],
    release_id: str | None,
    letsencrypt: bool,
) -> list[dict[str, str]]:
    if snapshot is None:
        return [{"step": "skip", "status": "error", "detail": "No snapshot available for rollback."}]

    steps: list[dict[str, str]] = []
    state_path = Path(app_ctx.config.state_path)

    for process_type in runtime_results:
        try:
            runtime_disable_process(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=state_path,
                systemd_manage=app_ctx.config.systemd.manage,
                domain=domain,
                process_type=process_type,
            )
            spec_file = _runtime_spec_path(state_path, domain, process_type)
            if spec_file.exists():
                spec_file.unlink()
            steps.append({"step": f"disable_{process_type}", "status": "ok", "detail": "disabled"})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": f"disable_{process_type}", "status": "error", "detail": str(exc)})

    if release_id:
        release_dir = paths.releases / release_id
        try:
            if release_dir.exists():
                shutil.rmtree(release_dir, ignore_errors=True)
            steps.append({"step": "remove_release", "status": "ok", "detail": release_id})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": "remove_release", "status": "error", "detail": str(exc)})

    try:
        target_raw = snapshot.get("current_target")
        if target_raw:
            target_path = Path(target_raw)
            if target_path.exists():
                switch_current_symlink(paths.current, target_path)
                steps.append({"step": "restore_current", "status": "ok", "detail": target_raw})
            else:
                steps.append(
                    {"step": "restore_current", "status": "error", "detail": "Previous current target missing."}
                )
        else:
            if paths.current.exists() or paths.current.is_symlink():
                paths.current.unlink()
            steps.append({"step": "restore_current", "status": "ok", "detail": "cleared"})
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "restore_current", "status": "error", "detail": str(exc)})

    try:
        if snapshot.get("metadata_exists"):
            metadata_raw = snapshot.get("metadata_raw")
            if metadata_raw is not None:
                paths.metadata.parent.mkdir(parents=True, exist_ok=True)
                paths.metadata.write_text(str(metadata_raw), encoding="utf-8")
            steps.append({"step": "restore_metadata", "status": "ok", "detail": "restored"})
        else:
            if paths.metadata.exists():
                paths.metadata.unlink()
            steps.append({"step": "restore_metadata", "status": "ok", "detail": "removed"})
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "restore_metadata", "status": "error", "detail": str(exc)})

    try:
        if not snapshot.get("root_exists") and paths.root.exists():
            shutil.rmtree(paths.root, ignore_errors=True)
            steps.append({"step": "cleanup_root", "status": "ok", "detail": str(paths.root)})
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "cleanup_root", "status": "error", "detail": str(exc)})

    if not snapshot.get("runtime_dir_exists"):
        runtime_dir = state_path / "runtime" / domain
        try:
            if runtime_dir.exists():
                shutil.rmtree(runtime_dir, ignore_errors=True)
            steps.append({"step": "cleanup_runtime_dir", "status": "ok", "detail": str(runtime_dir)})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": "cleanup_runtime_dir", "status": "error", "detail": str(exc)})

    if letsencrypt:
        try:
            cleanup_command = build_delete_command(domain=domain)
            run_delete(cleanup_command)
            steps.append(
                {
                    "step": "cleanup_tls",
                    "status": "ok",
                    "detail": "Certbot certificate cleanup completed.",
                }
            )
        except (SslServiceError, ShellCommandError) as exc:
            steps.append(
                {
                    "step": "cleanup_tls",
                    "status": "warn",
                    "detail": f"TLS cleanup failed and may need manual action: {exc}",
                }
            )

    return steps


def _enable_runtime_for_site(
    *,
    app_ctx: AppContext,
    domain: str,
    queue: str,
    concurrency: int,
    tries: int,
    timeout: int,
    schedule_command: str,
    targets: dict[str, bool],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for process_type, enabled in targets.items():
        if not enabled:
            continue
        if process_type == "worker":
            options: dict[str, Any] = {
                "queue": queue,
                "concurrency": concurrency,
                "tries": tries,
                "timeout": timeout,
            }
        elif process_type == "scheduler":
            options = {"command": schedule_command}
        else:
            options = {}
        results[process_type] = enable_process(
            base_releases_path=Path(app_ctx.config.deploy.releases_path),
            state_path=Path(app_ctx.config.state_path),
            unit_dir=Path(app_ctx.config.systemd.unit_dir),
            systemd_manage=app_ctx.config.systemd.manage,
            service_user=app_ctx.config.systemd.user,
            domain=domain,
            process_type=process_type,
            options=options,
            policy=_runtime_policy_for(app_ctx, process_type),
        )
    return results


def _disable_runtime_for_site(
    *,
    app_ctx: AppContext,
    domain: str,
    targets: dict[str, bool],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for process_type, enabled in targets.items():
        if not enabled:
            continue
        results[process_type] = runtime_disable_process(
            base_releases_path=Path(app_ctx.config.deploy.releases_path),
            state_path=Path(app_ctx.config.state_path),
            systemd_manage=app_ctx.config.systemd.manage,
            domain=domain,
            process_type=process_type,
        )
    return results


def _status_runtime_for_site(
    *,
    app_ctx: AppContext,
    domain: str,
    targets: dict[str, bool],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for process_type, enabled in targets.items():
        if not enabled:
            continue
        results[process_type] = runtime_status_process(
            state_path=Path(app_ctx.config.state_path),
            unit_dir=Path(app_ctx.config.systemd.unit_dir),
            systemd_manage=app_ctx.config.systemd.manage,
            domain=domain,
            process_type=process_type,
            policy=_runtime_policy_for(app_ctx, process_type),
        )
    return results


def _reconcile_runtime_for_site(
    *,
    app_ctx: AppContext,
    domain: str,
    targets: dict[str, bool],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for process_type, enabled in targets.items():
        if not enabled:
            continue
        results[process_type] = runtime_reconcile_process(
            state_path=Path(app_ctx.config.state_path),
            unit_dir=Path(app_ctx.config.systemd.unit_dir),
            systemd_manage=app_ctx.config.systemd.manage,
            domain=domain,
            process_type=process_type,
            policy=_runtime_policy_for(app_ctx, process_type),
        )
    return results


def _run_site_runtime_mode(
    *,
    app_ctx: AppContext,
    mode: str,
    domain: str,
    queue: str,
    concurrency: int,
    tries: int,
    timeout: int,
    schedule_command: str,
    apply: bool,
    worker: bool,
    scheduler: bool,
    horizon: bool,
) -> None:
    targets = _resolve_targets(worker, scheduler, horizon)
    if mode == "enable" and targets["worker"]:
        try:
            _validate_worker_options(concurrency=concurrency, tries=tries, timeout=timeout)
        except RuntimeProcessError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"Site {mode} plan prepared for {domain}",
        domain=domain,
        mode=mode,
        targets=targets,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if mode == "status":
        status = _status_runtime_for_site(app_ctx=app_ctx, domain=domain, targets=targets)
        app_ctx.emit_output("ok", f"Site status for {domain}", domain=domain, processes=status)
        return

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="info",
        event_type=f"site.{mode}.started",
        domain=domain,
        message=f"Site {mode} started.",
        metadata={"targets": targets},
    )
    try:
        with CommandLock(_lock_name(domain)):
            if mode == "enable":
                results = _enable_runtime_for_site(
                    app_ctx=app_ctx,
                    domain=domain,
                    queue=queue,
                    concurrency=concurrency,
                    tries=tries,
                    timeout=timeout,
                    schedule_command=schedule_command,
                    targets=targets,
                )
            elif mode == "reconcile":
                results = _reconcile_runtime_for_site(app_ctx=app_ctx, domain=domain, targets=targets)
            else:
                results = _disable_runtime_for_site(app_ctx=app_ctx, domain=domain, targets=targets)
    except (CommandLockError, RuntimeProcessError) as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type=f"site.{mode}.failed",
            domain=domain,
            message=f"Site {mode} failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type=f"site.{mode}.completed",
        domain=domain,
        message=f"Site {mode} completed.",
        metadata={"targets": targets},
    )
    app_ctx.emit_output("ok", f"Site {mode} completed for {domain}", domain=domain, mode=mode, results=results)


def manage_site_runtime(
    *,
    app_ctx: AppContext,
    mode: str,
    domain: str,
    queue: str,
    concurrency: int,
    tries: int,
    timeout: int,
    schedule_command: str,
    apply: bool,
    worker: bool,
    scheduler: bool,
    horizon: bool,
) -> None:
    _run_site_runtime_mode(
        app_ctx=app_ctx,
        mode=mode,
        domain=domain,
        queue=queue,
        concurrency=concurrency,
        tries=tries,
        timeout=timeout,
        schedule_command=schedule_command,
        apply=apply,
        worker=worker,
        scheduler=scheduler,
        horizon=horizon,
    )


@create_app.command("site")
def create_site(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain."),
    source: Path | None = typer.Option(
        None,
        "--source",
        help="Laravel source directory to deploy (default: deploy.source_base_path/<domain>).",
        exists=False,
        file_okay=False,
    ),
    ref: str = typer.Option("main", "--ref", "-r", help="Source ref metadata."),
    git_url: str | None = typer.Option(
        None,
        "--git-url",
        help="Clone source into deploy.source_base_path/<domain> when local source is missing.",
    ),
    deploy: bool = typer.Option(True, "--deploy/--no-deploy", help="Deploy source after create."),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Site profile preset. Example: small-vps.",
    ),
    site_type: str | None = typer.Option(
        None,
        "--type",
        help="WordOps-style site type preset (php|mysql|laravel|queue|horizon).",
    ),
    cache: str | None = typer.Option(
        None,
        "--cache",
        help="Cache preset (none|fastcgi|redis|supercache).",
    ),
    worker: bool | None = typer.Option(None, "--worker/--no-worker", "-w", help="Enable queue worker."),
    scheduler: bool | None = typer.Option(None, "--scheduler/--no-scheduler", "-s", help="Enable scheduler."),
    horizon: bool | None = typer.Option(None, "--horizon/--no-horizon", help="Enable horizon."),
    queue: str = typer.Option("default", "--queue", "-q", help="Worker queue."),
    concurrency: int = typer.Option(1, "--concurrency", "-c", help="Worker concurrency."),
    tries: int = typer.Option(3, "--tries", "-t", help="Worker tries."),
    timeout: int = typer.Option(90, "--timeout", help="Worker timeout in seconds."),
    schedule_command: str = typer.Option(
        "php artisan schedule:work",
        "--schedule-command",
        help="Scheduler command.",
    ),
    php: str | None = typer.Option(None, "--php", help="PHP runtime version override."),
    db: str | None = typer.Option(None, "--db", help="Database engine override."),
    with_db: bool = typer.Option(False, "--with-db", help="Provision an application database/user automatically."),
    db_name: str | None = typer.Option(None, "--db-name", help="Application database name override."),
    db_user: str | None = typer.Option(None, "--db-user", help="Application database user override."),
    db_host: str = typer.Option("127.0.0.1", "--db-host", help="Application database host."),
    db_port: int | None = typer.Option(None, "--db-port", help="Application database port."),
    db_password_env: str = typer.Option(
        "",
        "--db-password-env",
        help="Optional env var containing the application database password. If omitted, LarOps generates one.",
    ),
    db_password_file: Path | None = typer.Option(
        None,
        "--db-password-file",
        help="Optional application password file path.",
        dir_okay=False,
    ),
    db_credential_file: Path | None = typer.Option(
        None,
        "--db-credential-file",
        help="Optional application credential file path.",
        dir_okay=False,
    ),
    db_admin_credential_file: Path | None = typer.Option(
        None,
        "--db-admin-credential-file",
        help="Optional admin credential file for DB provisioning.",
        dir_okay=False,
    ),
    ssl: bool | None = typer.Option(None, "--ssl/--no-ssl", help="Enable SSL metadata flag."),
    nginx: bool | None = typer.Option(None, "--nginx/--no-nginx", help="Provision a managed Nginx site config."),
    letsencrypt: bool = typer.Option(
        False,
        "--letsencrypt",
        "-le",
        help="Issue Let's Encrypt certificate (WordOps-style).",
    ),
    le_email: str | None = typer.Option(None, "--le-email", help="Email for Let's Encrypt registration."),
    le_challenge: str = typer.Option("http", "--le-challenge", help="Challenge: http or dns."),
    le_dns_provider: str | None = typer.Option(None, "--le-dns-provider", help="DNS provider for dns challenge."),
    le_webroot: Path | None = typer.Option(
        None,
        "--le-webroot",
        help="HTTP challenge webroot path override.",
        file_okay=False,
    ),
    le_staging: bool = typer.Option(False, "--le-staging", help="Use Let's Encrypt staging environment."),
    atomic: bool = typer.Option(
        False,
        "--atomic",
        help="Rollback created artifacts automatically when create flow fails.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing app metadata."),
    apply: bool = typer.Option(False, "--apply", "-a", help="Apply create site workflow."),
) -> None:
    app_ctx: AppContext = ctx.obj
    try:
        site_profile = _resolve_site_profile(
            profile_name=profile,
            site_type=site_type,
            cache=cache,
            worker=worker,
            scheduler=scheduler,
            horizon=horizon,
            db=db,
            php=php,
            ssl=ssl,
        )
    except RuntimeProcessError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    runtime_plan = {
        "worker": bool(site_profile["runtime"]["worker"]),
        "scheduler": bool(site_profile["runtime"]["scheduler"]),
        "horizon": bool(site_profile["runtime"]["horizon"]),
    }
    php_runtime = str(site_profile["php"])
    db_engine = str(site_profile["db"])
    ssl_enabled = bool(site_profile["ssl"])
    nginx_enabled = deploy if nginx is None else nginx
    db_provision_plan: dict[str, Any] | None = None

    if with_db:
        if db_engine == "none":
            app_ctx.emit_output("error", "--with-db requires a site profile with a real database engine.")
            raise typer.Exit(code=2)
        try:
            resolved_db_name = normalize_database_name(db_name or domain)
            resolved_db_user = normalize_database_user(db_user or domain, engine=db_engine)
        except DbServiceError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc
        resolved_db_port = db_port if db_port is not None else (5432 if db_engine == "postgres" else 3306)
        if resolved_db_port < 1:
            app_ctx.emit_output("error", "Application DB port must be >= 1.")
            raise typer.Exit(code=2)
        resolved_db_password_file = (
            db_password_file or default_password_file(Path(app_ctx.config.state_path), domain, engine=db_engine)
        )
        resolved_db_credential_file = (
            db_credential_file or default_credential_file(Path(app_ctx.config.state_path), domain, engine=db_engine)
        )
        password_source = db_password_env or "generated"
        db_provision_plan = {
            "engine": db_engine,
            "database": resolved_db_name,
            "user": resolved_db_user,
            "host": db_host,
            "port": resolved_db_port,
            "password_source": password_source,
            "password_file": str(resolved_db_password_file),
            "credential_file": str(resolved_db_credential_file),
            "admin_credential_file": str(db_admin_credential_file) if db_admin_credential_file is not None else None,
        }

    if not deploy and (runtime_plan["worker"] or runtime_plan["scheduler"] or runtime_plan["horizon"]):
        app_ctx.emit_output(
            "error",
            "Runtime enable requires --deploy. Remove runtime flags or use --deploy.",
        )
        raise typer.Exit(code=2)
    if letsencrypt and not deploy:
        app_ctx.emit_output(
            "error",
            "Let's Encrypt requires --deploy. Remove -le or use --deploy.",
        )
        raise typer.Exit(code=2)
    if nginx_enabled and not deploy:
        app_ctx.emit_output(
            "error",
            "Managed Nginx site provisioning requires --deploy. Remove --nginx or use --deploy.",
        )
        raise typer.Exit(code=2)
    if runtime_plan["worker"]:
        try:
            _validate_worker_options(concurrency=concurrency, tries=tries, timeout=timeout)
        except RuntimeProcessError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc

    source_path = (
        source.resolve()
        if source is not None
        else (Path(app_ctx.config.deploy.source_base_path) / domain).resolve()
    )
    source_prepare = _source_prepare_plan(
        source_path=source_path,
        git_url=git_url,
        ref=ref,
        site_profile=site_profile,
        composer_binary=app_ctx.config.deploy.composer_binary,
    )
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    ssl_issue_command: list[str] | None = None
    if letsencrypt:
        try:
            default_webroot = str((paths.current / "public").resolve(strict=False)) if le_challenge == "http" else None
            ssl_issue_command = build_issue_command(
                domain=domain,
                email=le_email,
                challenge=le_challenge,
                dns_provider=le_dns_provider,
                staging=le_staging,
                webroot_path=str(le_webroot.resolve()) if le_webroot is not None else default_webroot,
            )
        except SslServiceError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc
    app_ctx.emit_output(
        "ok",
        f"Create site plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        source_prepare=source_prepare,
        profile=site_profile,
        ref=ref,
        git_url=git_url,
        deploy=deploy,
        runtime=runtime_plan,
        php=php_runtime,
        db=db_engine,
        db_provision=db_provision_plan,
        ssl=ssl_enabled or letsencrypt,
        nginx=nginx_enabled,
        letsencrypt=letsencrypt,
        letsencrypt_command=ssl_issue_command,
        atomic=atomic,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    if not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit(
        app_ctx,
        severity="info",
        event_type="create.site.started",
        domain=domain,
        message="Create site started.",
        metadata={
            "ref": ref,
            "source_prepare": source_prepare,
            "deploy": deploy,
            "runtime": runtime_plan,
        },
    )

    release_id: str | None = None
    deleted_releases: list[str] = []
    runtime_results: dict[str, dict] = {}
    rollback_steps: list[dict[str, str]] = []
    snapshot = _capture_atomic_snapshot(paths=paths, state_path=Path(app_ctx.config.state_path)) if atomic else None
    nginx_snapshot = capture_nginx_site_snapshot(domain) if atomic and deploy and nginx_enabled else None
    ssl_result: dict | None = None
    nginx_result: dict | None = None
    db_result: dict | None = None
    env_sync_result: dict | None = None
    db_provisioned = False
    db_password_value: str | None = None
    existing_cert_file = default_cert_file(domain)
    existing_privkey_file = existing_cert_file.parent / "privkey.pem"
    existing_certificate_available = existing_cert_file.exists() and existing_privkey_file.exists()
    try:
        with CommandLock(_lock_name(domain)):
            _prepare_source(plan=source_prepare)
            metadata_payload = {
                "domain": domain,
                "php": php_runtime,
                "db": db_engine,
                "ssl": ssl_enabled or letsencrypt,
                "profile": site_profile,
                "created_at": datetime.now(UTC).isoformat(),
            }
            initialize_app(paths, metadata_payload, overwrite=force)

            if db_provision_plan is not None:
                password = os.getenv(db_password_env, "").strip() if db_password_env else ""
                password = password or generate_database_password()
                db_password_value = password
                db_result = provision_database(
                    engine=str(db_provision_plan["engine"]),
                    database=str(db_provision_plan["database"]),
                    user=str(db_provision_plan["user"]),
                    password=password,
                    app_host=str(db_provision_plan["host"]),
                    app_port=int(db_provision_plan["port"]),
                    state_path=Path(app_ctx.config.state_path),
                    domain=domain,
                    credential_file=Path(str(db_provision_plan["credential_file"])),
                    password_file=Path(str(db_provision_plan["password_file"])),
                    admin_credential_file=(
                        Path(str(db_provision_plan["admin_credential_file"]))
                        if db_provision_plan["admin_credential_file"] is not None
                        else None
                    ),
                )
                db_provisioned = True
                metadata = load_metadata(paths.metadata)
                metadata["database_provision"] = db_result
                save_metadata(paths.metadata, metadata)

                if not deploy:
                    env_sync_result = upsert_env_values(
                        env_file=paths.shared / ".env",
                        updates=database_env_updates(
                            engine=str(db_result["engine"]),
                            host=str(db_result["host"]),
                            port=int(db_result["port"]),
                            database=str(db_result["database"]),
                            user=str(db_result["user"]),
                            password=db_password_value or "",
                        ),
                    )
                    metadata = load_metadata(paths.metadata)
                    metadata["env_sync"] = env_sync_result
                    save_metadata(paths.metadata, metadata)

            if deploy:
                phase_commands = build_deploy_phase_commands(app_ctx.config.deploy)
                release_id, release_dir = prepare_release_candidate(
                    paths=paths,
                    source_path=source_path,
                    ref=ref,
                    shared_dirs=app_ctx.config.deploy.shared_dirs,
                    shared_files=app_ctx.config.deploy.shared_files,
                )
                if db_result is not None:
                    env_sync_result = upsert_env_values(
                        env_file=paths.shared / ".env",
                        updates=database_env_updates(
                            engine=str(db_result["engine"]),
                            host=str(db_result["host"]),
                            port=int(db_result["port"]),
                            database=str(db_result["database"]),
                            user=str(db_result["user"]),
                            password=db_password_value or "",
                        ),
                    )
                    metadata = load_metadata(paths.metadata)
                    metadata["env_sync"] = env_sync_result
                    save_metadata(paths.metadata, metadata)
                build_reports = run_release_commands(
                    workdir=release_dir,
                    phase="build",
                    commands=phase_commands["build"],
                    timeout_seconds=app_ctx.config.deploy.build_timeout_seconds,
                )
                pre_activate_reports = run_release_commands(
                    workdir=release_dir,
                    phase="pre-activate",
                    commands=phase_commands["pre_activate"],
                    timeout_seconds=app_ctx.config.deploy.pre_activate_timeout_seconds,
                )
                activate_release(paths, release_dir)
                current_path = paths.current.resolve(strict=True)
                post_activate_reports = run_release_commands(
                    workdir=current_path,
                    phase="post-activate",
                    commands=phase_commands["post_activate"],
                    timeout_seconds=app_ctx.config.deploy.post_activate_timeout_seconds,
                )
                health_check = run_http_health_check(
                    domain=domain,
                    path=app_ctx.config.deploy.health_check_path,
                    enabled=app_ctx.config.deploy.health_check_enabled,
                    scheme=app_ctx.config.deploy.health_check_scheme,
                    host=app_ctx.config.deploy.health_check_host,
                    timeout_seconds=app_ctx.config.deploy.health_check_timeout_seconds,
                    retries=app_ctx.config.deploy.health_check_retries,
                    retry_delay_seconds=app_ctx.config.deploy.health_check_retry_delay_seconds,
                    expected_status=app_ctx.config.deploy.health_check_expected_status,
                    use_domain_host_header=app_ctx.config.deploy.health_check_use_domain_host_header,
                )
                if health_check["status"] == "failed":
                    raise AppLifecycleError(f"Deploy health check failed: {health_check.get('detail', 'unknown')}")
                verify_reports = run_release_commands(
                    workdir=current_path,
                    phase="verify",
                    commands=phase_commands["verify"],
                    timeout_seconds=app_ctx.config.deploy.verify_timeout_seconds,
                )
                deleted_releases = prune_releases(paths, app_ctx.config.deploy.keep_releases)
                metadata = load_metadata(paths.metadata)
                metadata["last_deploy"] = {
                    "release_id": release_id,
                    "ref": ref,
                    "deployed_at": datetime.now(UTC).isoformat(),
                    "source": str(source_path),
                    "build_reports": build_reports,
                    "pre_activate_reports": pre_activate_reports,
                    "post_activate_reports": post_activate_reports,
                    "verify_reports": verify_reports,
                    "health_check": health_check,
                }
                save_metadata(paths.metadata, metadata)
                write_release_manifest(
                    release_dir,
                    {
                        "status": "deployed",
                        "release_id": release_id,
                        "ref": ref,
                        "source": str(source_path),
                        "deployed_at": datetime.now(UTC).isoformat(),
                        "phase_reports": {
                            "build": build_reports,
                            "pre_activate": pre_activate_reports,
                            "post_activate": post_activate_reports,
                            "verify": verify_reports,
                        },
                        "health_check": health_check,
                    },
                )
                if nginx_enabled:
                    current_path = paths.current.resolve(strict=True)
                    nginx_result = apply_nginx_site_config(
                        domain=domain,
                        current_path=current_path,
                        php_version=php_runtime,
                        https_enabled=existing_certificate_available,
                        force=force,
                    )

            if any(runtime_plan.values()):
                runtime_results = _enable_runtime_for_site(
                    app_ctx=app_ctx,
                    domain=domain,
                    queue=queue,
                    concurrency=concurrency,
                    tries=tries,
                    timeout=timeout,
                    schedule_command=schedule_command,
                    targets=runtime_plan,
                )
            if letsencrypt:
                assert ssl_issue_command is not None
                output = run_issue(ssl_issue_command)
                ssl_result = {
                    "provider": "letsencrypt",
                    "challenge": le_challenge,
                    "staging": le_staging,
                    "output": output,
                }
                if nginx_enabled and deploy:
                    current_path = paths.current.resolve(strict=True)
                    nginx_result = apply_nginx_site_config(
                        domain=domain,
                        current_path=current_path,
                        php_version=php_runtime,
                        https_enabled=True,
                        force=True,
                    )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (
        AppLifecycleError,
        RuntimeProcessError,
        SslServiceError,
        ShellCommandError,
        ReleaseServiceError,
        NginxSiteServiceError,
        DbServiceError,
        EnvFileServiceError,
    ) as exc:
        if atomic:
            rollback_steps = _atomic_rollback_create_site(
                app_ctx=app_ctx,
                domain=domain,
                paths=paths,
                snapshot=snapshot,
                runtime_results=runtime_results,
                release_id=release_id,
                letsencrypt=letsencrypt,
            )
            if nginx_snapshot is not None:
                restore_nginx_site_snapshot(nginx_snapshot)
                rollback_steps.append({"step": "restore_nginx_site"})
            if db_provisioned and db_result is not None:
                try:
                    deprovision_database(
                        engine=str(db_result["engine"]),
                        database=str(db_result["database"]),
                        user=str(db_result["user"]),
                        app_host=str(db_result["host"]),
                        admin_credential_file=(
                            Path(str(db_result["admin_credential_file"]))
                            if db_result.get("admin_credential_file")
                            else None
                        ),
                        drop_password_file=Path(str(db_result["password_file"])),
                        drop_credential_file=Path(str(db_result["credential_file"])),
                    )
                    rollback_steps.append({"step": "deprovision_database"})
                except DbServiceError as rollback_exc:
                    rollback_steps.append({"step": "deprovision_database_failed", "error": str(rollback_exc)})
        _emit(
            app_ctx,
            severity="error",
            event_type="create.site.failed",
            domain=domain,
            message="Create site failed.",
            metadata={
                "error": str(exc),
                "atomic": atomic,
                "rollback": rollback_steps,
            },
        )
        app_ctx.emit_output("error", str(exc), atomic=atomic, rollback=rollback_steps)
        raise typer.Exit(code=2) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="create.site.completed",
        domain=domain,
        message="Create site completed.",
        metadata={"deploy": deploy, "runtime": runtime_plan, "nginx": nginx_enabled, "db": db_result},
    )
    app_ctx.emit_output(
        "ok",
        f"Create site completed for {domain}",
        deployed=deploy,
        release_id=release_id,
        deleted_releases=deleted_releases,
        runtime_enabled=runtime_results,
        ssl_result=ssl_result,
        nginx_result=nginx_result,
        db_result=db_result,
        env_sync_result=env_sync_result,
    )
    _emit_create_site_summary(
        app_ctx,
        paths=paths,
        deploy=deploy,
        release_id=release_id,
        runtime_results=runtime_results,
        ssl_result=ssl_result,
        nginx_result=nginx_result,
        db_result=db_result,
        env_sync_result=env_sync_result,
    )
