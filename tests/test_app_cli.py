import json
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
