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


def test_site_delete_requires_purge_flag(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "site", "delete", "demo.test", "--apply"])
    assert result.exit_code == 2
    assert "requires --purge" in result.stdout


def test_site_delete_guard_requires_confirm_or_no_prompt(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "site", "delete", "demo.test", "--purge", "--apply"],
    )
    assert result.exit_code == 2
    assert "Guard check failed" in result.stdout


def test_site_delete_purge_with_checkpoint(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(
        app,
        ["--config", str(config), "site", "create", "demo.test", "--worker", "--apply"],
    )
    assert create.exit_code == 0

    delete = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "site",
            "delete",
            "demo.test",
            "--purge",
            "--confirm",
            "demo.test",
            "--apply",
        ],
    )
    assert delete.exit_code == 0
    payloads = [json.loads(line) for line in delete.stdout.strip().splitlines()]
    assert payloads[-1]["message"] == "Site deleted for demo.test"
    checkpoint_file = payloads[-1]["checkpoint_file"]
    assert checkpoint_file is not None
    assert Path(checkpoint_file).exists()

    assert not (tmp_path / "state" / "apps" / "demo.test.json").exists()
    assert not (tmp_path / "state" / "runtime" / "demo.test").exists()
    assert not (tmp_path / "apps" / "demo.test").exists()


def test_site_delete_with_no_prompt(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(
        app,
        ["--config", str(config), "site", "create", "demo.test", "--apply"],
    )
    assert create.exit_code == 0

    delete = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "delete",
            "demo.test",
            "--purge",
            "--no-prompt",
            "--no-checkpoint",
            "--apply",
        ],
    )
    assert delete.exit_code == 0
    assert not (tmp_path / "apps" / "demo.test").exists()
