from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError, run_command
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.app_bootstrap_service import (
    ensure_shared_app_key,
    resolve_bootstrap_app_commands,
    sync_env_from_database_provision,
)
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
from larops.services.permissions_service import ensure_site_writable_permissions
from larops.services.permissions_service import PermissionServiceError
from larops.services.env_file_service import EnvFileServiceError
from larops.services.nginx_site_service import (
    NginxSiteServiceError,
    apply_nginx_site_config,
    is_managed_nginx_site_config,
    resolve_nginx_site_paths,
)
from larops.services.release_service import (
    ReleaseServiceError,
    build_deploy_phase_commands,
    build_rollback_phase_commands,
    prepare_release_candidate,
    resolve_build_commands_for_release,
    refresh_runtime_after_activate,
    remove_release_dir,
    run_http_health_check,
    run_release_commands,
    validate_release_build_requirements_for_release,
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
    last_bootstrap = metadata.get("last_bootstrap") if isinstance(metadata.get("last_bootstrap"), dict) else None
    database_provision = metadata.get("database_provision") if isinstance(metadata.get("database_provision"), dict) else None
    env_sync = metadata.get("env_sync") if isinstance(metadata.get("env_sync"), dict) else None

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
            "smoke_checks": last_deploy.get("smoke_checks") if last_deploy else None,
        },
        "bootstrap": last_bootstrap,
        "database_provision": database_provision,
        "env_sync": env_sync,
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
    env_sync = report.get("env_sync")
    health_check = deploy.get("health_check") if isinstance(deploy.get("health_check"), dict) else None
    smoke_checks = deploy.get("smoke_checks") if isinstance(deploy.get("smoke_checks"), dict) else None
    bootstrap = report.get("bootstrap") if isinstance(report.get("bootstrap"), dict) else None
    https_enabled = bool(app.get("ssl")) or bool(web.get("certificate_present"))
    scheme = "https" if https_enabled else "http"

    lines = [
        f"  site: {scheme}://{report['domain']}",
        f"  release: {releases['current'] or 'none'} ({releases['count']} total)",
        f"  php: {app.get('php') or 'unknown'}",
        f"  paths: current={paths['current']}, env={env_sync.get('env_file', str(Path(paths['shared']) / '.env')) if env_sync else str(Path(paths['shared']) / '.env')}",
        f"  metadata: {paths['metadata']}",
        f"  web: nginx={web['nginx_server_config_file']}, cert={web['certificate_present']}",
    ]
    if profile:
        lines.extend(
            [
                f"  profile: {profile.get('preset') or 'custom'} / {profile.get('type') or 'custom'} / cache={profile.get('cache') or 'none'}",
            ]
        )
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
        if runtime:
            enabled = ", ".join(sorted(name for name, enabled in runtime.items() if enabled))
            lines.append(f"  runtime: {enabled or 'none'}")
    if deploy.get("source"):
        lines.extend(
            [
                f"  deploy: ref={deploy.get('ref') or 'unknown'} source={deploy['source']}",
                f"  deployed at: {deploy.get('deployed_at') or 'unknown'}",
            ]
        )
    if health_check:
        lines.append(f"  health check: {health_check.get('status', 'unknown')}")
    if bootstrap:
        lines.append(f"  bootstrap: {bootstrap.get('status', 'unknown')} at {bootstrap.get('bootstrapped_at', 'unknown')}")
    if db_provision:
        lines.extend(
            [
                f"  db: {app.get('db') or 'unknown'} {db_provision.get('database', 'unknown')} as {db_provision.get('user', 'unknown')}",
                f"  db host: {db_provision.get('host', 'unknown')}:{db_provision.get('port', 'unknown')}",
                f"  db secrets: credential={db_provision.get('credential_file', 'unknown')}, password={db_provision.get('password_file', 'unknown')}",
            ]
        )
    if smoke_checks:
        http_probe = smoke_checks.get("http")
        https_probe = smoke_checks.get("https")
        if isinstance(http_probe, dict):
            lines.append(f"  smoke http: {_format_app_info_probe(http_probe)}")
        if isinstance(https_probe, dict):
            lines.append(f"  smoke https: {_format_app_info_probe(https_probe)}")
    for line in lines:
        app_ctx.emit_output("ok", line)


