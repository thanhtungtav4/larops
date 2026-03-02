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
                "  keep_releases: 3",
                "  health_check_path: /up",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_bootstrap_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "bootstrap", "init", "--skip-stack"])
    assert result.exit_code == 0
    assert "Bootstrap plan prepared." in result.stdout


def test_bootstrap_apply_with_domain_and_skip_stack(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("bootstrap", encoding="utf-8")
    generated_config = tmp_path / "generated-larops.yaml"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "bootstrap",
            "init",
            "--skip-stack",
            "--write-config",
            "--config-path",
            str(generated_config),
            "--domain",
            "demo.test",
            "--source",
            str(source),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert generated_config.exists()

    info = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    assert info.exit_code == 0
    payload = json.loads(info.stdout.strip())
    assert payload["releases_count"] == 1

