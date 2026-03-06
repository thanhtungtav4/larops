from __future__ import annotations

from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.core.locks import CommandLock, CommandLockError
from larops.runtime import AppContext
from larops.services.observability_logs_service import (
    ObservabilityLogsError,
    default_laravel_log_patterns,
    default_nginx_access_logs,
    default_nginx_error_logs,
    disable_logs_shipping,
    enable_logs_shipping,
    status_logs_shipping,
    supported_sinks,
)

observability_app = typer.Typer(help="Manage observability integrations.")
logs_app = typer.Typer(help="Ship logs to a remote collector with Vector.")
observability_app.add_typer(logs_app, name="logs")


def _resolve_cli_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


def _resolve_logs_config_file(config_file: Path | None) -> Path:
    return config_file or Path("/etc/larops/vector/logs.yaml")


def _resolve_logs_data_dir(app_ctx: AppContext, data_dir: Path | None) -> Path:
    return data_dir or (Path(app_ctx.config.state_path) / "observability" / "vector")


def _observability_root(app_ctx: AppContext) -> Path:
    return Path(app_ctx.config.state_path) / "observability"


def _resolve_laravel_logs(app_ctx: AppContext, laravel_logs: list[str] | None) -> list[str]:
    if laravel_logs:
        return laravel_logs
    return default_laravel_log_patterns(Path(app_ctx.config.deploy.releases_path))


def _resolve_nginx_access_logs(nginx_access_logs: list[str] | None) -> list[str]:
    if nginx_access_logs:
        return nginx_access_logs
    return default_nginx_access_logs()


def _resolve_nginx_error_logs(nginx_error_logs: list[str] | None) -> list[str]:
    if nginx_error_logs:
        return nginx_error_logs
    return default_nginx_error_logs()