def _format_app_info_probe(result: dict) -> str:
    if result.get("status") == "failed":
        return f"failed ({result.get('detail', 'unknown')})"
    http_status = result.get("http_status")
    if http_status is None:
        return str(result.get("status", "unknown"))
    return str(http_status)


def _build_manual_bootstrap_commands(
    *,
    current_path: Path,
    shared_env_file: Path,
    seed: bool,
    seeder_class: str | None,
    skip_migrate: bool,
    skip_package_discover: bool,
    skip_optimize: bool,
) -> list[str]:
    return resolve_bootstrap_app_commands(
        current_path=current_path,
        shared_env_file=shared_env_file,
        bootstrap_mode="eager",
        seed=seed,
        seeder_class=seeder_class,
        skip_migrate=skip_migrate,
        skip_package_discover=skip_package_discover,
        skip_optimize=skip_optimize,
    )


def _resolve_refresh_source_path(
    *,
    domain: str,
    explicit_source: Path | None,
    metadata: dict[str, Any],
    app_ctx: AppContext,
) -> Path:
    if explicit_source is not None:
        resolved = explicit_source.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise AppLifecycleError(f"Source path does not exist or is not a directory: {resolved}")
        return resolved

    last_deploy = metadata.get("last_deploy") if isinstance(metadata.get("last_deploy"), dict) else None
    source_raw = last_deploy.get("source") if last_deploy else None
    if isinstance(source_raw, str) and source_raw.strip():
        return Path(source_raw).resolve()

    resolved = (Path(app_ctx.config.deploy.source_base_path) / domain).resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise AppLifecycleError(f"Source path does not exist or is not a directory: {resolved}")
    return resolved


