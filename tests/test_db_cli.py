import gzip
import json
import os
import re
import shlex
import stat
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app
from larops.services.db_service import build_backup_command, build_restore_command

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
                "  user: root",
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


def write_postgres_secret(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("127.0.0.1:5432:*:appuser:appsecret\n", encoding="utf-8")
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


def test_build_backup_command_sets_restrictive_umask(tmp_path: Path) -> None:
    secret = tmp_path / "db.cnf"
    write_secret(secret)
    command = build_backup_command(
        backup_file=tmp_path / "backup.sql.gz",
        database="appdb",
        credential_file=secret,
    )
    assert command[:2] == ["bash", "-lc"]
    assert "umask 077;" in command[2]


def test_db_backup_apply_hardens_backup_dir_permissions(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "db.cnf"
    write_secret(secret)
    backup_dir = tmp_path / "state" / "backups" / "demo.test"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    backup_dir.chmod(0o755)
    created_backup: Path | None = None

    def fake_run_backup(command: list[str]) -> str:
        nonlocal created_backup
        shell = command[-1]
        matched = re.search(r">\s+(.+)$", shell)
        assert matched is not None
        created_backup = Path(shlex.split(matched.group(1))[0])
        created_backup.write_text("backup", encoding="utf-8")
        return ""

    monkeypatch.setattr("larops.commands.db.run_backup", fake_run_backup)
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
            "--target-dir",
            str(backup_dir),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert created_backup is not None and created_backup.exists()
    assert created_backup.with_name(f"{created_backup.name}.json").exists()


def test_db_credential_set_postgres_and_show(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "demo.pgpass"
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
            "--engine",
            "postgres",
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
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert "127.0.0.1:5432:*:appuser:super-secret" in secret.read_text(encoding="utf-8")

    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "credential",
            "show",
            "demo.test",
            "--engine",
            "postgres",
            "--credential-file",
            str(secret),
        ],
    )
    assert show_result.exit_code == 0
    assert "Credential file status for demo.test" in show_result.stdout


def test_build_backup_command_postgres_uses_pg_dump(tmp_path: Path) -> None:
    secret = tmp_path / "db.pgpass"
    write_postgres_secret(secret)
    command = build_backup_command(
        backup_file=tmp_path / "backup.sql.gz",
        database="appdb",
        credential_file=secret,
        engine="postgres",
    )
    assert command[:2] == ["bash", "-lc"]
    assert "pg_dump" in command[2]
    assert "PGPASSFILE=" in command[2]


def test_build_restore_command_postgres_uses_psql(tmp_path: Path) -> None:
    secret = tmp_path / "db.pgpass"
    write_postgres_secret(secret)
    backup = tmp_path / "backup.sql.gz"
    backup.write_text("fake", encoding="utf-8")
    command = build_restore_command(
        backup_file=backup,
        database="appdb",
        credential_file=secret,
        engine="postgresql",
    )
    assert command[:2] == ["bash", "-lc"]
    assert "psql" in command[2]
    assert "PGPASSFILE=" in command[2]


def test_db_status_reports_latest_backup_and_manifest(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    backup_dir = tmp_path / "state" / "backups" / "demo.test"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / "demo_test_20260306T000000Z.sql.gz"
    with gzip.open(backup_file, "wb") as handle:
        handle.write(b"backup")
    manifest = backup_file.with_name(f"{backup_file.name}.json")
    manifest.write_text(json.dumps({"size_bytes": backup_file.stat().st_size, "sha256": "stub"}), encoding="utf-8")

    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "db", "status", "demo.test", "--target-dir", str(backup_dir)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    report = payload["status_report"]
    assert report["count"] == 1
    assert report["manifest_present"] is True
    assert report["latest_backup"] == str(backup_file)


def test_db_verify_warns_without_manifest(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    backup_file = tmp_path / "backup.sql.gz"
    with gzip.open(backup_file, "wb") as handle:
        handle.write(b"backup")

    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "db", "verify", "--backup-file", str(backup_file)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    verification = payload["verification"]
    assert payload["status"] == "warn"
    assert verification["manifest_present"] is False
    assert verification["gzip_check"] == "ok"


def test_db_auto_backup_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "db.cnf"
    write_secret(secret)
    backup_dir = tmp_path / "backups"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "auto-backup",
            "enable",
            "demo.test",
            "--database",
            "appdb",
            "--credential-file",
            str(secret),
            "--target-dir",
            str(backup_dir),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-db-backup-demo-test.service"
    timer = tmp_path / "units" / "larops-db-backup-demo-test.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "db backup demo.test" in service_body
    assert f"--credential-file {secret}" in service_body
    assert f"--target-dir {backup_dir}" in service_body


def test_db_auto_backup_status_and_disable(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "auto-backup",
            "enable",
            "demo.test",
            "--database",
            "appdb",
            "--apply",
        ],
    )
    assert enable.exit_code == 0

    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "db", "auto-backup", "status", "demo.test"],
    )
    assert status.exit_code == 0
    payload = json.loads(status.stdout.strip())
    assert payload["auto_backup"]["service_unit_exists"] is True
    assert payload["auto_backup"]["timer_unit_exists"] is True

    disable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "auto-backup",
            "disable",
            "demo.test",
            "--remove-units",
            "--apply",
        ],
    )
    assert disable.exit_code == 0
    assert not (tmp_path / "units" / "larops-db-backup-demo-test.service").exists()
    assert not (tmp_path / "units" / "larops-db-backup-demo-test.timer").exists()


def test_db_backup_plan_mode_postgres(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    secret = tmp_path / "db.pgpass"
    write_postgres_secret(secret)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "db",
            "backup",
            "demo.test",
            "--engine",
            "postgres",
            "--database",
            "appdb",
            "--credential-file",
            str(secret),
        ],
    )
    assert result.exit_code == 0
    assert "DB backup plan prepared for demo.test" in result.stdout


def test_db_rejects_unsupported_engine(tmp_path: Path) -> None:
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
            "--engine",
            "sqlite",
            "--database",
            "appdb",
            "--credential-file",
            str(secret),
        ],
    )
    assert result.exit_code == 2
    assert "Unsupported DB engine" in result.stdout
