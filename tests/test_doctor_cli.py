import json
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
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_doctor_quick_json(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "quick"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["message"] == "Doctor quick report for host"
    assert "report" in payload


def test_doctor_run_for_unknown_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "doctor", "run", "missing.test"])
    assert result.exit_code == 0
    assert "Doctor report for missing.test" in result.stdout


def test_doctor_run_reports_missing_restore_verify_for_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "backup-verify:demo.test" in names


def test_doctor_run_executes_configured_app_command_checks(tmp_path: Path) -> None:
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
                "doctor:",
                "  app_command_checks:",
                "    - name: release-file",
                "      command: test -f README.txt",
                "      timeout_seconds: 5",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("ok", encoding="utf-8")

    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    result = runner.invoke(app, ["--config", str(config_file), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "app-check:release-file" in names
