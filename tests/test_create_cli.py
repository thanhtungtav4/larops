import json
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app

runner = CliRunner()


def write_config(tmp_path: Path) -> Path:
    source_base = tmp_path / "sources"
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                f"  source_base_path: {source_base}",
                "  keep_releases: 5",
                "  health_check_path: /up",
                "systemd:",
                "  manage: false",
                f"  unit_dir: {tmp_path / 'units'}",
                "  user: www-data",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def make_source(tmp_path: Path, domain: str) -> Path:
    source = tmp_path / "sources" / domain
    source.mkdir(parents=True, exist_ok=True)
    (source / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    return source


def test_create_site_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--source", str(source)],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


def test_create_site_apply_creates_and_deploys(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    metadata = tmp_path / "state" / "apps" / "demo.test.json"
    assert metadata.exists()

    info = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    payload = json.loads(info.stdout.strip())
    assert payload["current_release"] is not None
    assert payload["releases_count"] == 1


def test_create_site_apply_with_runtime_flags(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--worker",
            "--scheduler",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    worker_status = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", "demo.test"])
    worker_payload = json.loads(worker_status.stdout.strip())
    assert worker_payload["process"]["enabled"] is True

    scheduler_status = runner.invoke(app, ["--config", str(config), "--json", "scheduler", "status", "demo.test"])
    scheduler_payload = json.loads(scheduler_status.stdout.strip())
    assert scheduler_payload["process"]["enabled"] is True


def test_create_site_runtime_requires_deploy(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--no-deploy",
            "--worker",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert "Runtime enable requires --deploy" in result.stdout
