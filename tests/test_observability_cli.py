import json
from pathlib import Path

import yaml
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


def test_observability_logs_enable_vector_unmanaged_writes_unit_and_config(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    config_file = tmp_path / "vector" / "logs.yaml"
    data_dir = tmp_path / "vector-data"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "observability",
            "logs",
            "enable",
            "--sink",
            "vector",
            "--vector-address",
            "127.0.0.1:6000",
            "--vector-bin",
            "/bin/echo",
            "--config-file",
            str(config_file),
            "--data-dir",
            str(data_dir),
            "--extra-log",
            str(tmp_path / "custom.log"),
            "--apply",
        ],
    )
    assert result.exit_code == 0

    unit_path = tmp_path / "units" / "larops-observability-logs.service"
    assert unit_path.exists()
    assert config_file.exists()
    body = unit_path.read_text(encoding="utf-8")
    assert "--watch-config" in body
    assert "User=root" in body

    payload = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert payload["sinks"]["ship_logs"]["type"] == "vector"
    assert payload["sinks"]["ship_logs"]["address"] == "127.0.0.1:6000"
    assert payload["sources"]["larops_events"]["include"] == [str(tmp_path / "events.jsonl")]
    assert str(tmp_path / "custom.log") in payload["sources"]["extra_logs"]["include"]


def test_observability_logs_enable_http_renders_env_file_and_auth(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    config_file = tmp_path / "vector" / "logs-http.yaml"
    data_dir = tmp_path / "vector-http-data"
    env_file = tmp_path / "vector-http.env"
    env_file.write_text("LAROPS_VECTOR_HTTP_TOKEN=secret\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "observability",
            "logs",
            "enable",
            "--sink",
            "http",
            "--http-uri",
            "https://logs.example.com/ingest",
            "--http-env-file",
            str(env_file),
            "--http-bearer-token-env-var",
            "LAROPS_VECTOR_HTTP_TOKEN",
            "--vector-bin",
            "/bin/echo",
            "--config-file",
            str(config_file),
            "--data-dir",
            str(data_dir),
            "--apply",
        ],
    )
    assert result.exit_code == 0

    unit_path = tmp_path / "units" / "larops-observability-logs.service"
    body = unit_path.read_text(encoding="utf-8")
    assert f'EnvironmentFile=-"{env_file}"' in body

    payload = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    sink = payload["sinks"]["ship_logs"]
    assert sink["type"] == "http"
    assert sink["uri"] == "https://logs.example.com/ingest"
    assert sink["auth"]["token"] == "${LAROPS_VECTOR_HTTP_TOKEN}"


def test_observability_logs_status_and_disable_cleanup(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    config_file = tmp_path / "vector" / "logs.yaml"
    data_dir = tmp_path / "vector-data"

    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "observability",
            "logs",
            "enable",
            "--sink",
            "vector",
            "--vector-address",
            "127.0.0.1:6000",
            "--vector-bin",
            "/bin/echo",
            "--config-file",
            str(config_file),
            "--data-dir",
            str(data_dir),
            "--apply",
        ],
    )
    assert enable.exit_code == 0

    status = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "observability",
            "logs",
            "status",
            "--vector-bin",
            "/bin/echo",
            "--config-file",
            str(config_file),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert status.exit_code == 0
    payload = json.loads(status.stdout.strip())
    assert payload["logs"]["unit_exists"] is True
    assert payload["logs"]["config_exists"] is True
    assert payload["logs"]["data_dir_exists"] is True
    assert payload["logs"]["vector_bin_exists"] is True

    disable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "observability",
            "logs",
            "disable",
            "--config-file",
            str(config_file),
            "--data-dir",
            str(data_dir),
            "--remove-files",
            "--apply",
        ],
    )
    assert disable.exit_code == 0
    assert not (tmp_path / "units" / "larops-observability-logs.service").exists()
    assert not config_file.exists()
    assert not data_dir.exists()