def _git_pull_source(*, source_path: Path, ref: str) -> dict[str, str]:
    if not (source_path / ".git").exists():
        raise AppLifecycleError(f"Source path is not a git repository: {source_path}")

    before = run_command(["git", "-C", str(source_path), "rev-parse", "HEAD"], check=True)
    before_ref = (before.stdout or "").strip()
    run_command(["git", "-C", str(source_path), "pull", "--ff-only", "origin", ref], check=True)
    after = run_command(["git", "-C", str(source_path), "rev-parse", "HEAD"], check=True)
    after_ref = (after.stdout or "").strip()
    return {"before": before_ref, "after": after_ref}


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
    permissions_result: dict | None = None
    nginx_result: dict | None = None
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
            build_commands = resolve_build_commands_for_release(
                config=app_ctx.config.deploy,
                release_dir=release_dir,
                commands=phase_commands["build"],
            )
            validate_release_build_requirements_for_release(
                config=app_ctx.config.deploy,
                release_dir=release_dir,
                commands=build_commands,
            )
            build_reports = run_release_commands(
                workdir=release_dir,
                phase="build",
                commands=build_commands,
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
            permissions_result = ensure_site_writable_permissions(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                owner=app_ctx.config.systemd.user,
                group=app_ctx.config.systemd.user,
            )
            if is_managed_nginx_site_config(domain):
                nginx_result = apply_nginx_site_config(
                    domain=domain,
                    current_path=current_path,
                    php_version=str(metadata.get("php") or "8.3"),
                    https_enabled=bool(metadata.get("ssl")) or default_cert_file(domain).exists(),
                    force=False,
                )
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
                "permissions": permissions_result,
                "nginx": nginx_result,
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
    except (AppLifecycleError, ReleaseServiceError, NginxSiteServiceError) as exc:
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


@app_cmd.command("refresh")
def refresh(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    ref: str = typer.Option("main", "--ref", help="Git ref to pull and deploy."),
    source: Path | None = typer.Option(
        None,
        "--source",
        help="Source directory. Defaults to last deploy source or deploy.source_base_path/<domain>.",
        exists=False,
        file_okay=False,
    ),
    git_pull: bool = typer.Option(True, "--git-pull/--no-git-pull", help="Pull source from git before deploy."),
    seed: bool = typer.Option(False, "--seed", help="Run php artisan db:seed --force after deploy."),
    seeder_class: str | None = typer.Option(None, "--seeder-class", help="Seeder class to use with --seed."),
    skip_migrate: bool = typer.Option(False, "--skip-migrate", help="Skip php artisan migrate --force."),
    skip_package_discover: bool = typer.Option(
        False,
        "--skip-package-discover",
        help="Skip php artisan package:discover --ansi.",
    ),
    skip_optimize: bool = typer.Option(False, "--skip-optimize", help="Skip optimize:clear and optimize."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply refresh. Without this flag, command runs in plan mode.",
    ),
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

    try:
        source_path = _resolve_refresh_source_path(domain=domain, explicit_source=source, metadata=metadata, app_ctx=app_ctx)
    except AppLifecycleError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.emit_output(
        "ok",
        f"Refresh plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        ref=ref,
        git_pull=git_pull,
        seed=seed,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if not app_ctx.json_output:
        app_ctx.emit_output("ok", f"  source: {source_path}")
        app_ctx.emit_output("ok", f"  ref: {ref}")
        app_ctx.emit_output("ok", f"  git pull: {'yes' if git_pull else 'no'}")
        app_ctx.emit_output("ok", f"  bootstrap seed: {'yes' if seed or seeder_class else 'no'}")

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    git_result: dict[str, str] | None = None
    try:
        _emit_event(
            app_ctx,
            severity="info",
            event_type="app.refresh.started",
            domain=domain,
            message="Application refresh started.",
            metadata={"ref": ref, "source": str(source_path), "git_pull": git_pull},
        )
        if git_pull:
            git_result = _git_pull_source(source_path=source_path, ref=ref)
            app_ctx.emit_output(
                "ok",
                "Git source updated.",
                before=git_result["before"],
                after=git_result["after"],
            )
            if not app_ctx.json_output:
                app_ctx.emit_output("ok", f"  git: {git_result['before']} -> {git_result['after']}")

        deploy(ctx, domain=domain, ref=ref, source=source_path, apply=True)
        bootstrap(
            ctx,
            domain=domain,
            seed=seed,
            seeder_class=seeder_class,
            skip_migrate=skip_migrate,
            skip_package_discover=skip_package_discover,
            skip_optimize=skip_optimize,
            apply=True,
        )
    except typer.Exit:
        raise
    except (AppLifecycleError, ReleaseServiceError, ShellCommandError) as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="app.refresh.failed",
            domain=domain,
            message="Application refresh failed.",
            metadata={"error": str(exc), "source": str(source_path), "git_pull": git_pull},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.refresh.completed",
        domain=domain,
        message="Application refresh completed.",
        metadata={"source": str(source_path), "git": git_result},
    )
    app_ctx.emit_output("ok", f"Refresh completed for {domain}", source=str(source_path), git=git_result)


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
    except (AppLifecycleError, ReleaseServiceError, PermissionServiceError, EnvFileServiceError) as exc:
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


@app_cmd.command("bootstrap")
def bootstrap(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Application domain."),
    seed: bool = typer.Option(False, "--seed", help="Run php artisan db:seed --force after bootstrap."),
    seeder_class: str | None = typer.Option(None, "--seeder-class", help="Seeder class to use with --seed."),
    skip_migrate: bool = typer.Option(False, "--skip-migrate", help="Skip php artisan migrate --force."),
    skip_package_discover: bool = typer.Option(
        False,
        "--skip-package-discover",
        help="Skip php artisan package:discover --ansi.",
    ),
    skip_optimize: bool = typer.Option(False, "--skip-optimize", help="Skip optimize:clear and optimize."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply app bootstrap. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    try:
        metadata = load_metadata(paths.metadata)
        current_path = paths.current.resolve(strict=True)
    except (AppLifecycleError, FileNotFoundError) as exc:
        app_ctx.emit_output("error", f"Current release is not available for {domain}: {exc}")
        raise typer.Exit(code=2) from exc

    if seeder_class:
        seed = True

    database_provision = metadata.get("database_provision") if isinstance(metadata.get("database_provision"), dict) else None
    shared_env_file = paths.shared / ".env"
    preview_commands = _build_manual_bootstrap_commands(
        current_path=current_path,
        shared_env_file=shared_env_file,
        seed=seed,
        seeder_class=seeder_class,
        skip_migrate=skip_migrate,
        skip_package_discover=skip_package_discover,
        skip_optimize=skip_optimize,
    )

    app_ctx.emit_output(
        "ok",
        f"App bootstrap plan prepared for {domain}",
        domain=domain,
        current_release=get_current_release(paths),
        current_path=str(current_path),
        env_file=str(shared_env_file),
        commands=preview_commands,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if not app_ctx.json_output:
        app_ctx.emit_output("ok", f"  release: {get_current_release(paths) or 'unknown'}")
        app_ctx.emit_output("ok", f"  env: {shared_env_file}")
        app_ctx.emit_output("ok", f"  commands: {len(preview_commands)}")
        for command in preview_commands:
            app_ctx.emit_output("ok", f"    - {command}")

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    env_sync_result: dict[str, Any] | None = None
    app_key_sync: dict[str, Any] | None = None
    permissions_result: dict[str, Any] | None = None
    bootstrap_reports: list[dict[str, Any]] = []
    executed_commands: list[str] = []
    try:
        with CommandLock(_lock_name(domain)):
            if database_provision is not None:
                synced = sync_env_from_database_provision(
                    shared_env_file=shared_env_file,
                    database_provision=database_provision,
                )
                if synced is not None:
                    env_sync_result, _password = synced

            app_key_sync = ensure_shared_app_key(shared_env_file)
            executed_commands = _build_manual_bootstrap_commands(
                current_path=current_path,
                shared_env_file=shared_env_file,
                seed=seed,
                seeder_class=seeder_class,
                skip_migrate=skip_migrate,
                skip_package_discover=skip_package_discover,
                skip_optimize=skip_optimize,
            )
            permissions_result = ensure_site_writable_permissions(
                base_releases_path=Path(app_ctx.config.deploy.releases_path),
                state_path=Path(app_ctx.config.state_path),
                domain=domain,
                owner=app_ctx.config.systemd.user,
                group=app_ctx.config.systemd.user,
            )
            bootstrap_reports = run_release_commands(
                workdir=current_path,
                phase="app-bootstrap",
                commands=executed_commands,
                timeout_seconds=app_ctx.config.deploy.post_activate_timeout_seconds,
            )
            metadata = load_metadata(paths.metadata)
            if env_sync_result is not None:
                metadata["env_sync"] = env_sync_result
            metadata["last_bootstrap"] = {
                "bootstrapped_at": datetime.now(UTC).isoformat(),
                "status": "completed" if executed_commands else "skipped",
                "commands": executed_commands,
                "reports": bootstrap_reports,
                "permissions": permissions_result,
                "env_sync": env_sync_result,
                "app_key_sync": app_key_sync,
            }
            save_metadata(paths.metadata, metadata)
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (AppLifecycleError, ReleaseServiceError) as exc:
        _emit_event(
            app_ctx,
            severity="error",
            event_type="app.bootstrap.failed",
            domain=domain,
            message="App bootstrap failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    _emit_event(
        app_ctx,
        severity="info",
        event_type="app.bootstrap.completed",
        domain=domain,
        message="App bootstrap completed.",
        metadata={"commands": executed_commands},
    )
    app_ctx.emit_output(
        "ok",
        f"App bootstrap completed for {domain}",
        commands=executed_commands,
        reports=bootstrap_reports,
        env_sync=env_sync_result,
        permissions=permissions_result,
    )
    if not app_ctx.json_output:
        app_ctx.emit_output("ok", f"  commands run: {len(executed_commands)}")
        if env_sync_result is not None:
            app_ctx.emit_output("ok", f"  env synced: {', '.join(env_sync_result.get('updated_keys', []))}")
        if permissions_result is not None:
            app_ctx.emit_output("ok", f"  permissions: {permissions_result.get('writable_mode')}")


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
