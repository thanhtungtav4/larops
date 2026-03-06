import json
from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from larops.cli import app
from larops.services.doctor_service import run_host_checks

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


def test_doctor_quick_json(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "quick"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["message"] == "Doctor quick report for host"
    assert "report" in payload


def test_doctor_run_for_unknown_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "doctor", "run", "missing.test"])
    assert result.exit_code == 0
    assert "Doctor report for missing.test" in result.stdout


def test_doctor_run_reports_missing_restore_verify_for_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "backup-verify:demo.test" in names


def test_doctor_run_reports_failed_restore_verify_as_error(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    report_file = tmp_path / "state" / "backups" / "demo.test" / "last_restore_verify.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(
            {
                "status": "error",
                "verified_at": "2026-03-06T00:00:00+00:00",
                "error": "restore verification failed",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    checks = {check["name"]: check for check in payload["report"]["checks"]}
    assert checks["backup-verify:demo.test"]["status"] == "error"
    assert "restore verification failed" in checks["backup-verify:demo.test"]["detail"]


def test_doctor_run_executes_configured_app_command_checks(tmp_path: Path) -> None:
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
                "    - name: release-file",
                "      command: test -f README.txt",
                "      timeout_seconds: 5",
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

    result = runner.invoke(app, ["--config", str(config_file), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "app-check:release-file" in names


def test_doctor_run_reports_heartbeat_and_runtime_checks(tmp_path: Path) -> None:
    heartbeat_file = tmp_path / "scheduler-heartbeat"
    heartbeat_file.write_text("ok\n", encoding="utf-8")
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
                "doctor:",
                "  heartbeat_checks:",
                "    - name: scheduler-heartbeat",
                f"      path: {heartbeat_file}",
                "      max_age_seconds: 300",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("ok", encoding="utf-8")
    deploy = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0
    worker = runner.invoke(app, ["--config", str(config_file), "worker", "enable", "demo.test", "--apply"])
    assert worker.exit_code == 0

    result = runner.invoke(app, ["--config", str(config_file), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "heartbeat:scheduler-heartbeat" in names
    assert "runtime:demo.test:worker" in names


def test_doctor_run_reports_queue_backlog_and_failed_jobs(tmp_path: Path, monkeypatch) -> None:
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
            return CompletedProcess(command, 0, stdout="12", stderr="")
        if command[:2] == ["bash", "-lc"] and "FailedJobProviderInterface" in command[2]:
            return CompletedProcess(command, 0, stdout="2", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    result = runner.invoke(app, ["--config", str(config_file), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    checks = {check["name"]: check for check in payload["report"]["checks"]}
    assert checks["queue-backlog:default-queue"]["status"] == "error"
    assert checks["failed-jobs:failed-jobs"]["status"] == "error"


def test_run_host_checks_includes_service_watchdog_timer(monkeypatch, tmp_path: Path) -> None:
    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    checks = run_host_checks(
        state_path=tmp_path / "state",
        events_path=tmp_path / "events.jsonl",
        quick=False,
        unit_dir=tmp_path / "units",
        systemd_manage=True,
    )
    names = {check.name for check in checks}
    assert "systemd:larops-monitor-service.timer" in names


def test_run_host_checks_includes_observability_logs_service_when_unit_exists(monkeypatch, tmp_path: Path) -> None:
    unit_path = tmp_path / "units" / "larops-observability-logs.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nDescription=test\n", encoding="utf-8")

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    checks = run_host_checks(
        state_path=tmp_path / "state",
        events_path=tmp_path / "events.jsonl",
        quick=False,
        unit_dir=tmp_path / "units",
        systemd_manage=True,
    )
    names = {check.name for check in checks}
    assert "systemd:larops-observability-logs.service" in names


def test_run_host_checks_includes_doctor_metrics_timer_when_unit_exists(monkeypatch, tmp_path: Path) -> None:
    unit_path = tmp_path / "units" / "larops-doctor-metrics.timer"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nDescription=test\n", encoding="utf-8")

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    checks = run_host_checks(
        state_path=tmp_path / "state",
        events_path=tmp_path / "events.jsonl",
        quick=False,
        unit_dir=tmp_path / "units",
        systemd_manage=True,
    )
    names = {check.name for check in checks}
    assert "systemd:larops-doctor-metrics.timer" in names


def test_run_host_checks_marks_failed_timers_as_error(monkeypatch, tmp_path: Path) -> None:
    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"] and command[2].endswith(".timer"):
            return CompletedProcess(command, 3, stdout="failed\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"] and command[2].endswith(".timer"):
            return CompletedProcess(command, 1, stdout="disabled\n", stderr="")
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="inactive\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 1, stdout="disabled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.doctor_service.run_command", fake_run_command)

    checks = run_host_checks(
        state_path=tmp_path / "state",
        events_path=tmp_path / "events.jsonl",
        quick=False,
        unit_dir=tmp_path / "units",
        systemd_manage=True,
    )
    timer_checks = {check.name: check for check in checks if check.name.endswith(".timer")}
    assert timer_checks["systemd:larops-monitor-service.timer"].status == "error"


def test_doctor_run_reports_offsite_backup_status(tmp_path: Path, monkeypatch) -> None:
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
                "backups:",
                "  offsite:",
                "    enabled: true",
                "    provider: s3",
                "    bucket: larops-backups",
                "    prefix: prod/backups",
                "    region: auto",
                "    endpoint_url: https://example.r2.cloudflarestorage.com",
                "    access_key_id: key-id",
                "    secret_access_key: secret-key",
                "    stale_hours: 12",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    monkeypatch.setattr(
        "larops.services.doctor_service.offsite_status",
        lambda **_: {
            "status": "ok",
            "bucket": "larops-backups",
            "prefix": "prod/backups/demo.test",
            "count": 1,
            "latest_object": "prod/backups/demo.test/demo_test.sql.gz.enc",
            "age_hours": 2.0,
        },
    )

    result = runner.invoke(app, ["--config", str(config_file), "--json", "doctor", "run", "demo.test"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    names = {check["name"] for check in payload["report"]["checks"]}
    assert "backup-offsite:demo.test" in names


def test_doctor_fleet_reports_registered_apps(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    monkeypatch.setattr("larops.commands.doctor.list_registered_apps", lambda _: ["alpha.test", "beta.test"])
    monkeypatch.setattr(
        "larops.commands.doctor._host_report",
        lambda _app_ctx, quick: {
            "overall": "ok",
            "checks": [{"name": "systemd:larops-monitor-service.timer", "status": "ok", "detail": "active=active, enabled=enabled"}],
            "counts": {"ok": 1, "warn": 0, "error": 0},
        },
    )
    monkeypatch.setattr(
        "larops.commands.doctor._app_report",
        lambda _app_ctx, domain: {
            "overall": "warn" if domain == "alpha.test" else "error",
            "checks": [{"name": f"app:{domain}:current", "status": "warn" if domain == "alpha.test" else "error", "detail": "stub"}],
            "counts": {"ok": 0, "warn": 1 if domain == "alpha.test" else 0, "error": 1 if domain == "beta.test" else 0},
        },
    )

    result = runner.invoke(app, ["--config", str(config), "--json", "doctor", "fleet"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    report = payload["report"]
    assert report["overall"] == "error"
    assert report["registered_apps"] == ["alpha.test", "beta.test"]
    targets = {item["target"]: item for item in report["targets"]}
    assert targets["host"]["overall"] == "ok"
    assert targets["alpha.test"]["overall"] == "warn"
    assert targets["beta.test"]["overall"] == "error"
    assert "checks" not in targets["host"]


def test_doctor_fleet_include_checks_and_skip_host(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    monkeypatch.setattr("larops.commands.doctor.list_registered_apps", lambda _: ["alpha.test"])
    monkeypatch.setattr(
        "larops.commands.doctor._app_report",
        lambda _app_ctx, domain: {
            "overall": "ok",
            "checks": [{"name": f"app:{domain}:metadata", "status": "ok", "detail": "stub"}],
            "counts": {"ok": 1, "warn": 0, "error": 0},
        },
    )

    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "doctor", "fleet", "--skip-host", "--include-checks"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    report = payload["report"]
    assert report["target_count"] == 1
    assert report["targets"][0]["target"] == "alpha.test"
    assert report["targets"][0]["checks"][0]["name"] == "app:alpha.test:metadata"


def test_doctor_metrics_run_prints_prometheus_text(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    monkeypatch.setattr(
        "larops.commands.doctor._fleet_report",
        lambda _app_ctx, quick, include_host: {
            "overall": "warn",
            "registered_apps": ["alpha.test"],
            "target_count": 2,
            "counts": {"ok": 0, "warn": 1, "error": 1},
            "targets": [
                {
                    "target": "host",
                    "overall": "ok",
                    "counts": {"ok": 3, "warn": 0, "error": 0},
                    "checks": [{"name": "disk:/", "status": "ok", "detail": "50% used"}],
                },
                {
                    "target": "alpha.test",
                    "overall": "warn",
                    "counts": {"ok": 2, "warn": 1, "error": 0},
                    "checks": [{"name": "backup-verify:alpha.test", "status": "warn", "detail": "age=200h"}],
                },
            ],
        },
    )

    result = runner.invoke(app, ["--config", str(config), "doctor", "metrics", "run", "--include-checks"])
    assert result.exit_code == 0
    assert '# HELP larops_fleet_status Overall LarOps fleet status code (0=ok,1=warn,2=error).' in result.stdout
    assert 'larops_target_status{target="alpha.test"} 1' in result.stdout
    assert 'larops_check_status{check="backup-verify:alpha.test",target="alpha.test"} 1' in result.stdout


def test_doctor_metrics_run_writes_output_file(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    output_file = tmp_path / "metrics" / "larops.prom"

    monkeypatch.setattr(
        "larops.commands.doctor._fleet_report",
        lambda _app_ctx, quick, include_host: {
            "overall": "ok",
            "registered_apps": [],
            "target_count": 1,
            "counts": {"ok": 1, "warn": 0, "error": 0},
            "targets": [
                {
                    "target": "host",
                    "overall": "ok",
                    "counts": {"ok": 3, "warn": 0, "error": 0},
                    "checks": [{"name": "disk:/", "status": "ok", "detail": "50% used"}],
                }
            ],
        },
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "doctor",
            "metrics",
            "run",
            "--output-file",
            str(output_file),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert output_file.exists()
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["output_file"] == str(output_file)
    assert 'larops_target_status{target="host"} 0' in output_file.read_text(encoding="utf-8")


def test_doctor_metrics_timer_enable_status_disable(tmp_path: Path) -> None:
    config = tmp_path / "larops.yaml"
    config.write_text(
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
    output_file = tmp_path / "metrics" / "larops.prom"

    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "doctor",
            "metrics",
            "timer",
            "enable",
            "--output-file",
            str(output_file),
            "--include-checks",
            "--apply",
        ],
    )
    assert enable.exit_code == 0
    service = tmp_path / "units" / "larops-doctor-metrics.service"
    timer = tmp_path / "units" / "larops-doctor-metrics.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "doctor metrics run" in service_body
    assert f"--output-file {output_file}" in service_body
    assert "--include-checks" in service_body

    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "doctor", "metrics", "timer", "status"],
    )
    assert status.exit_code == 0
    payload = json.loads(status.stdout.strip())
    assert payload["metrics_timer"]["service_unit_exists"] is True
    assert payload["metrics_timer"]["timer_unit_exists"] is True

    disable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "doctor",
            "metrics",
            "timer",
            "disable",
            "--remove-units",
            "--apply",
        ],
    )
    assert disable.exit_code == 0
    assert not service.exists()
    assert not timer.exists()
