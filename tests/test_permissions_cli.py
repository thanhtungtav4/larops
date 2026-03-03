from pathlib import Path

from typer.testing import CliRunner

from larops.cli import app

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
    (source / "storage" / "logs").mkdir(parents=True, exist_ok=True)
    (source / "bootstrap" / "cache").mkdir(parents=True, exist_ok=True)
    (source / "storage" / "logs" / "laravel.log").write_text("ok", encoding="utf-8")
    (source / "bootstrap" / "cache" / "config.php").write_text("<?php return [];", encoding="utf-8")
    (source / "README.md").write_text("demo", encoding="utf-8")
    return source


def test_site_permissions_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "site", "permissions", "demo.test"])
    assert result.exit_code == 0
    assert "Site permissions plan prepared for demo.test" in result.stdout


def test_site_permissions_apply_changes_modes(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(
        app,
        ["--config", str(config), "site", "create", "demo.test", "--apply"],
    )
    assert create.exit_code == 0

    permissions = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "permissions",
            "demo.test",
            "--dir-mode",
            "750",
            "--file-mode",
            "640",
            "--writable-mode",
            "770",
            "--apply",
        ],
    )
    assert permissions.exit_code == 0

    current_path = (tmp_path / "apps" / "demo.test" / "current").resolve()
    normal_file = current_path / "README.md"
    writable_dir = current_path / "storage"
    assert (normal_file.stat().st_mode & 0o777) == 0o640
    assert (writable_dir.stat().st_mode & 0o777) == 0o770


def test_site_permissions_owner_group_pair_required(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    _ = make_source(tmp_path, "demo.test")
    create = runner.invoke(
        app,
        ["--config", str(config), "site", "create", "demo.test", "--apply"],
    )
    assert create.exit_code == 0

    permissions = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "site",
            "permissions",
            "demo.test",
            "--owner",
            "www-data",
            "--apply",
        ],
    )
    assert permissions.exit_code == 2
    assert "Use --owner and --group together" in permissions.stdout
