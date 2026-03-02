from __future__ import annotations

from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.runtime import AppContext
from larops.services.telegram_adapter import (
    TelegramAdapterConfig,
    TelegramAdapterError,
    dispatch_once,
    send_telegram_message,
    watch,
)
from larops.services.notify_systemd import (
    NotifySystemdError,
    disable_telegram_daemon,
    enable_telegram_daemon,
    restart_telegram_daemon,
    status_telegram_daemon,
)

notify_app = typer.Typer(help="Notification adapters.")
telegram_app = typer.Typer(help="Telegram notification adapter.")
daemon_app = typer.Typer(help="Manage Telegram adapter as systemd service.")
notify_app.add_typer(telegram_app, name="telegram")
telegram_app.add_typer(daemon_app, name="daemon")


def _build_config(app_ctx: AppContext, batch_size_override: int | None = None) -> TelegramAdapterConfig:
    state_file = Path(app_ctx.config.state_path) / "notify" / "telegram_state.json"
    batch_size = batch_size_override or app_ctx.config.notifications.telegram.batch_size
    return TelegramAdapterConfig(
        events_path=Path(app_ctx.config.events.path),
        state_file=state_file,
        bot_token=app_ctx.config.notifications.telegram.bot_token,
        chat_id=app_ctx.config.notifications.telegram.chat_id,
        min_severity=app_ctx.config.notifications.telegram.min_severity,
        batch_size=batch_size,
    )


def _resolve_cli_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


def _validate_apply_ready(app_ctx: AppContext) -> None:
    telegram = app_ctx.config.notifications.telegram
    if not telegram.enabled:
        raise TelegramAdapterError("Telegram notifications are disabled in config.")
    if not telegram.bot_token or not telegram.chat_id:
        raise TelegramAdapterError("Telegram bot_token or chat_id is not configured.")


@telegram_app.command("run-once")
def run_once(
    ctx: typer.Context,
    batch_size: int | None = typer.Option(None, "--batch-size", help="Override batch size."),
    apply: bool = typer.Option(False, "--apply", help="Send messages."),
) -> None:
    app_ctx: AppContext = ctx.obj
    cfg = _build_config(app_ctx, batch_size_override=batch_size)
    app_ctx.emit_output(
        "ok",
        "Telegram run-once plan prepared.",
        events_path=str(cfg.events_path),
        state_file=str(cfg.state_file),
        min_severity=cfg.min_severity,
        batch_size=cfg.batch_size,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        _validate_apply_ready(app_ctx)
        report = dispatch_once(cfg, apply=True)
    except TelegramAdapterError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    app_ctx.emit_output("ok", "Telegram run-once completed.", report=report)


@telegram_app.command("watch")
def watch_loop(
    ctx: typer.Context,
    interval: int = typer.Option(10, "--interval", help="Polling interval in seconds."),
    iterations: int = typer.Option(1, "--iterations", help="Number of loops; 0 means infinite."),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Override batch size."),
    apply: bool = typer.Option(False, "--apply", help="Send messages."),
) -> None:
    app_ctx: AppContext = ctx.obj
    cfg = _build_config(app_ctx, batch_size_override=batch_size)
    app_ctx.emit_output(
        "ok",
        "Telegram watch plan prepared.",
        interval=interval,
        iterations=iterations,
        events_path=str(cfg.events_path),
        state_file=str(cfg.state_file),
        min_severity=cfg.min_severity,
        batch_size=cfg.batch_size,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        _validate_apply_ready(app_ctx)
        reports = watch(
            cfg,
            apply=True,
            interval_seconds=interval,
            iterations=iterations,
        )
    except TelegramAdapterError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    app_ctx.emit_output("ok", "Telegram watch completed.", reports=reports)


@telegram_app.command("test")
def send_test(
    ctx: typer.Context,
    message: str = typer.Option("LarOps test notification", "--message", help="Test message."),
    apply: bool = typer.Option(False, "--apply", help="Send test message."),
) -> None:
    app_ctx: AppContext = ctx.obj
    cfg = _build_config(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Telegram test plan prepared.",
        chat_id=cfg.chat_id,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        _validate_apply_ready(app_ctx)
        send_telegram_message(cfg.bot_token, cfg.chat_id, message)
    except TelegramAdapterError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    app_ctx.emit_output("ok", "Telegram test notification sent.")


@daemon_app.command("enable")
def daemon_enable(
    ctx: typer.Context,
    interval: int = typer.Option(10, "--interval", help="Polling interval in seconds."),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Override batch size."),
    env_file: Path = typer.Option(
        Path("/etc/larops/telegram.env"),
        "--env-file",
        help="Environment file for Telegram secrets.",
        dir_okay=False,
    ),
    larops_bin: str = typer.Option("/usr/local/bin/larops", "--larops-bin", help="LarOps executable path."),
    apply: bool = typer.Option(False, "--apply", help="Apply daemon changes."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_cli_config_path(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Telegram daemon enable plan prepared.",
        service_name="larops-notify-telegram.service",
        config_path=str(config_path),
        unit_dir=app_ctx.config.systemd.unit_dir,
        service_user=app_ctx.config.systemd.user,
        interval=interval,
        batch_size=batch_size,
        env_file=str(env_file),
        larops_bin=larops_bin,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        result = enable_telegram_daemon(
            unit_dir=Path(app_ctx.config.systemd.unit_dir),
            systemd_manage=app_ctx.config.systemd.manage,
            user=app_ctx.config.systemd.user,
            larops_bin=larops_bin,
            config_path=config_path,
            interval_seconds=interval,
            batch_size=batch_size,
            env_file=env_file,
        )
    except NotifySystemdError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    app_ctx.emit_output("ok", "Telegram daemon enabled.", daemon=result)


@daemon_app.command("disable")
def daemon_disable(
    ctx: typer.Context,
    apply: bool = typer.Option(False, "--apply", help="Apply daemon changes."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Telegram daemon disable plan prepared.",
        service_name="larops-notify-telegram.service",
        unit_dir=app_ctx.config.systemd.unit_dir,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    result = disable_telegram_daemon(systemd_manage=app_ctx.config.systemd.manage)
    app_ctx.emit_output("ok", "Telegram daemon disabled.", daemon=result)


@daemon_app.command("restart")
def daemon_restart(
    ctx: typer.Context,
    apply: bool = typer.Option(False, "--apply", help="Apply daemon restart."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output(
        "ok",
        "Telegram daemon restart plan prepared.",
        service_name="larops-notify-telegram.service",
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    try:
        result = restart_telegram_daemon(systemd_manage=app_ctx.config.systemd.manage)
    except NotifySystemdError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc
    app_ctx.emit_output("ok", "Telegram daemon restarted.", daemon=result)


@daemon_app.command("status")
def daemon_status(ctx: typer.Context) -> None:
    app_ctx: AppContext = ctx.obj
    result = status_telegram_daemon(
        unit_dir=Path(app_ctx.config.systemd.unit_dir),
        systemd_manage=app_ctx.config.systemd.manage,
    )
    app_ctx.emit_output("ok", "Telegram daemon status.", daemon=result)
