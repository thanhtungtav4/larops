from pathlib import Path
from subprocess import CompletedProcess

from larops.config import DeployConfig
from larops.services.app_lifecycle import get_app_paths, initialize_app
from larops.services.release_service import (
    build_deploy_phase_commands,
    prepare_release_candidate,
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
    assert commands["build"][0] == "COMPOSER_ALLOW_SUPERUSER=1 /usr/bin/composer install --no-interaction --no-progress --no-scripts --no-dev --optimize-autoloader"
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
    assert commands == ["COMPOSER_ALLOW_SUPERUSER=1 composer install --no-interaction --no-progress --no-scripts --no-dev --optimize-autoloader"]


def test_resolve_build_commands_for_release_auto_adds_vite_build_with_package_lock(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "public" / "build").mkdir(parents=True, exist_ok=True)
    (release_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (release_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (release_dir / "vite.config.js").write_text("export default {}", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == ["npm ci --no-audit --no-fund", "npm run build"]


def test_resolve_build_commands_for_release_skips_vite_auto_build_when_manifest_exists(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "public" / "build").mkdir(parents=True, exist_ok=True)
    (release_dir / "public" / "build" / "manifest.json").write_text("{}", encoding="utf-8")
    (release_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (release_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (release_dir / "vite.config.js").write_text("export default {}", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == []


def test_resolve_build_commands_for_release_skips_vite_auto_build_when_explicit_build_exists(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "public" / "build").mkdir(parents=True, exist_ok=True)
    (release_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (release_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (release_dir / "vite.config.js").write_text("export default {}", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(
        config=config,
        release_dir=release_dir,
        commands=["npm run build", "echo done"],
    )
    assert commands == ["npm run build", "echo done"]


def test_resolve_build_commands_for_release_skips_when_vendor_exists(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "vendor").mkdir(parents=True, exist_ok=True)
    (release_dir / "composer.json").write_text("{}", encoding="utf-8")
    (release_dir / "vendor" / "autoload.php").write_text("<?php", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == []


def test_resolve_build_commands_for_release_combines_composer_and_vite_steps(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    (release_dir / "public" / "build").mkdir(parents=True, exist_ok=True)
    (release_dir / "composer.json").write_text("{}", encoding="utf-8")
    (release_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (release_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (release_dir / "vite.config.js").write_text("export default {}", encoding="utf-8")
    config = DeployConfig()

    commands = resolve_build_commands_for_release(config=config, release_dir=release_dir, commands=[])
    assert commands == [
        "COMPOSER_ALLOW_SUPERUSER=1 composer install --no-interaction --no-progress --no-scripts --no-dev --optimize-autoloader",
        "npm ci --no-audit --no-fund",
        "npm run build",
    ]


def test_prepare_release_candidate_bootstraps_laravel_runtime_directories(tmp_path: Path) -> None:
    state_path = tmp_path / "state"
    paths = get_app_paths(tmp_path / "apps", state_path, "demo.test")
    initialize_app(paths, {"domain": "demo.test"})

    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    (source / ".env").write_text("APP_ENV=production\n", encoding="utf-8")

    release_id, release_dir = prepare_release_candidate(
        paths=paths,
        source_path=source,
        ref="main",
        shared_dirs=["storage", "bootstrap/cache"],
        shared_files=[".env"],
    )

    assert release_id
    assert (release_dir / "storage").is_symlink()
    assert (release_dir / "bootstrap" / "cache").is_symlink()
    assert (release_dir / "storage" / "framework" / "cache" / "data").is_dir()
    assert (release_dir / "storage" / "framework" / "sessions").is_dir()
    assert (release_dir / "storage" / "framework" / "views").is_dir()
    assert (release_dir / "storage" / "logs").is_dir()
    assert (release_dir / "storage" / "app" / "public").is_dir()
    assert (paths.shared / "storage" / "framework" / "views").is_dir()
    assert (paths.shared / "bootstrap" / "cache").is_dir()


def test_prepare_release_candidate_clears_stale_laravel_bootstrap_cache_files(tmp_path: Path) -> None:
    state_path = tmp_path / "state"
    paths = get_app_paths(tmp_path / "apps", state_path, "demo.test")
    initialize_app(paths, {"domain": "demo.test"})
    stale_cache_dir = paths.shared / "bootstrap" / "cache"
    stale_cache_dir.mkdir(parents=True, exist_ok=True)
    (stale_cache_dir / "config.php").write_text("<?php return ['db' => 'localhost'];", encoding="utf-8")
    (stale_cache_dir / "packages.php").write_text("<?php return [];", encoding="utf-8")
    (stale_cache_dir / ".gitignore").write_text("*\n", encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    (source / ".env").write_text("APP_ENV=production\n", encoding="utf-8")

    _, release_dir = prepare_release_candidate(
        paths=paths,
        source_path=source,
        ref="main",
        shared_dirs=["storage", "bootstrap/cache"],
        shared_files=[".env"],
    )

    cache_dir = release_dir / "bootstrap" / "cache"
    assert cache_dir.is_dir()
    assert not (cache_dir / "config.php").exists()
    assert not (cache_dir / "packages.php").exists()
    assert (cache_dir / ".gitignore").exists()
