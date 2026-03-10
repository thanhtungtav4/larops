import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app
from larops.services.release_service import ReleaseServiceError

runner = CliRunner()


def write_config(tmp_path: Path, keep_releases: int = 5) -> Path:
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                f"  keep_releases: {keep_releases}",
                "  health_check_path: /up",
                "  shared_dirs:",
                "    - storage",
                "    - bootstrap/cache",
                "  shared_files:",
                "    - .env",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def make_source(tmp_path: Path, name: str, content: str) -> Path:
    source = tmp_path / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text(content, encoding="utf-8")
    (source / ".env").write_text(f"APP_NAME={content}\n", encoding="utf-8")
    (source / "storage").mkdir(parents=True, exist_ok=True)
    (source / "bootstrap" / "cache").mkdir(parents=True, exist_ok=True)
    return source


def make_laravel_source(tmp_path: Path, name: str, content: str) -> Path:
    source = make_source(tmp_path, name, content)
    (source / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    return source


def test_app_create_apply_creates_structure(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert result.exit_code == 0

    app_root = tmp_path / "apps" / "demo.test"
    assert (app_root / "releases").exists()
    assert (app_root / "shared").exists()
    metadata = tmp_path / "state" / "apps" / "demo.test.json"
    assert metadata.exists()


def test_app_deploy_and_rollback_cycle(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source_one = make_source(tmp_path, "src-one", "release-one")
    source_two = make_source(tmp_path, "src-two", "release-two")

    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    deploy_one = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source_one), "--apply"],
    )
    assert deploy_one.exit_code == 0

    deploy_two = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source_two), "--apply"],
    )
    assert deploy_two.exit_code == 0
    current_manifest = tmp_path / "apps" / "demo.test" / "current" / ".larops-deploy-manifest.json"
    assert current_manifest.exists()

    info_before = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    assert info_before.exit_code == 0
    info_payload = json.loads(info_before.stdout.strip())
    current_release = info_payload["current_release"]
    assert current_release is not None
    assert info_payload["releases_count"] == 2

    rollback = runner.invoke(
        app,
        ["--config", str(config), "app", "rollback", "demo.test", "--to", "previous", "--apply"],
    )
    assert rollback.exit_code == 0

    info_after = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    assert info_after.exit_code == 0
    info_after_payload = json.loads(info_after.stdout.strip())
    assert info_after_payload["current_release"] != current_release


def test_app_info_non_json_prints_operator_summary(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "src-one", "release-one")

    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    metadata_path = tmp_path / "state" / "apps" / "demo.test.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["profile"] = {
        "preset": "small-vps",
        "type": "laravel",
        "cache": "fastcgi",
        "runtime": {"worker": False, "scheduler": True, "horizon": False},
    }
    payload["database_provision"] = {
        "database": "demo_test",
        "user": "demo_test",
        "host": "127.0.0.1",
        "port": 3306,
        "credential_file": "/tmp/demo.cnf",
        "password_file": "/tmp/demo.txt",
    }
    payload["env_sync"] = {
        "env_file": "/var/www/demo.test/shared/.env",
        "updated_keys": ["DB_CONNECTION", "DB_HOST"],
    }
    payload["last_bootstrap"] = {
        "bootstrapped_at": "2026-03-10T10:00:00+00:00",
        "status": "completed",
    }
    payload["last_deploy"]["smoke_checks"] = {
        "http": {"status": "ok", "http_status": 301},
        "https": {"status": "ok", "http_status": 200},
    }
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    monkeypatch.setattr("larops.commands.app.default_cert_file", lambda _domain: tmp_path / "fullchain.pem")

    info = runner.invoke(app, ["--config", str(config), "app", "info", "demo.test"])
    assert info.exit_code == 0
    assert "Application info: demo.test" in info.stdout
    assert "site: http://demo.test" in info.stdout
    assert "release:" in info.stdout
    assert "profile: small-vps / laravel / cache=fastcgi" in info.stdout
    assert "db: mysql demo_test as demo_test" in info.stdout
    assert "paths: current=" in info.stdout
    assert "env=/var/www/demo.test/shared/.env" in info.stdout
    assert "web: nginx=" in info.stdout
    assert "cert=False" in info.stdout
    assert "bootstrap: completed at 2026-03-10T10:00:00+00:00" in info.stdout
    assert "smoke http: 301" in info.stdout
    assert "smoke https: 200" in info.stdout


