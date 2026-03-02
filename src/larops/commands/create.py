from __future__ import annotations

import re
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

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
    switch_current_symlink,
)
from larops.services.runtime_process import RuntimeProcessError, enable_process
from larops.services.runtime_process import disable_process as runtime_disable_process
from larops.services.runtime_process import status_process as runtime_status_process
from larops.services.ssl_service import (
    SslServiceError,
    build_issue_command,
    run_issue,
)

create_app = typer.Typer(help="WordOps-style create shortcuts.")


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


def _validate_worker_options(*, concurrency: int, tries: int, timeout: int) -> None:
    if concurrency < 1:
        raise RuntimeProcessError("Worker concurrency must be >= 1.")
    if tries < 1:
        raise RuntimeProcessError("Worker tries must be >= 1.")
    if timeout < 1:
        raise RuntimeProcessError("Worker timeout must be >= 1 second.")


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
        steps.append(
            {
                "step": "tls_note",
                "status": "warn",
                "detail": "TLS cert may need manual cleanup if certbot already issued certificate.",
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
    deploy: bool = typer.Option(True, "--deploy/--no-deploy", help="Deploy source after create."),
    worker: bool = typer.Option(False, "--worker/--no-worker", "-w", help="Enable queue worker."),
    scheduler: bool = typer.Option(False, "--scheduler/--no-scheduler", "-s", help="Enable scheduler."),
    horizon: bool = typer.Option(False, "--horizon/--no-horizon", help="Enable horizon."),
    queue: str = typer.Option("default", "--queue", "-q", help="Worker queue."),
    concurrency: int = typer.Option(1, "--concurrency", "-c", help="Worker concurrency."),
    tries: int = typer.Option(3, "--tries", "-t", help="Worker tries."),
    timeout: int = typer.Option(90, "--timeout", help="Worker timeout in seconds."),
    schedule_command: str = typer.Option(
        "php artisan schedule:run",
        "--schedule-command",
        help="Scheduler command.",
    ),
    php: str = typer.Option("8.3", "--php", help="PHP runtime version."),
    db: str = typer.Option("mysql", "--db", help="Database engine."),
    ssl: bool = typer.Option(False, "--ssl", help="Enable SSL metadata flag."),
    letsencrypt: bool = typer.Option(
        False,
        "--letsencrypt",
        "-le",
        help="Issue Let's Encrypt certificate (WordOps-style).",
    ),
    le_email: str | None = typer.Option(None, "--le-email", help="Email for Let's Encrypt registration."),
    le_challenge: str = typer.Option("http", "--le-challenge", help="Challenge: http or dns."),
    le_dns_provider: str | None = typer.Option(None, "--le-dns-provider", help="DNS provider for dns challenge."),
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
    if not deploy and (worker or scheduler or horizon):
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
    if worker:
        try:
            _validate_worker_options(concurrency=concurrency, tries=tries, timeout=timeout)
        except RuntimeProcessError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc

    ssl_issue_command: list[str] | None = None
    if letsencrypt:
        try:
            ssl_issue_command = build_issue_command(
                domain=domain,
                email=le_email,
                challenge=le_challenge,
                dns_provider=le_dns_provider,
                staging=le_staging,
            )
        except SslServiceError as exc:
            app_ctx.emit_output("error", str(exc))
            raise typer.Exit(code=2) from exc

    source_path = (
        source.resolve()
        if source is not None
        else (Path(app_ctx.config.deploy.source_base_path) / domain).resolve()
    )
    paths = get_app_paths(
        Path(app_ctx.config.deploy.releases_path),
        Path(app_ctx.config.state_path),
        domain,
    )
    runtime_plan = {
        "worker": worker,
        "scheduler": scheduler,
        "horizon": horizon,
    }
    app_ctx.emit_output(
        "ok",
        f"Create site plan prepared for {domain}",
        domain=domain,
        source=str(source_path),
        ref=ref,
        deploy=deploy,
        runtime=runtime_plan,
        php=php,
        db=db,
        ssl=ssl or letsencrypt,
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
            "deploy": deploy,
            "runtime": runtime_plan,
        },
    )

    release_id: str | None = None
    deleted_releases: list[str] = []
    runtime_results: dict[str, dict] = {}
    rollback_steps: list[dict[str, str]] = []
    snapshot = _capture_atomic_snapshot(paths=paths, state_path=Path(app_ctx.config.state_path)) if atomic else None
    ssl_result: dict | None = None
    try:
        with CommandLock(_lock_name(domain)):
            metadata_payload = {
                "domain": domain,
                "php": php,
                "db": db,
                "ssl": ssl or letsencrypt,
                "created_at": datetime.now(UTC).isoformat(),
            }
            initialize_app(paths, metadata_payload, overwrite=force)

            if deploy:
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

            if worker:
                runtime_results["worker"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="worker",
                    options={
                        "queue": queue,
                        "concurrency": concurrency,
                        "tries": tries,
                        "timeout": timeout,
                    },
                )
            if scheduler:
                runtime_results["scheduler"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="scheduler",
                    options={"command": schedule_command},
                )
            if horizon:
                runtime_results["horizon"] = enable_process(
                    base_releases_path=Path(app_ctx.config.deploy.releases_path),
                    state_path=Path(app_ctx.config.state_path),
                    unit_dir=Path(app_ctx.config.systemd.unit_dir),
                    systemd_manage=app_ctx.config.systemd.manage,
                    service_user=app_ctx.config.systemd.user,
                    domain=domain,
                    process_type="horizon",
                    options={},
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
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except (AppLifecycleError, RuntimeProcessError, SslServiceError, ShellCommandError) as exc:
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
        metadata={"deploy": deploy, "runtime": runtime_plan},
    )
    app_ctx.emit_output(
        "ok",
        f"Create site completed for {domain}",
        deployed=deploy,
        release_id=release_id,
        deleted_releases=deleted_releases,
        runtime_enabled=runtime_results,
        ssl_result=ssl_result,
    )
