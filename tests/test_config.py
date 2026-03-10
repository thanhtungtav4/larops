from pathlib import Path

import pytest

import larops.config as config_module
from larops.config import ConfigError, load_config


def test_load_config_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.yaml")
    install_root = Path(config_module.__file__).resolve().parents[2]
    assert config.environment == "production"
    assert config.state_path == str((install_root / ".larops" / "state").resolve())
    assert config.events.path == str((install_root / ".larops" / "events.jsonl").resolve())


def test_load_config_reads_values(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "environment: staging",
                "events:",
                "  sink: jsonl",
                "  path: /tmp/custom-events.jsonl",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(file)
    assert config.environment == "staging"
    assert config.events.path == "/tmp/custom-events.jsonl"


def test_load_config_resolves_relative_runtime_paths_independent_of_cwd(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "state_path: .larops/custom-state",
                "events:",
                "  path: .larops/custom-events.jsonl",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_config(file)
    install_root = Path(config_module.__file__).resolve().parents[2]

    assert config.state_path == str((install_root / ".larops" / "custom-state").resolve())
    assert config.events.path == str((install_root / ".larops" / "custom-events.jsonl").resolve())


def test_load_config_reads_doctor_heartbeat_checks(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "doctor:",
                "  heartbeat_checks:",
                "    - name: scheduler-heartbeat",
                "      path: storage/app/larops/scheduler-heartbeat",
                "      max_age_seconds: 180",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(file)
    assert len(config.doctor.heartbeat_checks) == 1
    assert config.doctor.heartbeat_checks[0].name == "scheduler-heartbeat"
    assert config.doctor.heartbeat_checks[0].max_age_seconds == 180


def test_load_config_reads_queue_and_failed_job_checks(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "doctor:",
                "  queue_backlog_checks:",
                "    - name: default-queue",
                "      connection: redis",
                "      queue: default",
                "      max_size: 25",
                "      timeout_seconds: 20",
                "  failed_job_checks:",
                "    - name: failed-jobs",
                "      max_count: 0",
                "      timeout_seconds: 15",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(file)
    assert len(config.doctor.queue_backlog_checks) == 1
    assert config.doctor.queue_backlog_checks[0].connection == "redis"
    assert config.doctor.queue_backlog_checks[0].max_size == 25
    assert len(config.doctor.failed_job_checks) == 1
    assert config.doctor.failed_job_checks[0].max_count == 0


def test_load_config_reads_backup_offsite_and_encryption(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "backups:",
                "  encryption:",
                "    enabled: true",
                "    passphrase: secret-passphrase",
                "    cipher: aes-256-cbc",
                "  offsite:",
                "    enabled: true",
                "    provider: s3",
                "    bucket: larops-backups",
                "    prefix: prod/backups",
                "    region: auto",
                "    endpoint_url: https://example.r2.cloudflarestorage.com",
                "    access_key_id: key-id",
                "    secret_access_key: secret-key",
                "    retention_days: 14",
                "    stale_hours: 12",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(file)
    assert config.backups.encryption.enabled is True
    assert config.backups.encryption.passphrase == "secret-passphrase"
    assert config.backups.offsite.enabled is True
    assert config.backups.offsite.bucket == "larops-backups"
    assert config.backups.offsite.retention_days == 14
    assert config.backups.offsite.stale_hours == 12


def test_load_config_skips_disabled_secret_files_from_config_defaults(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "notifications:",
                "  telegram:",
                "    enabled: false",
                "    bot_token_file: /etc/larops/secrets/telegram_bot_token",
                "    chat_id_file: /etc/larops/secrets/telegram_chat_id",
                "backups:",
                "  encryption:",
                "    enabled: false",
                "    passphrase_file: /etc/larops/secrets/backup_passphrase",
                "  offsite:",
                "    enabled: false",
                "    access_key_id_file: /etc/larops/secrets/offsite_access_key_id",
                "    secret_access_key_file: /etc/larops/secrets/offsite_secret_access_key",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(file)
    assert config.notifications.telegram.enabled is False
    assert config.notifications.telegram.bot_token == ""
    assert config.notifications.telegram.chat_id == ""
    assert config.backups.encryption.passphrase == ""
    assert config.backups.offsite.access_key_id == ""
    assert config.backups.offsite.secret_access_key == ""


def test_load_config_env_overrides_telegram_from_secret_files(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "environment: staging",
                "notifications:",
                "  telegram:",
                "    enabled: false",
                "    bot_token: ''",
                "    bot_token_file: ''",
                "    chat_id: ''",
                "    chat_id_file: ''",
                "    min_severity: error",
                "    batch_size: 20",
            ]
        ),
        encoding="utf-8",
    )
    token_file = tmp_path / "bot_token.txt"
    token_file.write_text("bot-token-from-file\n", encoding="utf-8")
    chat_id_file = tmp_path / "chat_id.txt"
    chat_id_file.write_text("-100123\n", encoding="utf-8")

    monkeypatch.setenv("LAROPS_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("LAROPS_TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LAROPS_TELEGRAM_CHAT_ID_FILE", str(chat_id_file))
    monkeypatch.setenv("LAROPS_TELEGRAM_MIN_SEVERITY", "critical")
    monkeypatch.setenv("LAROPS_TELEGRAM_BATCH_SIZE", "50")
    config = load_config(file)

    assert config.notifications.telegram.enabled is True
    assert config.notifications.telegram.bot_token == "bot-token-from-file"
    assert config.notifications.telegram.chat_id == "-100123"
    assert config.notifications.telegram.min_severity == "critical"
    assert config.notifications.telegram.batch_size == 50


def test_load_config_fail_fast_when_secret_file_missing(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text("environment: test\n", encoding="utf-8")
    monkeypatch.setenv("LAROPS_TELEGRAM_BOT_TOKEN_FILE", str(tmp_path / "missing-token"))
    monkeypatch.setenv("LAROPS_TELEGRAM_ENABLED", "true")

    with pytest.raises(ConfigError):
        load_config(file)


def test_load_config_fail_fast_when_secret_file_empty(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text("environment: test\n", encoding="utf-8")
    token_file = tmp_path / "token.txt"
    token_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("LAROPS_TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LAROPS_TELEGRAM_ENABLED", "true")

    with pytest.raises(ConfigError):
        load_config(file)


def test_load_config_fail_fast_when_batch_size_invalid(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text("environment: test\n", encoding="utf-8")
    monkeypatch.setenv("LAROPS_TELEGRAM_BATCH_SIZE", "abc")
    with pytest.raises(ConfigError):
        load_config(file)
