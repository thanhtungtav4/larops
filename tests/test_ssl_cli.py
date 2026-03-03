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
                "  user: root",
                "events:",
                "  sink: jsonl",
                f"  path: {tmp_path / 'events.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def test_ssl_issue_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "ssl", "issue", "example.test", "--challenge", "http"],
    )
    assert result.exit_code == 0
    assert "SSL issue plan prepared for example.test" in result.stdout


def test_ssl_check_missing_cert_file(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    cert = tmp_path / "missing.pem"
    result = runner.invoke(
        app,
        ["--config", str(config), "ssl", "check", "example.test", "--cert-file", str(cert)],
    )
    assert result.exit_code == 2
    assert "Certificate file not found" in result.stdout


def test_ssl_auto_renew_enable_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        ["--config", str(config), "ssl", "auto-renew", "enable"],
    )
    assert result.exit_code == 0
    assert "SSL auto-renew enable plan prepared." in result.stdout


def test_ssl_auto_renew_enable_apply_writes_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "ssl",
            "auto-renew",
            "enable",
            "--on-calendar",
            "*-*-* 02:00:00",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    service = tmp_path / "units" / "larops-ssl-renew.service"
    timer = tmp_path / "units" / "larops-ssl-renew.timer"
    assert service.exists()
    assert timer.exists()
    service_body = service.read_text(encoding="utf-8")
    assert "NoNewPrivileges=true" in service_body
    assert "UMask=0077" in service_body
    assert "OnCalendar=*-*-* 02:00:00" in timer.read_text(encoding="utf-8")


def test_ssl_auto_renew_status_json(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        ["--config", str(config), "ssl", "auto-renew", "enable", "--apply"],
    )
    assert enable.exit_code == 0

    status = runner.invoke(
        app,
        ["--config", str(config), "--json", "ssl", "auto-renew", "status"],
    )
    assert status.exit_code == 0
    import json

    lines = [json.loads(line) for line in status.stdout.strip().splitlines()]
    payload = lines[-1]["auto_renew"]
    assert payload["service_unit_exists"] is True
    assert payload["timer_unit_exists"] is True


def test_ssl_auto_renew_disable_remove_units(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    enable = runner.invoke(
        app,
        ["--config", str(config), "ssl", "auto-renew", "enable", "--apply"],
    )
    assert enable.exit_code == 0

    disable = runner.invoke(
        app,
        ["--config", str(config), "ssl", "auto-renew", "disable", "--remove-units", "--apply"],
    )
    assert disable.exit_code == 0
    service = tmp_path / "units" / "larops-ssl-renew.service"
    timer = tmp_path / "units" / "larops-ssl-renew.timer"
    assert not service.exists()
    assert not timer.exists()
