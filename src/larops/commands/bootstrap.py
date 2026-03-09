from __future__ import annotations

from copy import deepcopy
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from larops.config import AppConfig
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
)
from larops.services.release_service import (
    build_deploy_phase_commands,
    ReleaseServiceError,
    prepare_release_candidate,
    run_http_health_check,
    run_release_commands,
    write_release_manifest,
)
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux
from larops.services.stack_service import StackServiceError, apply_stack_plan, build_stack_plan, resolve_groups

bootstrap_app = typer.Typer(help="Bootstrap empty servers like WordOps-style one-shot setup.")

_BOOTSTRAP_PROFILES: dict[str, dict[str, object]] = {
    "default": {
        "groups": {
            "web": True,
            "data": True,
            "postgres": False,
            "ops": True,
        },
        "runtime_policy": {
            "worker": {"max_restarts": 5, "window_seconds": 300, "cooldown_seconds": 120, "auto_heal": True},
            "scheduler": {"max_restarts": 5, "window_seconds": 300, "cooldown_seconds": 120, "auto_heal": True},
            "horizon": {"max_restarts": 5, "window_seconds": 300, "cooldown_seconds": 120, "auto_heal": True},
        },
        "telegram_batch_size": 20,
    },
    "small-vps": {
        "groups": {
            "web": True,
            "data": False,
            "postgres": False,
            "ops": True,
        },
        "runtime_policy": {
            "worker": {"max_restarts": 3, "window_seconds": 600, "cooldown_seconds": 180, "auto_heal": True},
            "scheduler": {"max_restarts": 3, "window_seconds": 600, "cooldown_seconds": 180, "auto_heal": True},
            "horizon": {"max_restarts": 2, "window_seconds": 900, "cooldown_seconds": 300, "auto_heal": True},
        },
        "telegram_batch_size": 10,
    },
}


def _resolve_bootstrap_profile(profile: str) -> dict[str, object]:
    normalized = profile.strip().lower()
    resolved = _BOOTSTRAP_PROFILES.get(normalized)
    if resolved is None:
        supported = ", ".join(sorted(_BOOTSTRAP_PROFILES))
        raise typer.BadParameter(f"Unsupported --profile: {profile}. Supported: {supported}.")
    return {"name": normalized, **resolved}


def _effective_bootstrap_runtime_policy(app_ctx: AppContext, *, profile_name: str) -> dict[str, object]:
    current_policy = app_ctx.config.runtime_policy.model_dump()
    default_policy = AppConfig().runtime_policy.model_dump()
    profile_policy = deepcopy(_resolve_bootstrap_profile(profile_name)["runtime_policy"])
    resolved_policy = deepcopy(current_policy)
    for process_type in ("worker", "scheduler", "horizon"):
        if resolved_policy.get(process_type) == default_policy.get(process_type):
            resolved_policy[process_type] = deepcopy(profile_policy[process_type])
    return resolved_policy


def _effective_bootstrap_telegram_batch_size(app_ctx: AppContext, *, profile_name: str) -> int:
    current_batch_size = int(app_ctx.config.notifications.telegram.batch_size)
    default_batch_size = int(AppConfig().notifications.telegram.batch_size)
    if current_batch_size != default_batch_size:
        return current_batch_size
    return int(_resolve_bootstrap_profile(profile_name)["telegram_batch_size"])


