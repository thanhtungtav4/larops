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


def test_db_backup_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "backup",
            "demo.test",
            "--database",
            "appdb",
            "--user",
            "appuser",
        ],
    )
    assert result.exit_code == 0
    assert "DB backup plan prepared for demo.test" in result.stdout


def test_db_list_backups(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "one.sql.gz").write_text("x", encoding="utf-8")
    (backup_dir / "two.sql.gz").write_text("y", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "list-backups",
            "demo.test",
            "--target-dir",
            str(backup_dir),
        ],
    )
    assert result.exit_code == 0
    assert "Backup list for demo.test" in result.stdout


def test_db_restore_requires_existing_backup(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    missing = tmp_path / "missing.sql.gz"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "restore",
            "demo.test",
            "--backup-file",
            str(missing),
            "--database",
            "appdb",
            "--user",
            "appuser",
        ],
    )
    assert result.exit_code == 2
    assert "Backup file not found" in result.stdout

