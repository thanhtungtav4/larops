import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("/etc/larops/larops.yaml")


class DeployConfig(BaseModel):
    releases_path: str = "/var/www"
    source_base_path: str = "/var/www/source"
    keep_releases: int = 5
    health_check_path: str = "/up"


class EventsConfig(BaseModel):
    sink: str = "jsonl"
    path: str = ".larops/events.jsonl"


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    bot_token_file: str = ""
    chat_id: str = ""
    chat_id_file: str = ""
    min_severity: str = "error"
    batch_size: int = 20


class SystemdConfig(BaseModel):
    manage: bool = True
    unit_dir: str = "/etc/systemd/system"
    user: str = "www-data"


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class AppConfig(BaseModel):
    environment: str = "production"
    state_path: str = ".larops/state"
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    systemd: SystemdConfig = Field(default_factory=SystemdConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}

    config = AppConfig.model_validate(raw)
    return apply_env_overrides(config)


def apply_env_overrides(config: AppConfig) -> AppConfig:
    def _parse_bool(raw: str) -> bool:
        return raw.lower() in {"1", "true", "yes", "on"}

    def _read_secret(path_raw: str, label: str) -> str:
        path = Path(path_raw)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    env = os.getenv("LAROPS_ENVIRONMENT")
    events_path = os.getenv("LAROPS_EVENTS_PATH")
    events_sink = os.getenv("LAROPS_EVENTS_SINK")
    systemd_manage = os.getenv("LAROPS_SYSTEMD_MANAGE")
    telegram_enabled = os.getenv("LAROPS_TELEGRAM_ENABLED")
    telegram_bot_token = os.getenv("LAROPS_TELEGRAM_BOT_TOKEN")
    telegram_bot_token_file = os.getenv("LAROPS_TELEGRAM_BOT_TOKEN_FILE")
    telegram_chat_id = os.getenv("LAROPS_TELEGRAM_CHAT_ID")
    telegram_chat_id_file = os.getenv("LAROPS_TELEGRAM_CHAT_ID_FILE")
    telegram_min_severity = os.getenv("LAROPS_TELEGRAM_MIN_SEVERITY")
    telegram_batch_size = os.getenv("LAROPS_TELEGRAM_BATCH_SIZE")

    updated = config.model_copy(deep=True)
    if env:
        updated.environment = env
    if events_path:
        updated.events.path = events_path
    if events_sink:
        updated.events.sink = events_sink
    if systemd_manage:
        updated.systemd.manage = _parse_bool(systemd_manage)
    if telegram_enabled:
        updated.notifications.telegram.enabled = _parse_bool(telegram_enabled)
    if telegram_bot_token:
        updated.notifications.telegram.bot_token = telegram_bot_token
    if telegram_bot_token_file:
        updated.notifications.telegram.bot_token_file = telegram_bot_token_file
    if telegram_chat_id:
        updated.notifications.telegram.chat_id = telegram_chat_id
    if telegram_chat_id_file:
        updated.notifications.telegram.chat_id_file = telegram_chat_id_file
    if telegram_min_severity:
        updated.notifications.telegram.min_severity = telegram_min_severity
    if telegram_batch_size:
        updated.notifications.telegram.batch_size = max(1, int(telegram_batch_size))

    if updated.notifications.telegram.bot_token_file:
        updated.notifications.telegram.bot_token = _read_secret(
            updated.notifications.telegram.bot_token_file,
            "Telegram bot token",
        )
    if updated.notifications.telegram.chat_id_file:
        updated.notifications.telegram.chat_id = _read_secret(
            updated.notifications.telegram.chat_id_file,
            "Telegram chat id",
        )

    return updated
