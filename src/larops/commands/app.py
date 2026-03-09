from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.app_lifecycle import (
    AppLifecycleError,
    activate_release,
    get_app_paths,
    get_current_release,
    initialize_app,
    list_releases,
    load_metadata,
    prune_releases,
    resolve_rollback_target,
    rollback_release,
    save_metadata,
)
from larops.services.nginx_site_service import resolve_nginx_site_paths
from larops.services.release_service import (
    ReleaseServiceError,
    build_deploy_phase_commands,
    build_rollback_phase_commands,
    prepare_release_candidate,
    refresh_runtime_after_activate,
    remove_release_dir,
    run_http_health_check,
    run_release_commands,
    write_release_manifest,
)
from larops.services.ssl_service import default_cert_file

app_cmd = typer.Typer(help="Manage Laravel application lifecycle.")


def _lock_name(domain: str) -> str:
    return f"app-{re.sub(r'[^a-zA-Z0-9]+', '-', domain)}"


def _emit_event(
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


def _build_app_info_report(*, domain: str, paths: Path, releases: list[str], current: str | None, metadata: dict) -> dict:
    app_paths = paths
    nginx_paths = resolve_nginx_site_paths(domain)
    cert_file = default_cert_file(domain)
    profile = metadata.get("profile") if isinstance(metadata.get("profile"), dict) else None
    last_deploy = metadata.get("last_deploy") if isinstance(metadata.get("last_deploy"), dict) else None
    database_provision = metadata.get("database_provision") if isinstance(metadata.get("database_provision"), dict) else None

    return {
        "domain": domain,
        "paths": {
            "root": str(app_paths.root),
            "releases": str(app_paths.releases),
            "shared": str(app_paths.shared),
            "current": str(app_paths.current),
            "metadata": str(app_paths.metadata),
        },
        "releases": {
            "count": len(releases),
            "current": current,
            "all": releases,
        },
        "app": {
            "php": metadata.get("php"),
            "db": metadata.get("db"),
            "ssl": metadata.get("ssl"),
            "created_at": metadata.get("created_at"),
        },
        "profile": profile,
        "deploy": {
            "source": last_deploy.get("source") if last_deploy else None,
            "ref": last_deploy.get("ref") if last_deploy else None,
            "deployed_at": last_deploy.get("deployed_at") if last_deploy else None,
            "health_check": last_deploy.get("health_check") if last_deploy else None,
        },
        "database_provision": database_provision,
        "web": {
            "nginx_server_config_file": str(nginx_paths.server_config_file),
            "nginx_enabled_site_file": str(nginx_paths.enabled_site_file),
            "nginx_activation_mode": nginx_paths.activation_mode,
            "certificate_file": str(cert_file),
            "certificate_present": cert_file.exists(),
        },
    }


def _emit_app_info_summary(app_ctx: AppContext, *, report: dict) -> None:
    if app_ctx.json_output:
        return

    paths = report["paths"]
    releases = report["releases"]
    app = report["app"]
    deploy = report["deploy"]
    web = report["web"]
    profile = report.get("profile")
    db_provision = report.get("database_provision")

    lines = [
        f"  app root: {paths['root']}",
        f"  shared path: {paths['shared']}",
        f"  current path: {paths['current']}",
        f"  metadata: {paths['metadata']}",
        f"  releases: {releases['count']}",
        f"  current release: {releases['current'] or 'none'}",
        f"  php: {app.get('php') or 'unknown'}",
        f"  db engine: {app.get('db') or 'unknown'}",
        f"  ssl enabled: {bool(app.get('ssl'))}",
    ]
    if profile:
        lines.extend(
            [
                f"  profile preset: {profile.get('preset') or 'custom'}",
                f"  profile type: {profile.get('type') or 'custom'}",
                f"  profile cache: {profile.get('cache') or 'none'}",
            ]
        )
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
        if runtime:
            enabled = ", ".join(sorted(name for name, enabled in runtime.items() if enabled))
            lines.append(f"  runtime profile: {enabled or 'none'}")
    if deploy.get("source"):
        lines.extend(
            [
                f"  deploy source: {deploy['source']}",
                f"  deploy ref: {deploy.get('ref') or 'unknown'}",
                f"  deployed at: {deploy.get('deployed_at') or 'unknown'}",
            ]
        )
        health_check = deploy.get("health_check")
        if isinstance(health_check, dict) and health_check:
            lines.append(f"  health check: {health_check.get('status', 'unknown')}")
    lines.extend(
        [
            f"  nginx config: {web['nginx_server_config_file']}",
            f"  nginx activation: {web['nginx_activation_mode']}",
            f"  cert file: {web['certificate_file']}",
            f"  cert present: {web['certificate_present']}",
        ]
    )
    if db_provision:
        lines.extend(
            [
                f"  db name: {db_provision.get('database', 'unknown')}",
                f"  db user: {db_provision.get('user', 'unknown')}",
                f"  db host: {db_provision.get('host', 'unknown')}:{db_provision.get('port', 'unknown')}",
                f"  db credential file: {db_provision.get('credential_file', 'unknown')}",
                f"  db password file: {db_provision.get('password_file', 'unknown')}",
            ]
        )
    for line in lines:
        app_ctx.emit_output("ok", line)


@app_cmd.command("create")
def create(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    php: str = typer.Option("8.3", "--php", help="PHP runtime version."),
    db: str = typer.Option("mysql", "--db", help="Database engine."),
    ssl: bool = typer.Option(False, "--ssl", help="Issue SSL certificate."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing metadata."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply filesystem changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    payload = {
        "domain": domain,
        "php": php,
        "db": db,
        "ssl": ssl,
        "created_at": datetime.now(UTC).isoformat(),
    }

    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.create.started",
        domain=domain,
        message="App create started.",
        metadata={"apply": apply, "dry_run": app_ctx.dry_run},
    )
    app_ctx.emit_output(
        "ok",
        f"App create plan prepared for {domain}",
        domain=domain,
        paths={
            "root": str(paths.root),
            "releases": str(paths.releases),
            "shared": str(paths.shared),
            "metadata": str(paths.metadata),
        },
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock(_lock_name(domain)):
            initialize_app(paths, payload, overwrite=force)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except AppLifecycleError as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="app.create.failed",
            domain=domain,
            message="App create failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.create.completed",
        domain=domain,
        message="App create completed.",
    )
    app_ctx.emit_output("ok", f"Application created: {domain}")


@app_cmd.command("deploy")
def deploy(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    ref: str = typer.Option("main", "--ref", help="Git ref to deploy."),
    source: Path = typer.Option(
        Path("."),
        "--source",
        help="Local source directory to deploy.",
        exists=False,
        file_okay=False,
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply filesystem changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    source_path = source.resolve()
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    keep_releases = app_ctx.config.deploy.keep_releases

    _emit_event(
        app_ctx,
        severity="info",
        event_type="deploy.started",
        domain=domain,
        message="Deploy started.",
        metadata={"apply": apply, "dry_run": app_ctx.dry_run, "ref": ref},
    )

    app_ctx.emit_output(
        "ok",
        f"Deploy plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        ref=ref,
        keep_releases=keep_releases,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    release_id: str | None = None
    release_dir: Path | None = None
    try:
        with CommandLock(_lock_name(domain)):
            metadata = load_metadata(paths.metadata)
            previous_release = get_current_release(paths)
            phase_commands = build_deploy_phase_commands(app_ctx.config.deploy)
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
                if app_ctx.config.deploy.rollback_on_health_check_failure and previous_release:
                    rollback_release(paths, previous_release)
                    remove_release_dir(release_dir)
                    raise AppLifecycleError(
                        f"Deploy health check failed after activate and rollback was applied: {health_check.get('detail', 'unknown')}"
                    )
                raise AppLifecycleError(f"Deploy health check failed: {health_check.get('detail', 'unknown')}")
            verify_reports: list[dict] = []
            verify_status = "skipped"
            if phase_commands["verify"]:
                try:
                    verify_reports = run_release_commands(
                        workdir=current_path,
                        phase="verify",
                        commands=phase_commands["verify"],
                        timeout_seconds=app_ctx.config.deploy.verify_timeout_seconds,
                    )
                    verify_status = "passed"
                except ReleaseServiceError as exc:
                    verify_status = "failed"
                    if app_ctx.config.deploy.rollback_on_verify_failure and previous_release:
                        rollback_release(paths, previous_release)
                        remove_release_dir(release_dir)
                        raise AppLifecycleError(
                            f"Deploy verify phase failed after activate and rollback was applied: {exc}"
                        ) from exc
                    raise
            runtime_refresh = refresh_runtime_after_activate(
                state_path=Path(app_ctx.config.state_path),
                current_path=current_path,
                domain=domain,
                strategy=app_ctx.config.deploy.runtime_refresh_strategy,
                systemd_manage=app_ctx.config.systemd.manage,
            )
            deleted_releases = prune_releases(paths, keep_releases)

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
                "runtime_refresh": runtime_refresh,
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
                    "verify_status": verify_status,
                    "runtime_refresh": runtime_refresh,
                },
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (AppLifecycleError, ReleaseServiceError) as exc:
        if release_dir is not None and release_dir.exists():
            write_release_manifest(
                release_dir,
                {
                    "status": "failed",
                    "release_id": release_id,
                    "ref": ref,
                    "source": str(source_path),
                    "failed_at": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                },
            )
        _emit_event(
            app_ctx,
            severity="error",
            event_type="deploy.failed",
            domain=domain,
            message="Deploy failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="deploy.completed",
        domain=domain,
        message="Deploy completed.",
        metadata={"release_id": release_id, "deleted_releases": deleted_releases},
    )
    app_ctx.emit_output(
        "ok",
        f"Deployment completed for {domain}",
        release_id=release_id,
        deleted_releases=deleted_releases,
        health_check=health_check,
        runtime_refresh=runtime_refresh,
    )


@app_cmd.command("rollback")
def rollback(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    to: str = typer.Option("previous", "--to", help="Release id or 'previous'."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply rollback. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        target = resolve_rollback_target(paths, to)
    except AppLifecycleError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output(
        "ok",
        f"Rollback plan prepared for {domain}",
        domain=domain,
        current=get_current_release(paths),
        target=target,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    _emit_event(
        app_ctx,
        severity="warn",
        event_type="rollback.started",
        domain=domain,
        message="Rollback started.",
        metadata={"target": target},
    )

    try:
        current_release_before = get_current_release(paths)
        with CommandLock(_lock_name(domain)):
            rollback_phase_commands = build_rollback_phase_commands(app_ctx.config.deploy)
            rollback_release(paths, target)
            current_path = paths.current.resolve(strict=True)
            post_activate_reports = run_release_commands(
                workdir=current_path,
                phase="post-activate",
                commands=rollback_phase_commands["post_activate"],
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
                if app_ctx.config.deploy.rollback_on_health_check_failure and current_release_before:
                    rollback_release(paths, current_release_before)
                    raise AppLifecycleError(
                        f"Rollback health check failed and previous release was restored: {health_check.get('detail', 'unknown')}"
                    )
                raise AppLifecycleError(f"Rollback health check failed: {health_check.get('detail', 'unknown')}")
            verify_reports: list[dict] = []
            if rollback_phase_commands["verify"]:
                try:
                    verify_reports = run_release_commands(
                        workdir=current_path,
                        phase="verify",
                        commands=rollback_phase_commands["verify"],
                        timeout_seconds=app_ctx.config.deploy.verify_timeout_seconds,
                    )
                except ReleaseServiceError as exc:
                    if app_ctx.config.deploy.rollback_on_verify_failure and current_release_before:
                        rollback_release(paths, current_release_before)
                        raise AppLifecycleError(
                            f"Rollback verify phase failed and previous release was restored: {exc}"
                        ) from exc
                    raise
            runtime_refresh = refresh_runtime_after_activate(
                state_path=Path(app_ctx.config.state_path),
                current_path=current_path,
                domain=domain,
                strategy=app_ctx.config.deploy.runtime_refresh_strategy,
                systemd_manage=app_ctx.config.systemd.manage,
            )
            metadata = load_metadata(paths.metadata)
            metadata["last_rollback"] = {
                "target": target,
                "rolled_back_at": datetime.now(UTC).isoformat(),
                "post_activate_reports": post_activate_reports,
                "verify_reports": verify_reports,
                "health_check": health_check,
                "runtime_refresh": runtime_refresh,
            }
            save_metadata(paths.metadata, metadata)
            write_release_manifest(
                current_path,
                {
                    "status": "rolled-back-active",
                    "release_id": target,
                    "rolled_back_at": datetime.now(UTC).isoformat(),
                    "phase_reports": {
                        "post_activate": post_activate_reports,
                        "verify": verify_reports,
                    },
                    "health_check": health_check,
                    "runtime_refresh": runtime_refresh,
                },
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (AppLifecycleError, ReleaseServiceError) as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="rollback.failed",
            domain=domain,
            message="Rollback failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="rollback.completed",
        domain=domain,
        message="Rollback completed.",
        metadata={"target": target},
    )
    app_ctx.emit_output(
        "ok",
        f"Rollback completed for {domain}",
        target=target,
        health_check=health_check,
        runtime_refresh=runtime_refresh,
    )


@app_cmd.command("info")
def info(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        metadata = load_metadata(paths.metadata)
    except AppLifecycleError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    releases = list_releases(paths)
    current = get_current_release(paths)
    report = _build_app_info_report(domain=domain, paths=paths, releases=releases, current=current, metadata=metadata)

    app_ctx.emit_output(
        "ok",
        f"Application info: {domain}",
        domain=domain,
        root=str(paths.root),
        releases=releases,
        releases_count=len(releases),
        current_release=current,
        metadata=metadata,
        report=report,
    )
    _emit_app_info_summary(app_ctx, report=report)
