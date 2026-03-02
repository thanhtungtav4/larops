import json
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app

runner = CliRunner()


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


def test_stack_install_plan_mode() -> None:
    result = runner.invoke(app, ["stack", "install", "--web"])
    assert result.exit_code == 0
    assert "Stack plan prepared for groups: web" in result.stdout
    assert "Plan mode finished. Use --apply to execute changes." in result.stdout


def test_stack_install_json_mode_and_event_file(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    result = runner.invoke(
        app,
        ["--json", "stack", "install", "--web"],
        env={"LAROPS_EVENTS_PATH": str(events)},
    )
    assert result.exit_code == 0

    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["status"] == "ok"
    assert lines[1]["status"] == "ok"

    event_lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(event_lines) >= 1