def _default_config_yaml(app_ctx: AppContext, *, profile_name: str) -> str:
    runtime_policy = _effective_bootstrap_runtime_policy(app_ctx, profile_name=profile_name)
    telegram_batch_size = _effective_bootstrap_telegram_batch_size(app_ctx, profile_name=profile_name)
    payload = {
        "environment": app_ctx.config.environment,
        "state_path": app_ctx.config.state_path,
        "deploy": {
            "releases_path": app_ctx.config.deploy.releases_path,
            "source_base_path": app_ctx.config.deploy.source_base_path,
            "keep_releases": app_ctx.config.deploy.keep_releases,
            "build_timeout_seconds": app_ctx.config.deploy.build_timeout_seconds,
            "pre_activate_timeout_seconds": app_ctx.config.deploy.pre_activate_timeout_seconds,
            "post_activate_timeout_seconds": app_ctx.config.deploy.post_activate_timeout_seconds,
            "health_check_path": app_ctx.config.deploy.health_check_path,
            "health_check_enabled": app_ctx.config.deploy.health_check_enabled,
            "health_check_scheme": app_ctx.config.deploy.health_check_scheme,
            "health_check_host": app_ctx.config.deploy.health_check_host,
            "health_check_timeout_seconds": app_ctx.config.deploy.health_check_timeout_seconds,
            "health_check_retries": app_ctx.config.deploy.health_check_retries,
            "health_check_retry_delay_seconds": app_ctx.config.deploy.health_check_retry_delay_seconds,
            "health_check_expected_status": app_ctx.config.deploy.health_check_expected_status,
            "health_check_use_domain_host_header": app_ctx.config.deploy.health_check_use_domain_host_header,
            "rollback_on_health_check_failure": app_ctx.config.deploy.rollback_on_health_check_failure,
            "runtime_refresh_strategy": app_ctx.config.deploy.runtime_refresh_strategy,
            "shared_dirs": list(app_ctx.config.deploy.shared_dirs),
            "shared_files": list(app_ctx.config.deploy.shared_files),
            "composer_install": app_ctx.config.deploy.composer_install,
            "composer_binary": app_ctx.config.deploy.composer_binary,
            "composer_no_dev": app_ctx.config.deploy.composer_no_dev,
            "composer_optimize_autoloader": app_ctx.config.deploy.composer_optimize_autoloader,
            "asset_commands": list(app_ctx.config.deploy.asset_commands),
            "migrate_enabled": app_ctx.config.deploy.migrate_enabled,
            "migrate_phase": app_ctx.config.deploy.migrate_phase,
            "migrate_command": app_ctx.config.deploy.migrate_command,
            "cache_warm_enabled": app_ctx.config.deploy.cache_warm_enabled,
            "cache_warm_commands": list(app_ctx.config.deploy.cache_warm_commands),
            "verify_timeout_seconds": app_ctx.config.deploy.verify_timeout_seconds,
            "verify_commands": list(app_ctx.config.deploy.verify_commands),
            "rollback_on_verify_failure": app_ctx.config.deploy.rollback_on_verify_failure,
            "pre_activate_commands": list(app_ctx.config.deploy.pre_activate_commands),
            "post_activate_commands": list(app_ctx.config.deploy.post_activate_commands),
        },
        "systemd": {
            "manage": app_ctx.config.systemd.manage,
            "unit_dir": app_ctx.config.systemd.unit_dir,
            "user": app_ctx.config.systemd.user,
        },
        "runtime_policy": runtime_policy,
        "events": {
            "sink": app_ctx.config.events.sink,
            "path": app_ctx.config.events.path,
        },
        "notifications": {
            "telegram": {
                "enabled": app_ctx.config.notifications.telegram.enabled,
                "bot_token": "",
                "bot_token_file": app_ctx.config.notifications.telegram.bot_token_file,
                "chat_id": "",
                "chat_id_file": app_ctx.config.notifications.telegram.chat_id_file,
                "min_severity": app_ctx.config.notifications.telegram.min_severity,
                "batch_size": telegram_batch_size,
            }
        },
        "backups": {
            "encryption": {
                "enabled": app_ctx.config.backups.encryption.enabled,
                "passphrase": "",
                "passphrase_file": app_ctx.config.backups.encryption.passphrase_file,
                "cipher": app_ctx.config.backups.encryption.cipher,
            },
            "offsite": {
                "enabled": app_ctx.config.backups.offsite.enabled,
                "provider": app_ctx.config.backups.offsite.provider,
                "bucket": app_ctx.config.backups.offsite.bucket,
                "prefix": app_ctx.config.backups.offsite.prefix,
                "region": app_ctx.config.backups.offsite.region,
                "endpoint_url": app_ctx.config.backups.offsite.endpoint_url,
                "access_key_id": "",
                "access_key_id_file": app_ctx.config.backups.offsite.access_key_id_file,
                "secret_access_key": "",
                "secret_access_key_file": app_ctx.config.backups.offsite.secret_access_key_file,
                "storage_class": app_ctx.config.backups.offsite.storage_class,
                "retention_days": app_ctx.config.backups.offsite.retention_days,
                "stale_hours": app_ctx.config.backups.offsite.stale_hours,
            },
        },
        "doctor": {
            "app_command_checks": [
                {
                    "name": item.name,
                    "command": item.command,
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in app_ctx.config.doctor.app_command_checks
            ],
            "heartbeat_checks": [
                {
                    "name": item.name,
                    "path": item.path,
                    "max_age_seconds": item.max_age_seconds,
                }
                for item in app_ctx.config.doctor.heartbeat_checks
            ],
            "queue_backlog_checks": [
                {
                    "name": item.name,
                    "connection": item.connection,
                    "queue": item.queue,
                    "max_size": item.max_size,
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in app_ctx.config.doctor.queue_backlog_checks
            ],
            "failed_job_checks": [
                {
                    "name": item.name,
                    "max_count": item.max_count,
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in app_ctx.config.doctor.failed_job_checks
            ],
        },
    }
    return yaml.safe_dump(payload, sort_keys=False)


def _relabel_managed_etc_paths(paths: list[Path]) -> None:
    relabel_managed_paths_for_selinux(
        paths,
        run_command=run_command,
        which=shutil.which,
        roots=[Path("/etc")],
    )


@bootstrap_app.command("init")
def init(
    ctx: typer.Context,
    profile: str = typer.Option("default", "--profile", help="Bootstrap profile: default|small-vps."),
    web: bool | None = typer.Option(None, "--web/--no-web", help="Install web stack group."),
    data: bool | None = typer.Option(None, "--data/--no-data", help="Install data stack group."),
    postgres: bool | None = typer.Option(None, "--postgres/--no-postgres", help="Install PostgreSQL stack group."),
    ops: bool | None = typer.Option(None, "--ops/--no-ops", help="Install ops stack group."),
    skip_stack: bool = typer.Option(False, "--skip-stack", help="Skip stack installation stage."),
    write_config: bool = typer.Option(
        True,
        "--write-config/--no-write-config",
        help="Write default config file if it does not exist.",
    ),
    config_path: Path = typer.Option(
        Path("/etc/larops/larops.yaml"),
        "--config-path",
        help="Target config file path for bootstrap.",
        dir_okay=False,
    ),
    domain: str | None = typer.Option(None, "--domain", help="Optional domain to create/deploy after stack setup."),
    source: Path = typer.Option(Path("."), "--source", help="Source directory for initial deploy.", file_okay=False),
    ref: str = typer.Option("main", "--ref", help="Deploy ref metadata for initial release."),
    force: bool = typer.Option(False, "--force", help="Force re-initialize existing app metadata."),
    apply: bool = typer.Option(False, "--apply", help="Apply bootstrap changes."),
) -> None:
    app_ctx: AppContext = ctx.obj
    host = socket.gethostname()
    resolved_profile = _resolve_bootstrap_profile(profile)
    default_groups = dict(resolved_profile["groups"])
    effective_web = default_groups["web"] if web is None else web
    effective_data = default_groups["data"] if data is None else data
    effective_postgres = default_groups["postgres"] if postgres is None else postgres
    effective_ops = default_groups["ops"] if ops is None else ops
    requested_groups = [] if skip_stack else resolve_groups(effective_web, effective_data, effective_postgres, effective_ops)
    try:
        stack_plan = build_stack_plan(requested_groups) if requested_groups else None
    except StackServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    source_path = source.resolve()

    app_plan = None
    if domain:
        app_plan = {
            "domain": domain,
            "source": str(source_path),
            "ref": ref,
        }

    app_ctx.event_emitter.emit(
        EventRecord(
            severity="info",
            event_type="bootstrap.init.started",
            host=host,
            message="Bootstrap init started.",
            metadata={
                "groups": requested_groups,
                "profile": str(resolved_profile["name"]),
                "write_config": write_config,
                "config_path": str(config_path),
                "domain": domain,
                "apply": apply,
                "dry_run": app_ctx.dry_run,
            },
        )
    )

    app_ctx.emit_output(
        "ok",
        "Bootstrap plan prepared.",
        profile=str(resolved_profile["name"]),
        write_config=write_config,
        config_path=str(config_path),
        stack_groups=requested_groups,
        group_defaults=default_groups,
        stack_platform=stack_plan.platform.label if stack_plan else None,
        stack_platform_support=stack_plan.platform.support_level if stack_plan else None,
        stack_commands=stack_plan.commands if stack_plan else [],
        app_plan=app_plan,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("bootstrap-init"):
            if write_config and not config_path.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(_default_config_yaml(app_ctx, profile_name=str(resolved_profile["name"])), encoding="utf-8")
                _relabel_managed_etc_paths([config_path])
                app_ctx.emit_output("ok", f"Created config file: {config_path}")

            if stack_plan:
                apply_stack_plan(stack_plan)
                app_ctx.emit_output("ok", "Stack installation stage completed.")

            if domain:
                paths = get_app_paths(
                    Path(app_ctx.config.deploy.releases_path),
                    Path(app_ctx.config.state_path),
                    domain,
                )
                phase_commands = build_deploy_phase_commands(app_ctx.config.deploy)
                payload = {
                    "domain": domain,
                    "php": "8.3",
                    "db": "mysql",
                    "ssl": False,
                    "created_at": datetime.now(UTC).isoformat(),
                }

                if not paths.metadata.exists() or force:
                    initialize_app(paths, payload, overwrite=force)
                else:
                    _ = load_metadata(paths.metadata)

                release_id, release_dir = prepare_release_candidate(
                    paths=paths,
                    source_path=source_path,
                    ref=ref,
                    shared_dirs=app_ctx.config.deploy.shared_dirs,
                    shared_files=app_ctx.config.deploy.shared_files,
                )
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
                app_ctx.emit_output(
                    "ok",
                    f"Bootstrap app deploy completed for {domain}",
                    release_id=release_id,
                    deleted_releases=deleted_releases,
                )
    except (CommandLockError, AppLifecycleError, ShellCommandError, ReleaseServiceError, SelinuxServiceError) as exc:
        app_ctx.event_emitter.emit(
            EventRecord(
                severity="error",
                event_type="bootstrap.init.failed",
                host=host,
                app=domain,
                message="Bootstrap init failed.",
                metadata={"error": str(exc)},
            )
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    app_ctx.event_emitter.emit(
        EventRecord(
            severity="info",
            event_type="bootstrap.init.completed",
            host=host,
            app=domain,
            message="Bootstrap init completed.",
            metadata={"groups": requested_groups, "domain": domain},
        )
    )
    app_ctx.emit_output("ok", "Bootstrap init completed.")
