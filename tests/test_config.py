from pathlib import Path

import pytest

from larops.config import ConfigError, load_config


def test_load_config_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.yaml")
    assert config.environment == "production"
    assert config.events.path == ".larops/events.jsonl"


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

    with pytest.raises(ConfigError):
        load_config(file)


def test_load_config_fail_fast_when_secret_file_empty(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text("environment: test\n", encoding="utf-8")
    token_file = tmp_path / "token.txt"
    token_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("LAROPS_TELEGRAM_BOT_TOKEN_FILE", str(token_file))

    with pytest.raises(ConfigError):
        load_config(file)


def test_load_config_fail_fast_when_batch_size_invalid(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text("environment: test\n", encoding="utf-8")
    monkeypatch.setenv("LAROPS_TELEGRAM_BATCH_SIZE", "abc")
    with pytest.raises(ConfigError):
        load_config(file)
