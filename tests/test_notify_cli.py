from pathlib import Path

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
                "    enabled: true",
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


def test_notify_telegram_run_once_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "notify", "telegram", "run-once"])
    assert result.exit_code == 0
    assert "Telegram run-once plan prepared." in result.stdout


def test_notify_telegram_test_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "notify", "telegram", "test"])
    assert result.exit_code == 0
    assert "Telegram test plan prepared." in result.stdout


def test_notify_telegram_daemon_enable_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "notify", "telegram", "daemon", "enable"])
    assert result.exit_code == 0
    assert "Telegram daemon enable plan prepared." in result.stdout


def test_notify_telegram_daemon_status(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "notify", "telegram", "daemon", "status"])
    assert result.exit_code == 0
    assert "Telegram daemon status." in result.stdout


def test_notify_telegram_daemon_enable_apply_unmanaged_writes_unit(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    env_file = tmp_path / "telegram.env"
    env_file.write_text("LAROPS_TELEGRAM_ENABLED=true\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "notify",
            "telegram",
            "daemon",
            "enable",
            "--interval",
            "7",
            "--batch-size",
            "10",
            "--env-file",
            str(env_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    unit_path = tmp_path / "units" / "larops-notify-telegram.service"
    assert unit_path.exists()
    body = unit_path.read_text(encoding="utf-8")
    assert "--interval 7 --iterations 0 --apply --batch-size 10" in body
