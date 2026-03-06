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
                "  user: root",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_monitor_scan_timer_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    state_file = tmp_path / "scan-state.json"
    nginx_log = tmp_path / "access.log"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "scan",
            "timer",
            "enable",
            "--state-file",
            str(state_file),
            "--nginx-log-path",
            str(nginx_log),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-monitor-scan.service"
    timer = tmp_path / "units" / "larops-monitor-scan.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "monitor scan run" in service_body
    assert f"--state-file {state_file}" in service_body
    assert f"--nginx-log-path {nginx_log}" in service_body
    assert "--window-seconds 300" in service_body


def test_monitor_scan_timer_disable_remove_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        ["--config", str(config), "monitor", "scan", "timer", "enable", "--apply"],
    )
    assert enable.exit_code == 0

    disable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "scan",
            "timer",
            "disable",
            "--remove-units",
            "--apply",
        ],
    )
    assert disable.exit_code == 0
    assert not (tmp_path / "units" / "larops-monitor-scan.service").exists()
    assert not (tmp_path / "units" / "larops-monitor-scan.timer").exists()


def test_monitor_fim_timer_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    baseline_file = tmp_path / "fim-baseline.json"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "fim",
            "timer",
            "enable",
            "--baseline-file",
            str(baseline_file),
            "--update-baseline",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-monitor-fim.service"
    timer = tmp_path / "units" / "larops-monitor-fim.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "monitor fim run" in service_body
    assert f"--baseline-file {baseline_file}" in service_body
    assert "--update-baseline" in service_body


def test_monitor_fim_timer_status(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        ["--config", str(config), "monitor", "fim", "timer", "enable", "--apply"],
    )
    assert enable.exit_code == 0
    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "monitor", "fim", "timer", "status"],
    )
    assert status.exit_code == 0
    import json

    lines = [json.loads(line) for line in status.stdout.strip().splitlines()]
    timer = lines[-1]["timer"]
    assert timer["service_unit_exists"] is True
    assert timer["timer_unit_exists"] is True


def test_monitor_service_timer_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    state_file = tmp_path / "service-watch.json"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "service",
            "timer",
            "enable",
            "--service",
            "mariadb",
            "--service",
            "redis",
            "--state-file",
            str(state_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-monitor-service.service"
    timer = tmp_path / "units" / "larops-monitor-service.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "monitor service run" in service_body
    assert f"--state-file {state_file}" in service_body
    assert "--service mariadb" in service_body
    assert "--service redis" in service_body
    assert "--restart-down-services" in service_body


def test_monitor_service_timer_status(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "service",
            "timer",
            "enable",
            "--service",
            "mariadb",
            "--apply",
        ],
    )
    assert enable.exit_code == 0
    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "monitor", "service", "timer", "status"],
    )
    assert status.exit_code == 0
    import json

    lines = [json.loads(line) for line in status.stdout.strip().splitlines()]
    timer = lines[-1]["timer"]
    assert timer["service_unit_exists"] is True
    assert timer["timer_unit_exists"] is True


def test_monitor_service_timer_enable_accepts_profile(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "service",
            "timer",
            "enable",
            "--profile",
            "laravel-host",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-monitor-service.service"
    service_body = service.read_text(encoding="utf-8")
    assert "--profile laravel-host" in service_body


def test_monitor_app_timer_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    state_file = tmp_path / "app-monitor.json"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "monitor",
            "app",
            "timer",
            "enable",
            "demo.test",
            "--state-file",
            str(state_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-monitor-app-demo-test.service"
    timer = tmp_path / "units" / "larops-monitor-app-demo-test.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "monitor app run demo.test" in service_body
    assert f"--state-file {state_file}" in service_body


def test_monitor_app_timer_status(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        ["--config", str(config), "monitor", "app", "timer", "enable", "demo.test", "--apply"],
    )
    assert enable.exit_code == 0
    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "monitor", "app", "timer", "status", "demo.test"],
    )
    assert status.exit_code == 0
    import json

    lines = [json.loads(line) for line in status.stdout.strip().splitlines()]
    timer = lines[-1]["timer"]
    assert timer["service_unit_exists"] is True
    assert timer["timer_unit_exists"] is True
