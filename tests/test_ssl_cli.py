from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from larops.cli import app
from larops.core.shell import ShellCommandError

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


def test_ssl_issue_plan_mode_uses_managed_site_webroot(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    releases_root = tmp_path / "apps"
    state_root = tmp_path / "state"
    current = releases_root / "example.test" / "current"
    release = releases_root / "example.test" / "releases" / "r1"
    public = release / "public"
    public.mkdir(parents=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(release)
    metadata = state_root / "apps" / "example.test.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text('{"php":"8.4"}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["--config", str(config), "--json", "ssl", "issue", "example.test", "--challenge", "http"],
    )
    assert result.exit_code == 0
    assert str(public) in result.stdout


def test_ssl_issue_apply_reports_missing_certbot_cleanly(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command and command[0] == "certbot":
            raise FileNotFoundError("certbot")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.ssl_service.run_command", fake_run_command)

    result = runner.invoke(
        app,
        ["--config", str(config), "ssl", "issue", "example.test", "--challenge", "http", "--apply"],
    )
    assert result.exit_code == 1
    assert "certbot is not installed" in result.stdout


def test_ssl_issue_apply_rerenders_managed_site_https(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    releases_root = tmp_path / "apps"
    state_root = tmp_path / "state"
    release = releases_root / "example.test" / "releases" / "r1"
    public = release / "public"
    public.mkdir(parents=True)
    current = releases_root / "example.test" / "current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(release)
    metadata = state_root / "apps" / "example.test.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text('{"php":"8.4"}', encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_issue(command: list[str]) -> str:
        captured["command"] = command
        return "ok"

    def fake_apply_nginx_site_config(*, domain: str, current_path: Path, php_version: str, https_enabled: bool, force: bool) -> dict:
        captured["nginx"] = {
            "domain": domain,
            "current_path": str(current_path),
            "php_version": php_version,
            "https_enabled": https_enabled,
            "force": force,
        }
        return {"https_enabled": https_enabled}

    monkeypatch.setattr("larops.commands.ssl.run_issue", fake_run_issue)
    monkeypatch.setattr("larops.commands.ssl.apply_nginx_site_config", fake_apply_nginx_site_config)

    result = runner.invoke(
        app,
        ["--config", str(config), "ssl", "issue", "example.test", "--challenge", "http", "--apply"],
    )
    assert result.exit_code == 0
    assert captured["nginx"] == {
        "domain": "example.test",
        "current_path": str(release),
        "php_version": "8.4",
        "https_enabled": True,
        "force": True,
    }


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


def test_ssl_auto_renew_disable_fails_when_systemctl_fails(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace("  manage: false", "  manage: true"),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        if command[:2] == ["systemctl", "disable"]:
            if check:
                raise ShellCommandError("command failed (1): systemctl disable --now larops-ssl-renew.timer")
            return CompletedProcess(command, 1, stdout="", stderr="boom")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.ssl_auto_renew.run_command", fake_run_command)

    result = runner.invoke(app, ["--config", str(config), "ssl", "auto-renew", "disable", "--apply"])
    assert result.exit_code == 1
    assert "systemctl disable --now larops-ssl-renew.timer" in result.stdout
