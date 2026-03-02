import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("/etc/larops/larops.yaml")


class DeployConfig(BaseModel):
    releases_path: str = "/var/www"
    keep_releases: int = 5
    health_check_path: str = "/up"


class EventsConfig(BaseModel):
    sink: str = "jsonl"
    path: str = ".larops/events.jsonl"


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class AppConfig(BaseModel):
    environment: str = "production"
    state_path: str = ".larops/state"
    deploy: DeployConfig = Field(default_factory=DeployConfig)
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
    env = os.getenv("LAROPS_ENVIRONMENT")
    events_path = os.getenv("LAROPS_EVENTS_PATH")
    events_sink = os.getenv("LAROPS_EVENTS_SINK")

    updated = config.model_copy(deep=True)
    if env:
        updated.environment = env
    if events_path:
        updated.events.path = events_path
    if events_sink:
        updated.events.sink = events_sink

    return updated
