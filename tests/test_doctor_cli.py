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