def test_app_bootstrap_runs_expected_commands_and_syncs_env(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = make_laravel_source(tmp_path, "src-one", "release-one")

    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    password_file = tmp_path / "state" / "secrets" / "db" / "demo.test.txt"
    password_file.parent.mkdir(parents=True, exist_ok=True)
    password_file.write_text("secret-pass\n", encoding="utf-8")

    metadata_path = tmp_path / "state" / "apps" / "demo.test.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["database_provision"] = {
        "engine": "mysql",
        "database": "demo_test",
        "user": "demo_test",
        "host": "127.0.0.1",
        "port": 3306,
        "credential_file": str(tmp_path / "state" / "secrets" / "db" / "demo.test.cnf"),
        "password_file": str(password_file),
    }
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.app.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "app",
            "bootstrap",
            "demo.test",
            "--seed",
            "--seeder-class",
            "DemoSeeder",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert "App bootstrap completed for demo.test" in result.stdout
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    assert bootstrap[0] == "php artisan migrate --force"
    assert bootstrap[1] == "php artisan package:discover --ansi"
    assert "php artisan db:seed --force --class=DemoSeeder" in bootstrap
    assert bootstrap[-2:] == ["php artisan optimize:clear", "php artisan optimize"]

    env_file = tmp_path / "apps" / "demo.test" / "shared" / ".env"
    env_body = env_file.read_text(encoding="utf-8")
    assert "DB_CONNECTION=mysql" in env_body
    assert "DB_HOST=127.0.0.1" in env_body
    assert "DB_PASSWORD=secret-pass" in env_body

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["last_bootstrap"]["status"] == "completed"
    assert metadata["last_bootstrap"]["commands"][2] == "php artisan db:seed --force --class=DemoSeeder"


def test_app_bootstrap_skip_flags_reduce_commands(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = make_laravel_source(tmp_path, "src-one", "release-one")

    assert runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"]).exit_code == 0
    assert runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    ).exit_code == 0

    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.app.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "app",
            "bootstrap",
            "demo.test",
            "--skip-migrate",
            "--skip-package-discover",
            "--skip-optimize",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    assert bootstrap == []


def test_app_refresh_runs_git_pull_then_deploy_and_bootstrap(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "src-refresh", "release-refresh")
    (source / ".git").mkdir()

    assert runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"]).exit_code == 0

    metadata_path = tmp_path / "state" / "apps" / "demo.test.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["last_deploy"] = {"source": str(source), "ref": "main"}
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    git_calls: list[list[str]] = []
    deploy_calls: list[dict] = []
    bootstrap_calls: list[dict] = []

    def fake_run_command(command: list[str], check: bool = False, timeout_seconds=None):
        git_calls.append(list(command))
        if command[-2:] == ["rev-parse", "HEAD"]:
            stdout = "oldsha\n" if len([c for c in git_calls if c[-2:] == ["rev-parse", "HEAD"]]) == 1 else "newsha\n"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[:3] == ["git", "-C", str(source)] and "pull" in command:
            return subprocess.CompletedProcess(command, 0, stdout="Updated\n", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    def fake_deploy(ctx, *, domain, ref, source, apply):
        deploy_calls.append({"domain": domain, "ref": ref, "source": str(source), "apply": apply})

    def fake_bootstrap(
        ctx,
        *,
        domain,
        seed,
        seeder_class,
        skip_migrate,
        skip_package_discover,
        skip_optimize,
        apply,
    ):
        bootstrap_calls.append(
            {
                "domain": domain,
                "seed": seed,
                "seeder_class": seeder_class,
                "skip_migrate": skip_migrate,
                "skip_package_discover": skip_package_discover,
                "skip_optimize": skip_optimize,
                "apply": apply,
            }
        )

    monkeypatch.setattr("larops.commands.app.run_command", fake_run_command)
    monkeypatch.setattr("larops.commands.app.deploy", fake_deploy)
    monkeypatch.setattr("larops.commands.app.bootstrap", fake_bootstrap)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "app",
            "refresh",
            "demo.test",
            "--seed",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert "Refresh completed for demo.test" in result.stdout
    assert git_calls[0][-2:] == ["rev-parse", "HEAD"]
    assert git_calls[1][0:3] == ["git", "-C", str(source)]
    assert git_calls[1][3:] == ["pull", "--ff-only", "origin", "main"]
    assert git_calls[2][-2:] == ["rev-parse", "HEAD"]
    assert deploy_calls == [{"domain": "demo.test", "ref": "main", "source": str(source), "apply": True}]
    assert bootstrap_calls == [
        {
            "domain": "demo.test",
            "seed": True,
            "seeder_class": None,
            "skip_migrate": False,
            "skip_package_discover": False,
            "skip_optimize": False,
            "apply": True,
        }
    ]


def test_deploy_prunes_old_releases(tmp_path: Path) -> None:
    config = write_config(tmp_path, keep_releases=2)
    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    for index in range(3):
        source = make_source(tmp_path, f"src-{index}", f"release-{index}")
        deploy = runner.invoke(
            app,
            ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
        )
        assert deploy.exit_code == 0

    info = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    payload = json.loads(info.stdout.strip())
    assert payload["releases_count"] == 2


def test_deploy_uses_shared_env_and_storage(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source_one = make_source(tmp_path, "src-one", "release-one")
    source_two = make_source(tmp_path, "src-two", "release-two")

    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    deploy_one = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source_one), "--apply"],
    )
    assert deploy_one.exit_code == 0

    current = tmp_path / "apps" / "demo.test" / "current"
    shared = tmp_path / "apps" / "demo.test" / "shared"
    assert (current / ".env").is_symlink()
    assert (current / "storage").is_symlink()
    assert (shared / ".env").read_text(encoding="utf-8") == "APP_NAME=release-one\n"

    (shared / "storage" / "user-upload.txt").write_text("persist", encoding="utf-8")
    deploy_two = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source_two), "--apply"],
    )
    assert deploy_two.exit_code == 0
    assert (shared / ".env").read_text(encoding="utf-8") == "APP_NAME=release-one\n"
    assert (current / "storage" / "user-upload.txt").read_text(encoding="utf-8") == "persist"


