import json
from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from larops.cli import app
from larops.commands.create import _resolve_app_bootstrap_strategy
from larops.services.nginx_site_service import NginxSiteServiceError
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
    (source / ".env").write_text("APP_NAME=Demo\nAPP_ENV=local\n", encoding="utf-8")
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
    assert lines[0]["source_prepare"]["mode"] == "laravel-init"


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


def test_create_site_plan_mode_uses_git_clone_when_git_url_is_provided(tmp_path: Path) -> None:
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
            "--git-url",
            "https://github.com/example/demo.git",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["source_prepare"]["mode"] == "git-clone"
    assert lines[0]["source_prepare"]["git_url"] == "https://github.com/example/demo.git"


def test_create_site_apply_small_vps_bootstraps_laravel_source_when_missing(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command[:3] == ["composer", "create-project", "laravel/laravel"]:
            target = Path(command[3])
            target.mkdir(parents=True, exist_ok=True)
            (target / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
            return CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.commands.create.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--profile",
            "small-vps",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    source = tmp_path / "sources" / "demo.test"
    assert (source / "artisan").exists()


def test_create_site_apply_clones_git_source_when_requested(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command[:4] == ["git", "clone", "--branch", "main"]:
            target = Path(command[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
            return CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.commands.create.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--git-url",
            "https://github.com/example/demo.git",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    source = tmp_path / "sources" / "demo.test"
    assert (source / "artisan").exists()


def test_create_site_apply_bootstraps_laravel_artisan_after_deploy(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--app-bootstrap-mode", "eager", "--apply"],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"]
    assert len(bootstrap) == 1
    assert bootstrap[0][0] == "php artisan migrate --force"
    assert bootstrap[0][1] == "php artisan package:discover --ansi"
    assert "php artisan optimize" in bootstrap[0]
    assert "APP_KEY=" in (tmp_path / "apps" / "demo.test" / "shared" / ".env").read_text(encoding="utf-8")
    assert "app bootstrap: completed" in result.stdout


def test_create_site_apply_skips_key_generate_when_app_key_exists(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = make_source(tmp_path, "demo.test")
    (source / ".env").write_text("APP_NAME=Demo\nAPP_KEY=base64:existing-key\n", encoding="utf-8")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--app-bootstrap-mode", "eager", "--apply"],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    assert "php artisan key:generate --force" not in bootstrap
    assert bootstrap[0] == "php artisan migrate --force"
    assert bootstrap[1] == "php artisan package:discover --ansi"


def test_create_site_apply_runs_bootstrap_before_post_activate_when_cache_warm_enabled(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  health_check_path: /up\n",
            "  health_check_path: /up\n  cache_warm_enabled: true\n",
        ),
        encoding="utf-8",
    )
    _ = make_source(tmp_path, "demo.test")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--app-bootstrap-mode", "eager", "--apply"],
    )
    assert result.exit_code == 0
    phases = [phase for phase, _commands in phase_calls]
    assert phases.index("app-bootstrap") < phases.index("post-activate")
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    post_activate = [commands for phase, commands in phase_calls if phase == "post-activate"][0]
    assert "php artisan migrate --force" in bootstrap
    assert "php artisan optimize" in bootstrap
    assert "php artisan config:cache" not in post_activate
    assert "php artisan route:cache" not in post_activate
    assert "php artisan view:cache" not in post_activate
    assert "php artisan event:cache" not in post_activate
    assert post_activate == []


def test_create_site_apply_dedupes_framework_post_activate_work_but_keeps_custom_commands(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  health_check_path: /up\n",
            "\n".join(
                [
                    "  health_check_path: /up",
                    "  migrate_enabled: true",
                    "  cache_warm_enabled: true",
                    "  post_activate_commands:",
                    "    - echo custom-post",
                    "",
                ]
            ),
        ),
        encoding="utf-8",
    )
    _ = make_source(tmp_path, "demo.test")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--app-bootstrap-mode", "eager", "--apply"],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    post_activate = [commands for phase, commands in phase_calls if phase == "post-activate"][0]
    assert bootstrap.count("php artisan migrate --force") == 1
    assert "php artisan migrate --force" not in post_activate
    assert "php artisan config:cache" not in post_activate
    assert "echo custom-post" in post_activate


def test_resolve_app_bootstrap_strategy_auto_skips_when_database_is_empty(monkeypatch, tmp_path: Path) -> None:
    current_path = tmp_path / "current"
    current_path.mkdir(parents=True, exist_ok=True)
    (current_path / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    credential_file = tmp_path / "db.cnf"
    credential_file.write_text("[client]\n", encoding="utf-8")
    monkeypatch.setattr("larops.commands.create.count_database_tables", lambda **_kwargs: 0)

    strategy = _resolve_app_bootstrap_strategy(
        requested_mode="auto",
        current_path=current_path,
        database_provision={
            "engine": "mysql",
            "database": "demo",
            "credential_file": str(credential_file),
        },
    )

    assert strategy["mode"] == "skip"
    assert strategy["reason"] == "database-empty"
    assert strategy["table_count"] == 0


def test_resolve_app_bootstrap_strategy_auto_skips_without_database_context(tmp_path: Path) -> None:
    current_path = tmp_path / "current"
    current_path.mkdir(parents=True, exist_ok=True)
    (current_path / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")

    strategy = _resolve_app_bootstrap_strategy(
        requested_mode="auto",
        current_path=current_path,
        database_provision=None,
    )

    assert strategy["mode"] == "skip"
    assert strategy["reason"] == "no-db-context"
    assert strategy["table_count"] is None


def test_create_site_apply_auto_mode_skips_bootstrap_without_database_context(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--apply"],
    )

    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    assert bootstrap == []
    assert "APP_KEY=" in (tmp_path / "apps" / "demo.test" / "shared" / ".env").read_text(encoding="utf-8")
    assert "app bootstrap: skipped (no-db-context)" in result.stdout


def test_resolve_app_bootstrap_strategy_auto_eager_when_database_has_tables(monkeypatch, tmp_path: Path) -> None:
    current_path = tmp_path / "current"
    current_path.mkdir(parents=True, exist_ok=True)
    (current_path / "artisan").write_text("<?php echo 'ok';", encoding="utf-8")
    credential_file = tmp_path / "db.cnf"
    credential_file.write_text("[client]\n", encoding="utf-8")
    monkeypatch.setattr("larops.commands.create.count_database_tables", lambda **_kwargs: 4)

    strategy = _resolve_app_bootstrap_strategy(
        requested_mode="auto",
        current_path=current_path,
        database_provision={
            "engine": "mysql",
            "database": "demo",
            "credential_file": str(credential_file),
        },
    )

    assert strategy["mode"] == "eager"
    assert strategy["reason"] == "database-has-tables"
    assert strategy["table_count"] == 4


def test_create_site_apply_skip_bootstrap_mode_avoids_framework_boot_commands(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  health_check_path: /up\n",
            "\n".join(
                [
                    "  health_check_path: /up",
                    "  migrate_enabled: true",
                    "  cache_warm_enabled: true",
                    "  post_activate_commands:",
                    "    - echo custom-post",
                    "",
                ]
            ),
        ),
        encoding="utf-8",
    )
    _ = make_source(tmp_path, "demo.test")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--app-bootstrap-mode",
            "skip",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    post_activate = [commands for phase, commands in phase_calls if phase == "post-activate"][0]
    assert bootstrap == []
    assert post_activate == ["echo custom-post"]
    assert "app bootstrap: skipped (explicit-skip)" in result.stdout


def test_create_site_apply_skips_app_bootstrap_when_artisan_is_missing(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    source = tmp_path / "sources" / "demo.test"
    source.mkdir(parents=True, exist_ok=True)
    (source / ".env").write_text("APP_NAME=Demo\n", encoding="utf-8")
    phase_calls: list[tuple[str, list[str]]] = []

    def fake_run_release_commands(*, workdir: Path, phase: str, commands: list[str], timeout_seconds: int | None):
        phase_calls.append((phase, list(commands)))
        return [{"phase": phase, "command": command} for command in commands]

    monkeypatch.setattr("larops.commands.create.run_release_commands", fake_run_release_commands)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--apply"],
    )
    assert result.exit_code == 0
    bootstrap = [commands for phase, commands in phase_calls if phase == "app-bootstrap"][0]
    assert bootstrap == []
    assert "app bootstrap: completed" not in result.stdout


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


def test_create_site_apply_provisions_nginx_by_default_when_deploying(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    captured: list[dict[str, object]] = []

    def fake_apply_nginx_site_config(**kwargs):
        captured.append(kwargs)
        return {"managed": True, **kwargs}

    monkeypatch.setattr("larops.commands.create.apply_nginx_site_config", fake_apply_nginx_site_config)

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--apply"],
    )
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0]["domain"] == "demo.test"
    assert captured[0]["https_enabled"] is False


def test_create_site_apply_with_letsencrypt_rewrites_nginx_to_https(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    captured: list[dict[str, object]] = []

    def fake_apply_nginx_site_config(**kwargs):
        captured.append(kwargs)
        return {"managed": True, **kwargs}

    monkeypatch.setattr("larops.commands.create.apply_nginx_site_config", fake_apply_nginx_site_config)
    monkeypatch.setattr("larops.commands.create.run_issue", lambda _command: "ok")

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "-le", "--apply"],
    )
    assert result.exit_code == 0
    assert [item["https_enabled"] for item in captured] == [False, True]


def test_create_site_apply_uses_existing_certificate_for_https_vhost(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    cert_dir = tmp_path / "letsencrypt" / "demo.test"
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / "fullchain.pem").write_text("cert", encoding="utf-8")
    (cert_dir / "privkey.pem").write_text("key", encoding="utf-8")
    captured: list[dict[str, object]] = []

    def fake_apply_nginx_site_config(**kwargs):
        captured.append(kwargs)
        return {"managed": True, **kwargs}

    monkeypatch.setattr("larops.commands.create.apply_nginx_site_config", fake_apply_nginx_site_config)
    monkeypatch.setattr(
        "larops.commands.create.default_cert_file",
        lambda _domain: cert_dir / "fullchain.pem",
    )

    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--force", "--apply"],
    )
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0]["https_enabled"] is True


