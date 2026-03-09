from pathlib import Path

import yaml
from typer.testing import CliRunner

from larops.cli import app

runner = CliRunner()


def write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                "  keep_releases: 5",
                "  health_check_path: /up",
                "systemd:",
                "  manage: false",
                f"  unit_dir: {tmp_path / 'units'}",
                "  user: www-data",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
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
    return config_file


def test_alert_set_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "alert",
            "set",
            "--telegram-token",
            "123:token",
            "--telegram-chat-id",
            "-100123",
        ],
    )
    assert result.exit_code == 0
    assert "Alert set plan prepared." in result.stdout


def test_alert_set_apply_writes_secret_files_and_updates_config(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    token_file = tmp_path / "secrets" / "telegram_bot_token"
    chat_id_file = tmp_path / "secrets" / "telegram_chat_id"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "alert",
            "set",
            "--telegram-token",
            "123:token",
            "--telegram-chat-id",
            "-100123",
            "--telegram-token-file",
            str(token_file),
            "--telegram-chat-id-file",
            str(chat_id_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert token_file.exists()
    assert chat_id_file.exists()
    assert token_file.read_text(encoding="utf-8").strip() == "123:token"
    assert chat_id_file.read_text(encoding="utf-8").strip() == "-100123"

    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    telegram = payload["notifications"]["telegram"]
    assert telegram["enabled"] is True
    assert telegram["bot_token_file"] == str(token_file)
    assert telegram["chat_id_file"] == str(chat_id_file)
    assert telegram["bot_token"] == ""
    assert telegram["chat_id"] == ""


def test_alert_test_apply_fails_when_not_configured(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "alert", "test", "--apply"])
    assert result.exit_code == 1
    assert "Telegram alerts are disabled in config." in result.stdout


def test_alert_set_disabled_does_not_require_secret_files(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    token_file = tmp_path / "secrets" / "telegram_bot_token"
    chat_id_file = tmp_path / "secrets" / "telegram_chat_id"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "alert",
            "set",
            "--disabled",
            "--telegram-token-file",
            str(token_file),
            "--telegram-chat-id-file",
            str(chat_id_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    telegram = payload["notifications"]["telegram"]
    assert telegram["enabled"] is False
    assert telegram["bot_token_file"] == str(token_file)
    assert telegram["chat_id_file"] == str(chat_id_file)


def test_alert_set_apply_invokes_selinux_relabel_helper(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    token_file = tmp_path / "secrets" / "telegram_bot_token"
    chat_id_file = tmp_path / "secrets" / "telegram_chat_id"
    relabel_calls: list[list[str]] = []

    def fake_relabel(paths, **kwargs) -> dict[str, object]:
        relabel_calls.append([str(path) for path in paths])
        return {"mode": "disabled", "relabelled_paths": []}

    monkeypatch.setattr("larops.services.alert_service.relabel_managed_paths_for_selinux", fake_relabel)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "alert",
            "set",
            "--telegram-token",
            "123:token",
            "--telegram-chat-id",
            "-100123",
            "--telegram-token-file",
            str(token_file),
            "--telegram-chat-id-file",
            str(chat_id_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert relabel_calls == [[str(token_file), str(chat_id_file), str(config)]]
