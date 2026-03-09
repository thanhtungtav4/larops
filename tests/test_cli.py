import json
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app

runner = CliRunner()


def linux_env(tmp_path: Path) -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n', encoding="utf-8")
    return {"LAROPS_STACK_OS_RELEASE_PATH": str(os_release)}


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_help_output() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "LarOps: Laravel-first server operations CLI." in result.stdout


def test_stack_install_requires_group() -> None:
    result = runner.invoke(app, ["stack", "install"])
    assert result.exit_code == 2
    assert "No stack group selected" in result.stdout


def test_stack_install_plan_mode(tmp_path: Path) -> None:
    result = runner.invoke(app, ["stack", "install", "--web"], env=linux_env(tmp_path))
    assert result.exit_code == 0
    assert "Stack plan prepared for groups: web" in result.stdout
    assert "Plan mode finished. Use --apply to execute changes." in result.stdout


def test_stack_install_json_mode_and_event_file(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    env = linux_env(tmp_path)
    env["LAROPS_EVENTS_PATH"] = str(events)
    result = runner.invoke(
        app,
        ["--json", "stack", "install", "--web"],
        env=env,
    )
    assert result.exit_code == 0

    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["status"] == "ok"
    assert lines[1]["status"] == "ok"

    event_lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(event_lines) >= 1


def test_cli_fails_fast_on_missing_telegram_secret(tmp_path: Path) -> None:
    config = tmp_path / "larops.yaml"
    config.write_text("environment: test\n", encoding="utf-8")
    missing = tmp_path / "missing-token"
    result = runner.invoke(
        app,
        ["--config", str(config), "stack", "install", "--web"],
        env={"LAROPS_TELEGRAM_BOT_TOKEN_FILE": str(missing)},
    )
    assert result.exit_code == 2
    assert "Config error" in result.stdout