def test_deploy_records_writable_permissions_metadata(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "src-one", "release-one")

    create = runner.invoke(app, ["--config", str(config), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    deploy = runner.invoke(
        app,
        ["--config", str(config), "app", "deploy", "demo.test", "--source", str(source), "--apply"],
    )
    assert deploy.exit_code == 0

    metadata = json.loads((tmp_path / "state" / "apps" / "demo.test.json").read_text(encoding="utf-8"))
    assert metadata["last_deploy"]["permissions"]["writable_mode"] == "0o775"


def test_deploy_rolls_back_on_health_check_failure(tmp_path: Path, monkeypatch) -> None:
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
                "  health_check_enabled: true",
                "  rollback_on_health_check_failure: true",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    source_one = make_source(tmp_path, "src-one", "release-one")
    source_two = make_source(tmp_path, "src-two", "release-two")
    health_results = iter(
        [
            {"enabled": True, "checked": True, "status": "ok", "http_status": 200},
            {"enabled": True, "checked": True, "status": "failed", "http_status": 500, "detail": "boom"},
        ]
    )

    monkeypatch.setattr("larops.commands.app.run_http_health_check", lambda **_: next(health_results))
    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0
    deploy_one = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source_one), "--apply"],
    )
    assert deploy_one.exit_code == 0

    info_before = runner.invoke(app, ["--config", str(config_file), "--json", "app", "info", "demo.test"])
    current_before = json.loads(info_before.stdout.strip())["current_release"]

    deploy_two = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source_two), "--apply"],
    )
    assert deploy_two.exit_code == 2

    info_after = runner.invoke(app, ["--config", str(config_file), "--json", "app", "info", "demo.test"])
    current_after = json.loads(info_after.stdout.strip())["current_release"]
    assert current_after == current_before


def test_deploy_rolls_back_on_verify_failure(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                "  keep_releases: 5",
                "  verify_commands:",
                "    - test -f README.txt",
                "  rollback_on_verify_failure: true",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    source_one = make_source(tmp_path, "src-one", "release-one")
    source_two = make_source(tmp_path, "src-two", "release-two")
    real_run_release_commands = __import__("larops.commands.app", fromlist=["run_release_commands"]).run_release_commands
    verify_calls = {"count": 0}

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None) -> list[dict]:
        if phase == "verify":
            verify_calls["count"] += 1
            if verify_calls["count"] == 2:
                raise ReleaseServiceError("verify failed")
        return real_run_release_commands(
            workdir=workdir,
            phase=phase,
            commands=commands,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr("larops.commands.app.run_release_commands", fake_run_release_commands)
    create = runner.invoke(app, ["--config", str(config_file), "app", "create", "demo.test", "--apply"])
    assert create.exit_code == 0

    deploy_one = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source_one), "--apply"],
    )
    assert deploy_one.exit_code == 0

    info_before = runner.invoke(app, ["--config", str(config_file), "--json", "app", "info", "demo.test"])
    current_before = json.loads(info_before.stdout.strip())["current_release"]

    deploy_two = runner.invoke(
        app,
        ["--config", str(config_file), "app", "deploy", "demo.test", "--source", str(source_two), "--apply"],
    )
    assert deploy_two.exit_code == 2

    info_after = runner.invoke(app, ["--config", str(config_file), "--json", "app", "info", "demo.test"])
    current_after = json.loads(info_after.stdout.strip())["current_release"]
    assert current_after == current_before
