from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
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
from larops.services.stack_service import apply_stack_plan, build_stack_plan, resolve_groups

bootstrap_app = typer.Typer(help="Bootstrap empty servers like WordOps-style one-shot setup.")


def _default_config_yaml(app_ctx: AppContext) -> str:
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
                "batch_size": app_ctx.config.notifications.telegram.batch_size,
            }
        },
        "doctor": {
            "app_command_checks": [
                {
                    "name": item.name,
                    "command": item.command,
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in app_ctx.config.doctor.app_command_checks
            ]
        },
    }
    return yaml.safe_dump(payload, sort_keys=False)


@bootstrap_app.command("init")
def init(
    ctx: typer.Context,
    web: bool = typer.Option(True, "--web/--no-web", help="Install web stack group."),
    data: bool = typer.Option(True, "--data/--no-data", help="Install data stack group."),
    postgres: bool = typer.Option(False, "--postgres/--no-postgres", help="Install PostgreSQL stack group."),
    ops: bool = typer.Option(True, "--ops/--no-ops", help="Install ops stack group."),
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
    requested_groups = [] if skip_stack else resolve_groups(web, data, postgres, ops)
    stack_plan = build_stack_plan(requested_groups) if requested_groups else None
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
        write_config=write_config,
        config_path=str(config_path),
        stack_groups=requested_groups,
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
                config_path.write_text(_default_config_yaml(app_ctx), encoding="utf-8")
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
    except (CommandLockError, AppLifecycleError, ShellCommandError, ReleaseServiceError) as exc:
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
