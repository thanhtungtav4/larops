import json
from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app

runner = CliRunner()


def linux_env(tmp_path: Path) -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n', encoding="utf-8")
    return {"LAROPS_STACK_OS_RELEASE_PATH": str(os_release)}


def write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                "  keep_releases: 3",
                "  health_check_path: /up",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_bootstrap_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "bootstrap", "init", "--skip-stack"])
    assert result.exit_code == 0
    assert "Bootstrap plan prepared." in result.stdout


def test_bootstrap_small_vps_profile_skips_data_stack_by_default(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "bootstrap", "init", "--profile", "small-vps"],
        env=linux_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[0])
    assert payload["profile"] == "small-vps"
    assert payload["stack_groups"] == ["web", "ops"]
    assert payload["group_defaults"] == {"web": True, "data": False, "postgres": False, "ops": True}


def test_bootstrap_small_vps_profile_allows_explicit_data_override(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "bootstrap", "init", "--profile", "small-vps", "--data"],
        env=linux_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[0])
    assert payload["stack_groups"] == ["web", "data", "ops"]


def test_bootstrap_apply_with_domain_and_skip_stack(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("bootstrap", encoding="utf-8")
    generated_config = tmp_path / "generated-larops.yaml"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "bootstrap",
            "init",
            "--skip-stack",
            "--write-config",
            "--config-path",
            str(generated_config),
            "--domain",
            "demo.test",
            "--source",
            str(source),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert generated_config.exists()

    info = runner.invoke(app, ["--config", str(config), "--json", "app", "info", "demo.test"])
    assert info.exit_code == 0
    payload = json.loads(info.stdout.strip())
    assert payload["releases_count"] == 1


def test_bootstrap_write_config_does_not_materialize_telegram_secrets(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    bot_token_file = secret_dir / "bot-token"
    chat_id_file = secret_dir / "chat-id"
    bot_token_file.write_text("real-bot-token", encoding="utf-8")
    chat_id_file.write_text("123456", encoding="utf-8")

    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                "  keep_releases: 3",
                "  health_check_path: /up",
                "notifications:",
                "  telegram:",
                "    enabled: true",
                f"    bot_token_file: {bot_token_file}",
                f"    chat_id_file: {chat_id_file}",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    generated_config = tmp_path / "generated.yaml"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "bootstrap",
            "init",
            "--skip-stack",
            "--write-config",
            "--config-path",
            str(generated_config),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    rendered = generated_config.read_text(encoding="utf-8")
    assert "real-bot-token" not in rendered
    assert "123456" not in rendered
    assert f"bot_token_file: {bot_token_file}" in rendered
    assert f"chat_id_file: {chat_id_file}" in rendered


def test_bootstrap_small_vps_profile_writes_conservative_runtime_policy(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    generated_config = tmp_path / "generated.yaml"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "bootstrap",
            "init",
            "--profile",
            "small-vps",
            "--skip-stack",
            "--write-config",
            "--config-path",
            str(generated_config),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    rendered = generated_config.read_text(encoding="utf-8")
    assert "runtime_policy:" in rendered
    assert "batch_size: 10" in rendered
    assert "max_restarts: 3" in rendered
    assert "cooldown_seconds: 180" in rendered


def test_bootstrap_small_vps_profile_preserves_custom_runtime_policy_and_batch_size(tmp_path: Path) -> None:
    config_file = tmp_path / "larops.yaml"
    config_file.write_text(
        "\n".join(
            [
                "environment: test",
                f"state_path: {tmp_path / 'state'}",
                "deploy:",
                f"  releases_path: {tmp_path / 'apps'}",
                "  keep_releases: 3",
                "  health_check_path: /up",
                "runtime_policy:",
                "  worker:",
                "    max_restarts: 9",
                "    window_seconds: 111",
                "    cooldown_seconds: 222",
                "    auto_heal: true",
                "notifications:",
                "  telegram:",
                "    batch_size: 7",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    generated_config = tmp_path / "generated-preserved.yaml"

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "bootstrap",
            "init",
            "--profile",
            "small-vps",
            "--skip-stack",
            "--write-config",
            "--config-path",
            str(generated_config),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    rendered = generated_config.read_text(encoding="utf-8")
    assert "batch_size: 7" in rendered
    assert "max_restarts: 9" in rendered
    assert "window_seconds: 111" in rendered
    assert "cooldown_seconds: 222" in rendered
    assert "scheduler:" in rendered
    assert "horizon:" in rendered