def test_create_site_plan_with_db_exposes_db_provision_plan(tmp_path: Path) -> None:
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
            "--with-db",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    provision = lines[0]["db_provision"]
    assert provision["engine"] == "mysql"
    assert provision["database"] == "demo_test"
    assert provision["user"] == "demo_test"
    assert provision["password_source"] == "generated"


def test_create_site_apply_with_db_provisions_database_and_persists_metadata(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    captured: dict[str, object] = {}

    def fake_provision_database(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "domain": "demo.test",
            "engine": kwargs["engine"],
            "database": kwargs["database"],
            "user": kwargs["user"],
            "host": kwargs["app_host"],
            "port": kwargs["app_port"],
            "credential_file": str(kwargs["credential_file"]),
            "password_file": str(kwargs["password_file"]),
            "admin_credential_file": None,
            "provisioned_at": "2026-03-09T00:00:00+00:00",
        }

    monkeypatch.setattr("larops.commands.create.provision_database", fake_provision_database)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--with-db",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert captured["engine"] == "mysql"
    metadata = json.loads((tmp_path / "state" / "apps" / "demo.test.json").read_text(encoding="utf-8"))
    assert metadata["database_provision"]["database"] == "demo_test"
    assert metadata["env_sync"]["env_file"].endswith("/shared/.env")
    shared_env = tmp_path / "apps" / "demo.test" / "shared" / ".env"
    env_body = shared_env.read_text(encoding="utf-8")
    assert "APP_NAME=Demo" in env_body
    assert "DB_CONNECTION=mysql" in env_body
    assert "DB_DATABASE=demo_test" in env_body
    assert "DB_USERNAME=demo_test" in env_body
    assert "db credential file:" in result.stdout
    assert "env file:" in result.stdout


def test_create_site_atomic_with_db_rolls_back_database_on_follow_up_failure(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    deprovision_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "larops.commands.create.provision_database",
        lambda **kwargs: {
            "status": "ok",
            "domain": "demo.test",
            "engine": kwargs["engine"],
            "database": kwargs["database"],
            "user": kwargs["user"],
            "host": kwargs["app_host"],
            "port": kwargs["app_port"],
            "credential_file": str(kwargs["credential_file"]),
            "password_file": str(kwargs["password_file"]),
            "admin_credential_file": None,
            "provisioned_at": "2026-03-09T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "larops.commands.create.apply_nginx_site_config",
        lambda **_kwargs: (_ for _ in ()).throw(NginxSiteServiceError("nginx failed")),
    )

    def fake_deprovision_database(**kwargs):
        deprovision_calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr("larops.commands.create.deprovision_database", fake_deprovision_database)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--with-db",
            "--atomic",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert len(deprovision_calls) == 1
    assert deprovision_calls[0]["database"] == "demo_test"


def test_create_site_force_reuses_provisioned_db_metadata_and_resyncs_env(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    state_path = tmp_path / "state"
    apps_root = tmp_path / "apps" / "demo.test"
    password_file = state_path / "secrets" / "db" / "demo.test.txt"
    password_file.parent.mkdir(parents=True, exist_ok=True)
    password_file.write_text("secret-123\n", encoding="utf-8")
    metadata_path = state_path / "apps" / "demo.test.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "domain": "demo.test",
                "php": "8.4",
                "db": "mysql",
                "ssl": True,
                "database_provision": {
                    "engine": "mysql",
                    "database": "demo_test",
                    "user": "demo_test",
                    "host": "127.0.0.1",
                    "port": 3306,
                    "credential_file": str(state_path / "secrets" / "db" / "demo.test.cnf"),
                    "password_file": str(password_file),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (apps_root / "shared").mkdir(parents=True, exist_ok=True)
    (apps_root / "shared" / ".env").write_text("APP_NAME=Demo\nDB_HOST=localhost\n", encoding="utf-8")

    monkeypatch.setattr("larops.commands.create.run_release_commands", lambda **_kwargs: [])
    monkeypatch.setattr(
        "larops.commands.create.run_http_health_check",
        lambda **_kwargs: {"enabled": False, "checked": False, "status": "skipped"},
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--force",
            "--apply",
        ],
    )

    assert result.exit_code == 0
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["database_provision"]["host"] == "127.0.0.1"
    assert metadata["env_sync"]["env_file"].endswith("/shared/.env")
    env_body = (apps_root / "shared" / ".env").read_text(encoding="utf-8")
    assert "DB_HOST=127.0.0.1" in env_body
    assert "DB_PASSWORD=secret-123" in env_body


def test_create_site_force_recovers_db_env_from_secret_files_when_metadata_was_lost(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    state_path = tmp_path / "state"
    apps_root = tmp_path / "apps" / "demo.test"
    secret_dir = state_path / "secrets" / "db"
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "demo.test.txt").write_text("secret-456\n", encoding="utf-8")
    (secret_dir / "demo.test.cnf").write_text(
        "[client]\nuser=demo_test\npassword=secret-456\nhost=127.0.0.1\nport=3306\n",
        encoding="utf-8",
    )
    metadata_path = state_path / "apps" / "demo.test.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "domain": "demo.test",
                "php": "8.4",
                "db": "mysql",
                "ssl": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (apps_root / "shared").mkdir(parents=True, exist_ok=True)
    (apps_root / "shared" / ".env").write_text("APP_NAME=Demo\nDB_HOST=localhost\n", encoding="utf-8")

    monkeypatch.setattr("larops.commands.create.run_release_commands", lambda **_kwargs: [])
    monkeypatch.setattr(
        "larops.commands.create.run_http_health_check",
        lambda **_kwargs: {"enabled": False, "checked": False, "status": "skipped"},
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "create",
            "site",
            "demo.test",
            "--force",
            "--apply",
        ],
    )

    assert result.exit_code == 0
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["database_provision"]["host"] == "127.0.0.1"
    assert metadata["database_provision"]["user"] == "demo_test"
    env_body = (apps_root / "shared" / ".env").read_text(encoding="utf-8")
    assert "DB_HOST=127.0.0.1" in env_body
    assert "DB_USERNAME=demo_test" in env_body
    assert "DB_PASSWORD=secret-456" in env_body


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


def test_create_site_no_deploy_does_not_require_explicit_no_nginx(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "create", "site", "demo.test", "--no-deploy"],
    )
    assert result.exit_code == 0
    assert "Create site plan prepared for demo.test" in result.stdout


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
