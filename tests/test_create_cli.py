import json
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app
from larops.services.ssl_service import SslServiceError

runner = CliRunner()


def write_config(tmp_path: Path) -> Path:
    source_base = tmp_path / "sources"
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                f"  source_base_path: {source_base}",
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


def make_source(tmp_path: Path, domain: str) -> Path:
    source = tmp_path / "sources" / domain
    source.mkdir(parents=True, exist_ok=True)
    (source / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    return source


def test_create_site_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--source", str(source)],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout
    assert "Plan mode finished. Use --apply to execute changes." in result.stdout


def test_site_create_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        ["--config", str(config), "site", "create", "demo.test", "--source", str(source)],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


def test_create_site_type_profile_applies_runtime_and_ssl(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "create", "site", "demo.test", "--type", "laravel"],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["profile"]["type"] == "laravel"
    assert lines[0]["runtime"]["worker"] is True
    assert lines[0]["runtime"]["scheduler"] is True
    assert lines[0]["ssl"] is True


def test_create_site_profile_override_runtime_flag(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "create",
            "site",
            "demo.test",
            "--type",
            "laravel",
            "--no-worker",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["runtime"]["worker"] is False
    assert lines[0]["runtime"]["scheduler"] is True


def test_create_site_small_vps_profile_applies_lightweight_defaults(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "create", "site", "demo.test", "--profile", "small-vps"],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["profile"]["preset"] == "small-vps"
    assert lines[0]["profile"]["type"] == "laravel"
    assert lines[0]["profile"]["cache"] == "fastcgi"
    assert lines[0]["runtime"]["worker"] is False
    assert lines[0]["runtime"]["scheduler"] is True
    assert lines[0]["runtime"]["horizon"] is False
    assert lines[0]["ssl"] is True


def test_create_site_small_vps_profile_allows_explicit_worker_override(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "create",
            "site",
            "demo.test",
            "--profile",
            "small-vps",
            "--worker",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["runtime"]["worker"] is True


def test_create_site_small_vps_profile_composes_consistently_with_explicit_type(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "create",
            "site",
            "demo.test",
            "--profile",
            "small-vps",
            "--type",
            "php",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["profile"]["type"] == "php"
    assert lines[0]["profile"]["cache"] == "fastcgi"
    assert lines[0]["runtime"]["worker"] is False
    assert lines[0]["runtime"]["scheduler"] is False
    assert lines[0]["runtime"]["horizon"] is False
    assert lines[0]["ssl"] is True


def test_create_site_invalid_type_rejected(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--type", "foobar"],
    )
    assert result.exit_code == 2
    assert "Unsupported --type" in result.stdout


def test_create_site_invalid_profile_rejected(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--profile", "tiny-box"],
    )
    assert result.exit_code == 2
    assert "Unsupported --profile" in result.stdout


def test_create_site_cache_redis_enables_worker(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "create", "site", "demo.test", "--cache", "redis"],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["profile"]["cache"] == "redis"
    assert lines[0]["runtime"]["worker"] is True


def test_create_site_dry_run_still_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--dry-run",
            "create",
            "site",
            "demo.test",
            "--source",
            str(source),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


def test_create_site_apply_creates_and_deploys(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    metadata = tmp_path / "state" / "apps" / "demo.test.json"
    assert metadata.exists()

    info = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    payload = json.loads(info.stdout.strip())
    assert payload["current_release"] is not None
    assert payload["releases_count"] == 1


def test_site_create_apply_short_flag(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "create",
            "demo.test",
            "-a",
        ],
    )
    assert result.exit_code == 0
    assert "Create site completed for demo.test" in result.stdout


def test_create_site_apply_with_runtime_flags(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--worker",
            "--scheduler",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    worker_status = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", "demo.test"])
    worker_payload = json.loads(worker_status.stdout.strip())
    assert worker_payload["process"]["enabled"] is True

    scheduler_status = runner.invoke(app, ["--config", str(config), "--json", "scheduler", "status", "demo.test"])
    scheduler_payload = json.loads(scheduler_status.stdout.strip())
    assert scheduler_payload["process"]["enabled"] is True


def test_site_mode_disable_apply(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "create",
            "demo.test",
            "-w",
            "-s",
            "-a",
        ],
    )
    assert create.exit_code == 0

    disable = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "runtime",
            "disable",
            "demo.test",
            "-a",
        ],
    )
    assert disable.exit_code == 0

    worker_status = runner.invoke(app, ["--config", str(config), "--json", "worker", "status", "demo.test"])
    worker_payload = json.loads(worker_status.stdout.strip())
    assert worker_payload["process"]["enabled"] is False

    scheduler_status = runner.invoke(app, ["--config", str(config), "--json", "scheduler", "status", "demo.test"])
    scheduler_payload = json.loads(scheduler_status.stdout.strip())
    assert scheduler_payload["process"]["enabled"] is False


def test_site_runtime_status_json(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(app, ["--config", str(config), "site", "create", "demo.test", "-w", "-a"])
    assert create.exit_code == 0

    status = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "site",
            "runtime",
            "status",
            "demo.test",
            "--worker",
        ],
    )
    assert status.exit_code == 0
    lines = [json.loads(line) for line in status.stdout.strip().splitlines()]
    assert lines[-1]["message"] == "Site status for demo.test"
    assert lines[-1]["processes"]["worker"]["enabled"] is True


def test_site_runtime_disable_requires_registered_app(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "runtime",
            "disable",
            "demo.test",
            "-a",
        ],
    )
    assert result.exit_code == 2
    assert "Application is not registered" in result.stdout


