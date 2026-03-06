import json
from pathlib import Path
from subprocess import CompletedProcess

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


def test_monitor_fim_init_glob_includes_nested_files(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    app_root = tmp_path / "app"
    nested_dir = app_root / "config" / "packages"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (nested_dir / "security.php").write_text("<?php return ['x' => 1];\n", encoding="utf-8")
    baseline_file = tmp_path / "fim-baseline.json"

    init = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "fim",
            "init",
            "--root",
            str(app_root),
            "--baseline-file",
            str(baseline_file),
            "--pattern",
            "config/*",
            "--apply",
        ],
    )
    assert init.exit_code == 0
    lines = [json.loads(line) for line in init.stdout.strip().splitlines()]
    result = lines[-1]["result"]
    assert result["file_count"] == 1


def test_monitor_service_run_restarts_down_service_and_emits_event(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    state_file = tmp_path / "service-watch.json"
    active_checks = {"mariadb": 0}

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"] and command[2] == "mariadb":
            active_checks["mariadb"] += 1
            state = "failed\n" if active_checks["mariadb"] == 1 else "active\n"
            return CompletedProcess(command, 0, stdout=state, stderr="")
        if command[:2] == ["systemctl", "is-enabled"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        if command[:2] == ["systemctl", "restart"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.monitor_service_watch.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "service",
            "run",
            "--service",
            "sql",
            "--state-file",
            str(state_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    payload = lines[-1]["result"]
    assert payload["services"][0]["service"] == "mariadb"
    assert payload["services"][0]["transition"] == "restarted"

    event_lines = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert event_lines[-1]["event_type"] == "monitor.service.restarted"
    assert event_lines[-1]["severity"] == "warn"


def test_monitor_service_run_skips_duplicate_alerts_during_cooldown(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    state_file = tmp_path / "service-watch.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "services": {
                    "mariadb": {
                        "active": "failed",
                        "enabled": "enabled",
                        "last_checked_at": "2026-03-06T10:00:00+00:00",
                        "last_restart_attempt_at": "2999-03-06T10:00:00+00:00",
                        "last_transition": "restart_failed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "restart"]:
            raise AssertionError("restart should be skipped during cooldown")
        if command[:2] == ["systemctl", "is-active"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="failed\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.monitor_service_watch.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "service",
            "run",
            "--service",
            "mariadb",
            "--state-file",
            str(state_file),
            "--restart-cooldown",
            "300",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    payload = lines[-1]["result"]
    assert payload["services"][0]["action"] == "cooldown"
    assert payload["services"][0]["transition"] == "steady"

    events_path = tmp_path / "events.jsonl"
    if events_path.exists():
        assert events_path.read_text(encoding="utf-8").strip() == ""


def test_monitor_service_run_profile_resolves_php_fpm_unit(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    known_services = {
        "nginx": {"active": "active", "enabled": "enabled"},
        "php8.3-fpm": {"active": "active", "enabled": "enabled"},
        "mariadb": {"active": "active", "enabled": "enabled"},
        "redis-server": {"active": "active", "enabled": "enabled"},
    }

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            service = command[2]
            if service in known_services:
                return CompletedProcess(command, 0, stdout=f"{known_services[service]['active']}\n", stderr="")
            return CompletedProcess(command, 3, stdout="unknown\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            service = command[2]
            if service in known_services:
                return CompletedProcess(command, 0, stdout=f"{known_services[service]['enabled']}\n", stderr="")
            return CompletedProcess(command, 1, stdout="", stderr="Failed to get unit file state\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.monitor_service_watch.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "service",
            "run",
            "--profile",
            "laravel-host",
            "--no-restart-down-services",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    payload = lines[-1]["result"]
    services = [item["service"] for item in payload["services"]]
    assert services == ["nginx", "php8.3-fpm", "mariadb", "redis-server"]


def test_monitor_service_run_does_not_restart_activating_service(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "restart"]:
            raise AssertionError("activating service should not be restarted")
        if command[:2] == ["systemctl", "is-active"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="activating\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"] and command[2] == "mariadb":
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.monitor_service_watch.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "monitor",
            "service",
            "run",
            "--service",
            "mariadb",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["result"]["services"][0]["transition"] == "steady"
    assert payload["result"]["services"][0]["action"] == "none"


def test_monitor_app_run_emits_alert_once_for_failed_check(tmp_path: Path) -> None:
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
                "doctor:",
                "  app_command_checks:",
                "    - name: queue-failed-empty",
                "      command: test -f .larops-check-ok",
                "      timeout_seconds: 5",
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
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("ok", encoding="utf-8")

    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    first = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "monitor", "app", "run", "demo.test", "--apply"],
    )
    assert first.exit_code == 0
    first_lines = [json.loads(line) for line in first.stdout.strip().splitlines()]
    assert first_lines[-1]["status"] == "error"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    first_alert_count = [event["event_type"] for event in events].count("monitor.app.alert")
    assert first_alert_count >= 1

    second = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "monitor", "app", "run", "demo.test", "--apply"],
    )
    assert second.exit_code == 0
    events_after = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events_after].count("monitor.app.alert") == first_alert_count


def test_monitor_app_run_queue_checks_alert_once(tmp_path: Path, monkeypatch) -> None:
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
                "doctor:",
                "  queue_backlog_checks:",
                "    - name: default-queue",
                "      connection: redis",
                "      queue: default",
                "      max_size: 10",
                "      timeout_seconds: 10",
                "  failed_job_checks:",
                "    - name: failed-jobs",
                "      max_count: 0",
                "      timeout_seconds: 10",
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
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("ok", encoding="utf-8")

    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["bash", "-lc"] and "app('queue')->connection" in command[2]:
            return CompletedProcess(command, 0, stdout="15", stderr="")
        if command[:2] == ["bash", "-lc"] and "FailedJobProviderInterface" in command[2]:
            return CompletedProcess(command, 0, stdout="3", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    first = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "monitor", "app", "run", "demo.test", "--apply"],
    )
    assert first.exit_code == 0
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    first_alert_count = [event["event_type"] for event in events].count("monitor.app.alert")
    assert first_alert_count >= 2

    second = runner.invoke(
        app,
        ["--config", str(config_file), "--json", "monitor", "app", "run", "demo.test", "--apply"],
    )
    assert second.exit_code == 0
    events_after = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events_after].count("monitor.app.alert") == first_alert_count