@logs_app.command("enable")
def logs_enable(
    ctx: typer.Context,
    sink: str = typer.Option("vector", "--sink", help="Remote sink type: vector or http."),
    vector_address: str | None = typer.Option(None, "--vector-address", help="Vector sink address host:port."),
    http_uri: str | None = typer.Option(None, "--http-uri", help="HTTP ingest endpoint when --sink=http."),
    http_env_file: Path | None = typer.Option(
        None,
        "--http-env-file",
        help="Optional env file that contains bearer token for HTTP sink auth.",
        dir_okay=False,
    ),
    http_bearer_token_env_var: str = typer.Option(
        "LAROPS_VECTOR_HTTP_TOKEN",
        "--http-bearer-token-env-var",
        help="Environment variable name containing the HTTP bearer token.",
    ),
    vector_bin: str = typer.Option("/usr/bin/vector", "--vector-bin", help="Vector executable path."),
    config_file: Path | None = typer.Option(
        None,
        "--config-file",
        help="Vector config file written by LarOps.",
        dir_okay=False,
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Vector checkpoint and disk buffer directory.",
        file_okay=False,
    ),
    laravel_log: list[str] | None = typer.Option(None, "--laravel-log", help="Laravel log glob/pattern. Repeatable."),
    nginx_access_log: list[str] | None = typer.Option(
        None,
        "--nginx-access-log",
        help="Nginx access log path or glob. Repeatable.",
    ),
    nginx_error_log: list[str] | None = typer.Option(
        None,
        "--nginx-error-log",
        help="Nginx error log path or glob. Repeatable.",
    ),
    extra_log: list[str] | None = typer.Option(None, "--extra-log", help="Extra log path or glob. Repeatable."),
    service_user: str = typer.Option(
        "root",
        "--service-user",
        help="System user for the Vector service. Root is recommended when reading nginx logs.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply log shipping setup."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    resolved_config_file = _resolve_logs_config_file(config_file)
    resolved_data_dir = _resolve_logs_data_dir(app_ctx, data_dir)
    resolved_laravel_logs = _resolve_laravel_logs(app_ctx, laravel_log)
    resolved_nginx_access_logs = _resolve_nginx_access_logs(nginx_access_log)
    resolved_nginx_error_logs = _resolve_nginx_error_logs(nginx_error_log)
    resolved_extra_logs = list(extra_log or [])

    app_ctx.emit_output(
        "ok",
        "Observability logs enable plan prepared.",
        sink=sink,
        supported_sinks=supported_sinks(),
        vector_address=vector_address,
        http_uri=http_uri,
        http_env_file=str(http_env_file) if http_env_file else None,
        http_bearer_token_env_var=http_bearer_token_env_var,
        vector_bin=vector_bin,
        config_path=str(config_path),
        config_file=str(resolved_config_file),
        data_dir=str(resolved_data_dir),
        events_path=app_ctx.config.events.path,
        laravel_logs=resolved_laravel_logs,
        nginx_access_logs=resolved_nginx_access_logs,
        nginx_error_logs=resolved_nginx_error_logs,
        extra_logs=resolved_extra_logs,
        service_user=service_user,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("observability-logs-enable"):
            result = enable_logs_shipping(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                service_user=service_user,
                vector_bin=vector_bin,
                config_file=resolved_config_file,
                data_dir=resolved_data_dir,
                events_path=Path(app_ctx.config.events.path),
                laravel_logs=resolved_laravel_logs,
                nginx_access_logs=resolved_nginx_access_logs,
                nginx_error_logs=resolved_nginx_error_logs,
                extra_logs=resolved_extra_logs,
                sink=sink,
                vector_address=vector_address,
                http_uri=http_uri,
                http_bearer_token_env_var=http_bearer_token_env_var,
                http_env_file=http_env_file,
            )
    except (CommandLockError, ObservabilityLogsError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output("ok", "Observability logs enabled.", logs=result)


@logs_app.command("disable")
def logs_disable(
    ctx: typer.Context,
    config_file: Path | None = typer.Option(
        None,
        "--config-file",
        help="Vector config file written by LarOps.",
        dir_okay=False,
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Vector checkpoint and disk buffer directory.",
        file_okay=False,
    ),
    remove_files: bool = typer.Option(False, "--remove-files", help="Remove unit, config and checkpoint files."),
    apply: bool = typer.Option(False, "--apply", help="Apply log shipping disable."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_config_file = _resolve_logs_config_file(config_file)
    resolved_data_dir = _resolve_logs_data_dir(app_ctx, data_dir)
    app_ctx.emit_output(
        "ok",
        "Observability logs disable plan prepared.",
        config_file=str(resolved_config_file),
        data_dir=str(resolved_data_dir),
        remove_files=remove_files,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("observability-logs-disable"):
            result = disable_logs_shipping(
                unit_dir=Path(app_ctx.config.systemd.unit_dir),
                systemd_manage=app_ctx.config.systemd.manage,
                remove_files=remove_files,
                config_file=resolved_config_file,
                data_dir=resolved_data_dir,
                allowed_data_root=_observability_root(app_ctx),
            )
    except (CommandLockError, ObservabilityLogsError) as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc

    app_ctx.emit_output("ok", "Observability logs disabled.", logs=result)


@logs_app.command("status")
def logs_status(
    ctx: typer.Context,
    config_file: Path | None = typer.Option(
        None,
        "--config-file",
        help="Vector config file written by LarOps.",
        dir_okay=False,
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Vector checkpoint and disk buffer directory.",
        file_okay=False,
    ),
    vector_bin: str = typer.Option("/usr/bin/vector", "--vector-bin", help="Vector executable path."),
) -> None:
    app_ctx: AppContext = ctx.obj
    resolved_config_file = _resolve_logs_config_file(config_file)
    resolved_data_dir = _resolve_logs_data_dir(app_ctx, data_dir)
    result = status_logs_shipping(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
        config_file=resolved_config_file,
        data_dir=resolved_data_dir,
        vector_bin=vector_bin,
    )
    app_ctx.emit_output("ok", "Observability logs status.", logs=result)
