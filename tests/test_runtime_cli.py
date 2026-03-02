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


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("runtime-test", encoding="utf-8")
    return source


def bootstrap_app(tmp_path: Path, domain: str = "demo.test") -> Path:
    config = write_config(tmp_path)
    source = make_source(tmp_path)
    create = runner.invoke(app, ["--config", str(config), "app", "create", domain, "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", domain, "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0
    return config


def json_output(result_stdout: str) -> dict:
    lines = [line for line in result_stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_worker_lifecycle_commands(tmp_path: Path) -> None:
    config = bootstrap_app(tmp_path)
    domain = "demo.test"

    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "worker",
            "enable",
            domain,
            "--queue",
            "emails",
            "--concurrency",
            "2",
            "--tries",
            "5",
            "--timeout",
            "120",
            "--apply",
        ],
    )
    assert enable.exit_code == 0

    status = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", domain])
    payload = json_output(status.stdout)
    assert payload["status"] == "ok"
    assert payload["process"]["enabled"] is True
    assert payload["process"]["options"]["queue"] == "emails"

    restart = runner.invoke(app, ["--config", str(config), "worker", "restart", domain, "--apply"])
    assert restart.exit_code == 0

    status_after = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", domain])
    payload_after = json_output(status_after.stdout)
    assert payload_after["process"]["restart_count"] == 1

    disable = runner.invoke(app, ["--config", str(config), "worker", "disable", domain, "--apply"])
    assert disable.exit_code == 0

    status_disabled = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", domain])
    payload_disabled = json_output(status_disabled.stdout)
    assert payload_disabled["process"]["enabled"] is False


def test_scheduler_lifecycle_commands(tmp_path: Path) -> None:
    config = bootstrap_app(tmp_path)
    domain = "demo.test"

    enable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "scheduler",
            "enable",
            domain,
            "--command",
            "php artisan schedule:run",
            "--apply",
        ],
    )
    assert enable.exit_code == 0

    run_once = runner.invoke(
        app,
        ["--config", str(config), "scheduler", "run-once", domain, "--apply"],
    )
    assert run_once.exit_code == 0

    status = runner.invoke(app, ["--config", str(config), "--json", "scheduler", "status", domain])
    payload = json_output(status.stdout)
    assert payload["process"]["enabled"] is True
    assert payload["process"]["run_count"] >= 1


def test_horizon_lifecycle_commands(tmp_path: Path) -> None:
    config = bootstrap_app(tmp_path)
    domain = "demo.test"

    enable = runner.invoke(app, ["--config", str(config), "horizon", "enable", domain, "--apply"])
    assert enable.exit_code == 0

    terminate = runner.invoke(app, ["--config", str(config), "horizon", "terminate", domain, "--apply"])
    assert terminate.exit_code == 0

    status = runner.invoke(app, ["--config", str(config), "--json", "horizon", "status", domain])
    payload = json_output(status.stdout)
    assert payload["process"]["enabled"] is True
    assert payload["process"]["terminate_count"] == 1


def test_runtime_enable_requires_deployed_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    worker_enable = runner.invoke(
        app,
        ["--config", str(config), "worker", "enable", "demo.test", "--apply"],
    )
    assert worker_enable.exit_code == 2
    assert "Deploy app before enabling worker" in worker_enable.stdout
