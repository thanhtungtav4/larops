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


def test_monitor_scan_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "monitor", "scan", "run"])
    assert result.exit_code == 0
    assert "Monitor scan plan prepared." in result.stdout


def test_monitor_scan_apply_incremental_reads_only_new_lines(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    nginx_log = tmp_path / "access.log"
    state_file = tmp_path / "scan_state.json"
    nginx_log.write_text(
        "\n".join(
            [
                '1.1.1.1 - - [03/Mar/2026:10:00:00 +0700] "GET /.env HTTP/1.1" 404 100 "-" "curl/8.0"',
                '1.1.1.1 - - [03/Mar/2026:10:00:01 +0700] "GET /wp-login.php HTTP/1.1" 404 100 "-" "curl/8.0"',
                '9.9.9.9 - - [03/Mar/2026:10:00:02 +0700] "GET /ok HTTP/1.1" 200 10 "-" "curl/8.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    first = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "scan",
            "run",
            "--nginx-log-path",
            str(nginx_log),
            "--state-file",
            str(state_file),
            "--threshold-hits",
            "2",
            "--apply",
        ],
    )
    assert first.exit_code == 0
    first_lines = [json.loads(line) for line in first.stdout.strip().splitlines()]
    first_result = first_lines[-1]["result"]
    assert first_result["suspicious_total"] == 2
    assert len(first_result["alerts"]) == 1
    assert first_result["alerts"][0]["ip"] == "1.1.1.1"

    with nginx_log.open("a", encoding="utf-8") as handle:
        handle.write('2.2.2.2 - - [03/Mar/2026:10:01:00 +0700] "GET /.git/config HTTP/1.1" 404 100 "-" "curl/8.0"\n')

    second = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "scan",
            "run",
            "--nginx-log-path",
            str(nginx_log),
            "--state-file",
            str(state_file),
            "--threshold-hits",
            "2",
            "--apply",
        ],
    )
    assert second.exit_code == 0
    second_lines = [json.loads(line) for line in second.stdout.strip().splitlines()]
    second_result = second_lines[-1]["result"]
    assert second_result["lines_read"] == 1
    assert second_result["suspicious_total"] == 1
    assert second_result["alerts"] == []


def test_monitor_fim_init_and_run_detects_changes(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    app_root = tmp_path / "app"
    (app_root / "routes").mkdir(parents=True, exist_ok=True)
    (app_root / "config").mkdir(parents=True, exist_ok=True)
    (app_root / "public").mkdir(parents=True, exist_ok=True)
    (app_root / ".env").write_text("APP_ENV=prod\n", encoding="utf-8")
    (app_root / "composer.lock").write_text("{\"packages\":[]}\n", encoding="utf-8")
    (app_root / "public" / "index.php").write_text("<?php echo 'ok';\n", encoding="utf-8")
    (app_root / "routes" / "web.php").write_text("<?php\n", encoding="utf-8")
    (app_root / "config" / "app.php").write_text("<?php return [];\n", encoding="utf-8")
    baseline_file = tmp_path / "fim-baseline.json"

    init = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "fim",
            "init",
            "--root",
            str(app_root),
            "--baseline-file",
            str(baseline_file),
            "--apply",
        ],
    )
    assert init.exit_code == 0
    assert baseline_file.exists()

    (app_root / ".env").write_text("APP_ENV=staging\n", encoding="utf-8")
    (app_root / "config" / "new.php").write_text("<?php return ['x' => 1];\n", encoding="utf-8")
    (app_root / "routes" / "web.php").unlink()

    run = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "fim",
            "run",
            "--baseline-file",
            str(baseline_file),
            "--apply",
        ],
    )
    assert run.exit_code == 0
    lines = [json.loads(line) for line in run.stdout.strip().splitlines()]
    payload = lines[-1]["result"]
    assert payload["has_changes"] is True
    assert payload["counts"]["changed"] >= 1
    assert payload["counts"]["deleted"] >= 1
    assert payload["counts"]["created"] >= 1


def test_monitor_fim_run_clean_when_no_changes(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    app_root = tmp_path / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / ".env").write_text("APP_ENV=prod\n", encoding="utf-8")
    baseline_file = tmp_path / "fim-baseline.json"

    init = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "fim",
            "init",
            "--root",
            str(app_root),
            "--baseline-file",
            str(baseline_file),
            "--pattern",
            ".env",
            "--apply",
        ],
    )
    assert init.exit_code == 0

    run = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "fim",
            "run",
            "--baseline-file",
            str(baseline_file),
            "--apply",
        ],
    )
    assert run.exit_code == 0
    lines = [json.loads(line) for line in run.stdout.strip().splitlines()]
    payload = lines[-1]["result"]
    assert payload["has_changes"] is False
