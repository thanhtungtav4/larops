import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("/etc/larops/larops.yaml")


class ConfigError(RuntimeError):
    pass


class DeployConfig(BaseModel):
    releases_path: str = "/var/www"
    source_base_path: str = "/var/www/source"
    keep_releases: int = 5
    build_timeout_seconds: int = 1800
    pre_activate_timeout_seconds: int = 900
    post_activate_timeout_seconds: int = 900
    health_check_path: str = "/up"
    health_check_enabled: bool = False
    health_check_scheme: str = "http"
    health_check_host: str = "127.0.0.1"
    health_check_timeout_seconds: int = 5
    health_check_retries: int = 3
    health_check_retry_delay_seconds: int = 1
    health_check_expected_status: int = 200
    health_check_use_domain_host_header: bool = True
    rollback_on_health_check_failure: bool = False
    runtime_refresh_strategy: str = "none"
    shared_dirs: list[str] = Field(default_factory=lambda: ["storage", "bootstrap/cache"])
    shared_files: list[str] = Field(default_factory=lambda: [".env"])
    composer_install: bool = False
    composer_binary: str = "composer"
    composer_no_dev: bool = True
    composer_optimize_autoloader: bool = True
    asset_commands: list[str] = Field(default_factory=list)
    migrate_enabled: bool = False
    migrate_phase: str = "post-activate"
    migrate_command: str = "php artisan migrate --force"
    cache_warm_enabled: bool = False
    cache_warm_commands: list[str] = Field(
        default_factory=lambda: [
            "php artisan config:cache",
            "php artisan route:cache",
            "php artisan view:cache",
            "php artisan event:cache",
        ]
    )
    verify_timeout_seconds: int = 300
    verify_commands: list[str] = Field(default_factory=list)
    rollback_on_verify_failure: bool = False
    pre_activate_commands: list[str] = Field(default_factory=list)
    post_activate_commands: list[str] = Field(default_factory=list)


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


class RuntimePolicyItemConfig(BaseModel):
    max_restarts: int = 5
    window_seconds: int = 300
    cooldown_seconds: int = 120
    auto_heal: bool = True


class RuntimePolicyConfig(BaseModel):
    worker: RuntimePolicyItemConfig = Field(default_factory=RuntimePolicyItemConfig)
    scheduler: RuntimePolicyItemConfig = Field(default_factory=RuntimePolicyItemConfig)
    horizon: RuntimePolicyItemConfig = Field(default_factory=RuntimePolicyItemConfig)


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class BackupEncryptionConfig(BaseModel):
    enabled: bool = False
    passphrase: str = ""
    passphrase_file: str = ""
    cipher: str = "aes-256-cbc"


class BackupOffsiteConfig(BaseModel):
    enabled: bool = False
    provider: str = "s3"
    bucket: str = ""
    prefix: str = "larops/backups"
    region: str = "us-east-1"
    endpoint_url: str = ""
    access_key_id: str = ""
    access_key_id_file: str = ""
    secret_access_key: str = ""
    secret_access_key_file: str = ""
    storage_class: str = "STANDARD"
    retention_days: int = 30
    stale_hours: int = 24


class BackupsConfig(BaseModel):
    encryption: BackupEncryptionConfig = Field(default_factory=BackupEncryptionConfig)
    offsite: BackupOffsiteConfig = Field(default_factory=BackupOffsiteConfig)


class DoctorAppCommandCheckConfig(BaseModel):
    name: str
    command: str
    timeout_seconds: int = 30


class DoctorHeartbeatCheckConfig(BaseModel):
    name: str
    path: str
    max_age_seconds: int = 180


class DoctorQueueBacklogCheckConfig(BaseModel):
    name: str
    connection: str = "default"
    queue: str = "default"
    max_size: int = 100
    timeout_seconds: int = 30


class DoctorFailedJobCheckConfig(BaseModel):
    name: str
    max_count: int = 0
    timeout_seconds: int = 30


class DoctorConfig(BaseModel):
    app_command_checks: list[DoctorAppCommandCheckConfig] = Field(default_factory=list)
    heartbeat_checks: list[DoctorHeartbeatCheckConfig] = Field(default_factory=list)
    queue_backlog_checks: list[DoctorQueueBacklogCheckConfig] = Field(default_factory=list)
    failed_job_checks: list[DoctorFailedJobCheckConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    environment: str = "production"
    state_path: str = ".larops/state"
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    systemd: SystemdConfig = Field(default_factory=SystemdConfig)
    runtime_policy: RuntimePolicyConfig = Field(default_factory=RuntimePolicyConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    backups: BackupsConfig = Field(default_factory=BackupsConfig)
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)


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
            raise ConfigError(f"{label} file not found: {path}")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ConfigError(f"{label} file is empty: {path}")
        return value

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
    backup_passphrase = os.getenv("LAROPS_BACKUP_PASSPHRASE")
    backup_passphrase_file = os.getenv("LAROPS_BACKUP_PASSPHRASE_FILE")
    offsite_access_key_id = os.getenv("LAROPS_OFFSITE_ACCESS_KEY_ID")
    offsite_access_key_id_file = os.getenv("LAROPS_OFFSITE_ACCESS_KEY_ID_FILE")
    offsite_secret_access_key = os.getenv("LAROPS_OFFSITE_SECRET_ACCESS_KEY")
    offsite_secret_access_key_file = os.getenv("LAROPS_OFFSITE_SECRET_ACCESS_KEY_FILE")

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
        try:
            updated.notifications.telegram.batch_size = max(1, int(telegram_batch_size))
        except ValueError as exc:
            raise ConfigError(f"Invalid LAROPS_TELEGRAM_BATCH_SIZE: {telegram_batch_size}") from exc
    if backup_passphrase:
        updated.backups.encryption.passphrase = backup_passphrase
    if backup_passphrase_file:
        updated.backups.encryption.passphrase_file = backup_passphrase_file
    if offsite_access_key_id:
        updated.backups.offsite.access_key_id = offsite_access_key_id
    if offsite_access_key_id_file:
        updated.backups.offsite.access_key_id_file = offsite_access_key_id_file
    if offsite_secret_access_key:
        updated.backups.offsite.secret_access_key = offsite_secret_access_key
    if offsite_secret_access_key_file:
        updated.backups.offsite.secret_access_key_file = offsite_secret_access_key_file

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
    if updated.backups.encryption.passphrase_file:
        updated.backups.encryption.passphrase = _read_secret(
            updated.backups.encryption.passphrase_file,
            "Backup encryption passphrase",
        )
    if updated.backups.offsite.access_key_id_file:
        updated.backups.offsite.access_key_id = _read_secret(
            updated.backups.offsite.access_key_id_file,
            "Offsite access key id",
        )
    if updated.backups.offsite.secret_access_key_file:
        updated.backups.offsite.secret_access_key = _read_secret(
            updated.backups.offsite.secret_access_key_file,
            "Offsite secret access key",
        )

    return updated
