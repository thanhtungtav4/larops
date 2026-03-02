from __future__ import annotations

from pathlib import Path

import typer

from larops.runtime import AppContext
from larops.services.telegram_adapter import (
    TelegramAdapterConfig,
    TelegramAdapterError,
    dispatch_once,
    send_telegram_message,
    watch,
)

notify_app = typer.Typer(help="Notification adapters.")
telegram_app = typer.Typer(help="Telegram notification adapter.")
notify_app.add_typer(telegram_app, name="telegram")


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
