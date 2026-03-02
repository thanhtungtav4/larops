import os
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


def write_secret(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "[client]",
                "user=appuser",
                "password=appsecret",
                "host=127.0.0.1",
                "port=3306",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def test_db_backup_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "db.cnf"
    write_secret(secret)
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
            "--credential-file",
            str(secret),
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
    secret = tmp_path / "db.cnf"
    write_secret(secret)
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
            "--credential-file",
            str(secret),
        ],
    )
    assert result.exit_code == 2
    assert "Backup file not found" in result.stdout


def test_db_backup_rejects_invalid_database_name(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "db.cnf"
    write_secret(secret)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "backup",
            "demo.test",
            "--database",
            "appdb;DROP TABLE users",
            "--credential-file",
            str(secret),
        ],
    )
    assert result.exit_code == 2
    assert "Invalid database name" in result.stdout


def test_db_credential_set_and_show(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "demo.cnf"
    env = {"DB_PASSWORD_TEST": "super-secret"}
    set_result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "credential",
            "set",
            "demo.test",
            "--user",
            "appuser",
            "--password-env",
            "DB_PASSWORD_TEST",
            "--credential-file",
            str(secret),
            "--apply",
        ],
        env=env,
    )
    assert set_result.exit_code == 0
    assert secret.exists()
    assert oct(secret.stat().st_mode & 0o777) == "0o600"

    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "credential",
            "show",
            "demo.test",
            "--credential-file",
            str(secret),
        ],
    )
    assert show_result.exit_code == 0
    assert "Credential file status for demo.test" in show_result.stdout
