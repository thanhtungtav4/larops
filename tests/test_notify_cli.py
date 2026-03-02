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
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
                "notifications:",
                "  telegram:",
                "    enabled: true",
                "    bot_token: test-token",
                "    chat_id: test-chat",
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

