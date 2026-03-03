import os
import subprocess
from pathlib import Path

import pytest

from larops.services.db_service import (
    build_backup_command,
    build_restore_command,
    run_backup,
    run_restore,
    write_mysql_credentials,
    write_postgres_credentials,
)


pytestmark = pytest.mark.skipif(
    os.getenv("LAROPS_RUN_DB_INTEGRATION") != "1",
    reason="Set LAROPS_RUN_DB_INTEGRATION=1 to run DB integration tests.",
)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    completed = subprocess.run(command, capture_output=True, text=True, env=merged, check=False)
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout: {completed.stdout}\n"
            f"stderr: {completed.stderr}"
        )
    return (completed.stdout or "").strip()


def _mysql_base_command() -> tuple[list[str], str, str, str]:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "rootpass")
    base = ["mysql", "-h", host, "-P", port, "-u", user]
    return base, host, port, password


def _mysql_exec(sql: str, *, database: str | None = None) -> str:
    base, _host, _port, password = _mysql_base_command()
    cmd = [*base, f"-p{password}", "-N", "-s", "-e", sql]
    if database:
        cmd.extend(["--database", database])
    return _run(cmd)


def _postgres_base_command() -> tuple[list[str], dict[str, str], str, str]:
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgrespass")
    base = ["psql", "-h", host, "-p", port, "-U", user, "-At", "-c"]
    env = {"PGPASSWORD": password}
    return base, env, host, port


def _postgres_exec(sql: str, *, database: str = "postgres") -> str:
    base, env, _host, _port = _postgres_base_command()
    cmd = [*base[:-2], "-d", database, *base[-2:], sql]
    return _run(cmd, env=env)


def test_mysql_backup_restore_roundtrip(tmp_path: Path) -> None:
    db_name = "larops_mysql_integration"
    _mysql_exec(f"DROP DATABASE IF EXISTS {db_name}")
    _mysql_exec(f"CREATE DATABASE {db_name}")
    _mysql_exec("CREATE TABLE items (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(64) NOT NULL)", database=db_name)
    _mysql_exec("INSERT INTO items(name) VALUES ('before')", database=db_name)

    _base, host, port, password = _mysql_base_command()
    credential_file = tmp_path / "mysql.cnf"
    write_mysql_credentials(
        credential_file=credential_file,
        user=os.getenv("MYSQL_USER", "root"),
        password=password,
        host=host,
        port=int(port),
    )

    backup_file = tmp_path / "mysql-backup.sql.gz"
    backup_cmd = build_backup_command(
        backup_file=backup_file,
        database=db_name,
        credential_file=credential_file,
        engine="mysql",
    )
    run_backup(backup_cmd)
    assert backup_file.exists()

    _mysql_exec("TRUNCATE TABLE items", database=db_name)
    _mysql_exec("INSERT INTO items(name) VALUES ('after')", database=db_name)

    restore_cmd = build_restore_command(
        backup_file=backup_file,
        database=db_name,
        credential_file=credential_file,
        engine="mysql",
    )
    run_restore(restore_cmd)

    values = _mysql_exec("SELECT GROUP_CONCAT(name ORDER BY id SEPARATOR ',') FROM items", database=db_name)
    assert values == "before"

    _mysql_exec(f"DROP DATABASE IF EXISTS {db_name}")


def test_postgres_backup_restore_roundtrip(tmp_path: Path) -> None:
    db_name = "larops_pg_integration"
    _postgres_exec(f"DROP DATABASE IF EXISTS {db_name}")
    _postgres_exec(f"CREATE DATABASE {db_name}")
    _postgres_exec("CREATE TABLE items (id SERIAL PRIMARY KEY, name TEXT NOT NULL)", database=db_name)
    _postgres_exec("INSERT INTO items(name) VALUES ('before')", database=db_name)

    _base, _env, host, port = _postgres_base_command()
    credential_file = tmp_path / "postgres.pgpass"
    write_postgres_credentials(
        credential_file=credential_file,
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgrespass"),
        host=host,
        port=int(port),
    )

    backup_file = tmp_path / "postgres-backup.sql.gz"
    backup_cmd = build_backup_command(
        backup_file=backup_file,
        database=db_name,
        credential_file=credential_file,
        engine="postgres",
    )
    run_backup(backup_cmd)
    assert backup_file.exists()

    _postgres_exec("TRUNCATE TABLE items", database=db_name)
    _postgres_exec("INSERT INTO items(name) VALUES ('after')", database=db_name)

    restore_cmd = build_restore_command(
        backup_file=backup_file,
        database=db_name,
        credential_file=credential_file,
        engine="postgres",
    )
    run_restore(restore_cmd)

    values = _postgres_exec("SELECT string_agg(name, ',' ORDER BY id) FROM items", database=db_name)
    assert values == "before"

    _postgres_exec(f"DROP DATABASE IF EXISTS {db_name}")
