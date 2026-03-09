from pathlib import Path
from subprocess import CompletedProcess

from larops.config import DeployConfig
from larops.services.release_service import (
    build_deploy_phase_commands,
    build_rollback_phase_commands,
    resolve_build_commands_for_release,
    run_release_commands,
)


def test_build_deploy_phase_commands_adds_first_class_laravel_steps() -> None:
    config = DeployConfig(
        composer_install=True,
        composer_binary="/usr/bin/composer",
        asset_commands=["npm ci", "npm run build"],
        migrate_enabled=True,
        migrate_phase="post-activate",
        cache_warm_enabled=True,
        verify_commands=["php artisan about"],
        pre_activate_commands=["echo pre"],
        post_activate_commands=["echo post"],
    )

    commands = build_deploy_phase_commands(config)
    assert commands["build"][0] == "COMPOSER_ALLOW_SUPERUSER=1 /usr/bin/composer install --no-dev --optimize-autoloader"
    assert commands["build"][1:] == ["npm ci", "npm run build"]
    assert commands["pre_activate"] == ["echo pre"]
    assert commands["post_activate"][0:2] == ["echo post", "php artisan migrate --force"]
    assert "php artisan config:cache" in commands["post_activate"]
    assert commands["verify"] == ["php artisan about"]


def test_build_rollback_phase_commands_excludes_migrations() -> None:
    config = DeployConfig(
        migrate_enabled=True,
        migrate_phase="post-activate",
        cache_warm_enabled=True,
        verify_commands=["php artisan about"],
        post_activate_commands=["echo rollback"],
    )

    commands = build_rollback_phase_commands(config)
    assert commands["post_activate"][0] == "echo rollback"
    assert "php artisan migrate --force" not in commands["post_activate"]
    assert "php artisan config:cache" in commands["post_activate"]
    assert commands["verify"] == ["php artisan about"]


def test_run_release_commands_passes_timeout(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "release"
    workdir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[list[str], int | None]] = []

    def fake_run_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> CompletedProcess[str]:
        calls.append((command, timeout_seconds))
        return CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("larops.services.release_service.run_command", fake_run_command)
    reports = run_release_commands(
        workdir=workdir,
        phase="build",
        commands=["echo ready"],
        timeout_seconds=123,
    )

    assert reports[0]["phase"] == "build"
    assert reports[0]["stdout"] == "ok"
    assert calls[0][1] == 123


def test_resolve_build_commands_for_release_auto_adds_composer_install(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "composer.json").write_text("{}", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == ["COMPOSER_ALLOW_SUPERUSER=1 composer install --no-dev --optimize-autoloader"]


def test_resolve_build_commands_for_release_skips_when_vendor_exists(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "vendor").mkdir(parents=True, exist_ok=True)
    (release_dir / "composer.json").write_text("{}", encoding="utf-8")
    (release_dir / "vendor" / "autoload.php").write_text("<?php", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == []
