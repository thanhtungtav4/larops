import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

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


def test_security_install_plan_mode(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "security", "install"])
    assert result.exit_code == 0
    assert "Security install plan prepared." in result.stdout
    assert "Plan mode finished. Use --apply to execute changes." in result.stdout


def test_security_install_apply_writes_fail2ban_files(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    jail_file = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    filter_file = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "security",
            "install",
            "--fail2ban-jail-file",
            str(jail_file),
            "--fail2ban-filter-file",
            str(filter_file),
            "--nginx-log-path",
            str(tmp_path / "nginx-access.log"),
            "--fail2ban-log-path",
            str(tmp_path / "fail2ban.log"),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert jail_file.exists()
    assert filter_file.exists()
    assert ["ufw", "allow", "22/tcp"] in calls
    assert ["ufw", "allow", "80/tcp"] in calls
    assert ["ufw", "allow", "443/tcp"] in calls
    assert ["ufw", "--force", "enable"] in calls
    assert ["systemctl", "restart", "fail2ban"] in calls


def test_security_status_json(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["ufw", "status"]:
            return CompletedProcess(command, 0, stdout="Status: active\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"]:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(app, ["--config", str(config), "--json", "security", "status"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    payload = lines[-1]["report"]
    assert payload["ufw"]["exit_code"] == 0
    assert "Status: active" in payload["ufw"]["raw"]


def test_security_report_parses_logs(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    fail2ban_log = tmp_path / "fail2ban.log"
    nginx_log = tmp_path / "access.log"
    fail2ban_log.write_text(
        "\n".join(
            [
                "2026-03-03 10:00:00,001 fail2ban.actions [123]: NOTICE [sshd] Ban 1.2.3.4",
                "2026-03-03 10:02:00,001 fail2ban.actions [123]: NOTICE [sshd] Ban 1.2.3.4",
                "2026-03-03 10:03:00,001 fail2ban.actions [123]: NOTICE [sshd] Ban 5.6.7.8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nginx_log.write_text(
        "\n".join(
            [
                '1.2.3.4 - - [03/Mar/2026:10:00:00 +0700] "GET /.env HTTP/1.1" 404 150 "-" "curl/8.0"',
                '1.2.3.4 - - [03/Mar/2026:10:00:01 +0700] "GET /wp-login.php HTTP/1.1" 404 150 "-" "curl/8.0"',
                '9.9.9.9 - - [03/Mar/2026:10:00:02 +0700] "GET /health HTTP/1.1" 200 10 "-" "curl/8.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "report",
            "--fail2ban-log-path",
            str(fail2ban_log),
            "--nginx-log-path",
            str(nginx_log),
            "--top",
            "2",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    report = lines[-1]["report"]
    assert report["fail2ban"]["top_banned_ips"][0]["ip"] == "1.2.3.4"
    assert report["nginx_scan"]["suspicious_404_total"] == 2


def test_security_report_since_window_filters_old_lines(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    fail2ban_log = tmp_path / "fail2ban.log"
    nginx_log = tmp_path / "access.log"
    now_utc = datetime.now(UTC)
    recent_fail2ban = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    old_fail2ban = (now_utc - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    recent_nginx = now_utc.astimezone().strftime("%d/%b/%Y:%H:%M:%S %z")
    old_nginx = (now_utc - timedelta(hours=3)).astimezone().strftime("%d/%b/%Y:%H:%M:%S %z")

    fail2ban_log.write_text(
        "\n".join(
            [
                f"{old_fail2ban},001 fail2ban.actions [123]: NOTICE [sshd] Ban 8.8.8.8",
                f"{recent_fail2ban},001 fail2ban.actions [123]: NOTICE [sshd] Ban 1.1.1.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nginx_log.write_text(
        "\n".join(
            [
                f'8.8.8.8 - - [{old_nginx}] "GET /.env HTTP/1.1" 404 150 "-" "curl/8.0"',
                f'1.1.1.1 - - [{recent_nginx}] "GET /.env HTTP/1.1" 404 150 "-" "curl/8.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "report",
            "--fail2ban-log-path",
            str(fail2ban_log),
            "--nginx-log-path",
            str(nginx_log),
            "--since",
            "1h",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    report = lines[-1]["report"]
    assert report["window"]["since"] == "1h"
    assert report["fail2ban"]["top_banned_ips"][0]["ip"] == "1.1.1.1"
    assert report["nginx_scan"]["suspicious_404_total"] == 1


def test_security_report_invalid_since_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "security", "report", "--since", "tomorrow"])
    assert result.exit_code == 2
    assert "Invalid --since format" in result.stdout