def test_site_runtime_enable_scheduler_ignores_worker_options_in_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "runtime",
            "enable",
            "demo.test",
            "--scheduler",
            "--concurrency",
            "0",
        ],
    )
    assert result.exit_code == 0
    assert "Site enable plan prepared for demo.test" in result.stdout


def test_create_site_runtime_requires_deploy(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--no-deploy",
            "--worker",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert "Runtime enable requires --deploy" in result.stdout


def test_create_site_without_worker_ignores_worker_options(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--scheduler",
            "--concurrency",
            "0",
        ],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


def test_create_site_le_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "-le",
        ],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


def test_create_site_le_requires_deploy(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "-le",
            "--no-deploy",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert "Let's Encrypt requires --deploy" in result.stdout


def test_create_site_atomic_rolls_back_on_ssl_failure(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")

    def fake_run_issue(_command: list[str]) -> str:
        raise SslServiceError("certbot failed")

    monkeypatch.setattr("larops.commands.create.run_issue", fake_run_issue)
    monkeypatch.setattr("larops.commands.create.run_delete", lambda _command: "")
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "-le",
            "--atomic",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    metadata = tmp_path / "state" / "apps" / "demo.test.json"
    app_root = tmp_path / "apps" / "demo.test"
    assert not metadata.exists()
    assert not app_root.exists()


def test_create_site_atomic_attempts_ssl_cleanup_on_failure(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    captured: dict[str, object] = {}

    def fake_run_issue(_command: list[str]) -> str:
        raise SslServiceError("certbot failed")

    def fake_build_delete_command(*, domain: str) -> list[str]:
        captured["domain"] = domain
        return ["certbot", "delete", "--cert-name", domain, "--non-interactive"]

    def fake_run_delete(command: list[str]) -> str:
        captured["command"] = command
        return "cleanup ok"

    monkeypatch.setattr("larops.commands.create.run_issue", fake_run_issue)
    monkeypatch.setattr("larops.commands.create.build_delete_command", fake_build_delete_command)
    monkeypatch.setattr("larops.commands.create.run_delete", fake_run_delete)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "-le",
            "--atomic",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert captured["domain"] == "demo.test"
    assert captured["command"] == ["certbot", "delete", "--cert-name", "demo.test", "--non-interactive"]
