from __future__ import annotations

import socket
from pathlib import Path

import typer

from larops.config import DEFAULT_CONFIG_PATH
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.alert_service import AlertServiceError, configure_telegram_alert
from larops.services.telegram_adapter import TelegramAdapterError, send_telegram_message

alert_app = typer.Typer(help="Configure and test alert channels.")


def _emit(
    app_ctx: AppContext,
    *,
    severity: str,
    event_type: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    app_ctx.event_emitter.emit(
        EventRecord(
            severity=severity,
            event_type=event_type,
            host=socket.gethostname(),
            message=message,
            metadata=metadata or {},
        )
    )


def _resolve_config_path(app_ctx: AppContext) -> Path:
    return app_ctx.config_path or DEFAULT_CONFIG_PATH


@alert_app.command("set")
def alert_set(
    ctx: typer.Context,
    telegram_token: str | None = typer.Option(None, "--telegram-token", help="Telegram bot token."),
    telegram_chat_id: str | None = typer.Option(None, "--telegram-chat-id", help="Telegram chat id."),
    telegram_token_file: Path = typer.Option(
        Path("/etc/larops/secrets/telegram_bot_token"),
        "--telegram-token-file",
        help="Telegram bot token secret file path.",
        dir_okay=False,
    ),
    telegram_chat_id_file: Path = typer.Option(
        Path("/etc/larops/secrets/telegram_chat_id"),
        "--telegram-chat-id-file",
        help="Telegram chat id secret file path.",
        dir_okay=False,
    ),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Enable Telegram alerts in config."),
    apply: bool = typer.Option(False, "--apply", help="Apply alert configuration."),
) -> None:
    app_ctx: AppContext = ctx.obj
    config_path = _resolve_config_path(app_ctx)
    app_ctx.emit_output(
        "ok",
        "Alert set plan prepared.",
        config_path=str(config_path),
        telegram_token_file=str(telegram_token_file),
        telegram_chat_id_file=str(telegram_chat_id_file),
        enabled=enabled,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        result = configure_telegram_alert(
            config_path=config_path,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            telegram_token_file=telegram_token_file,
            telegram_chat_id_file=telegram_chat_id_file,
            enabled=enabled,
        )
    except AlertServiceError as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="alert.telegram.set.failed",
            message="Telegram alert setup failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="alert.telegram.set.completed",
        message="Telegram alert setup completed.",
        metadata={"config_path": str(config_path), "enabled": enabled},
    )
    app_ctx.emit_output("ok", "Alert configuration completed.", result=result)


@alert_app.command("test")
def alert_test(
    ctx: typer.Context,
    message: str = typer.Option("LarOps security alert test", "--message", help="Test alert message."),
    apply: bool = typer.Option(False, "--apply", help="Send a test alert."),
) -> None:
    app_ctx: AppContext = ctx.obj
    telegram = app_ctx.config.notifications.telegram
    app_ctx.emit_output(
        "ok",
        "Alert test plan prepared.",
        channel="telegram",
        enabled=telegram.enabled,
        chat_id=telegram.chat_id,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )
    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return
    if not telegram.enabled:
        app_ctx.emit_output("error", "Telegram alerts are disabled in config.")
        raise typer.Exit(code=1)
    if not telegram.bot_token or not telegram.chat_id:
        app_ctx.emit_output("error", "Telegram bot token or chat id is missing.")
        raise typer.Exit(code=1)

    try:
        send_telegram_message(telegram.bot_token, telegram.chat_id, message)
    except TelegramAdapterError as exc:
        _emit(
            app_ctx,
            severity="error",
            event_type="alert.test.failed",
            message="Alert test failed.",
            metadata={"error": str(exc)},
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    _emit(
        app_ctx,
        severity="info",
        event_type="alert.test.completed",
        message="Alert test sent.",
    )
    app_ctx.emit_output("ok", "Alert test notification sent.")
