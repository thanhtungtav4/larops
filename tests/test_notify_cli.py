from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from larops.cli import app
from larops.core.shell import ShellCommandError

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


def write_managed_config(tmp_path: Path) -> Path:
    config_file = write_config(tmp_path)
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace("  manage: false", "  manage: true"),
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


def test_notify_telegram_daemon_disable_fails_when_systemctl_fails(tmp_path: Path, monkeypatch) -> None:
    config = write_managed_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        if command[:2] == ["systemctl", "disable"]:
            if check:
                raise ShellCommandError("command failed (1): systemctl disable --now larops-notify-telegram.service")
            return CompletedProcess(command, 1, stdout="", stderr="boom")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.notify_systemd.run_command", fake_run_command)

    result = runner.invoke(app, ["--config", str(config), "notify", "telegram", "daemon", "disable", "--apply"])
    assert result.exit_code == 1
    assert "systemctl disable --now larops-notify-telegram.service" in result.stdout
