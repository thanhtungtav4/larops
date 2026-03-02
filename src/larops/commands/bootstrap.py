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
    deploy_release,
    get_app_paths,
    initialize_app,
    load_metadata,
    prune_releases,
    save_metadata,
)
from larops.services.stack_service import apply_stack_plan, build_stack_plan, resolve_groups

bootstrap_app = typer.Typer(help="Bootstrap empty servers like WordOps-style one-shot setup.")


def _default_config_yaml(app_ctx: AppContext) -> str:
    payload = {
        "environment": app_ctx.config.environment,
        "state_path": app_ctx.config.state_path,
        "deploy": {
            "releases_path": app_ctx.config.deploy.releases_path,
            "keep_releases": app_ctx.config.deploy.keep_releases,
            "health_check_path": app_ctx.config.deploy.health_check_path,
        },
        "events": {
            "sink": app_ctx.config.events.sink,
            "path": app_ctx.config.events.path,
        },
        "notifications": {
            "telegram": {
                "enabled": app_ctx.config.notifications.telegram.enabled,
                "bot_token": app_ctx.config.notifications.telegram.bot_token,
                "chat_id": app_ctx.config.notifications.telegram.chat_id,
            }
        },
    }
    return yaml.safe_dump(payload, sort_keys=False)


@bootstrap_app.command("init")
def init(
    ctx: typer.Context,
    web: bool = typer.Option(True, "--web/--no-web", help="Install web stack group."),
    data: bool = typer.Option(True, "--data/--no-data", help="Install data stack group."),
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
    requested_groups = [] if skip_stack else resolve_groups(web, data, ops)
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

                release_id = deploy_release(paths, source_path, ref)
                deleted_releases = prune_releases(paths, app_ctx.config.deploy.keep_releases)

                metadata = load_metadata(paths.metadata)
                metadata["last_deploy"] = {
                    "release_id": release_id,
                    "ref": ref,
                    "deployed_at": datetime.now(UTC).isoformat(),
                    "source": str(source_path),
                }
                save_metadata(paths.metadata, metadata)
                app_ctx.emit_output(
                    "ok",
                    f"Bootstrap app deploy completed for {domain}",
                    release_id=release_id,
                    deleted_releases=deleted_releases,
                )
    except (CommandLockError, AppLifecycleError, ShellCommandError) as exc:
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

