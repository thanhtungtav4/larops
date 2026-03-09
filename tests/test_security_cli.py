import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from larops.cli import app
from larops.core.shell import ShellCommandError

runner = CliRunner()


def ubuntu_env(tmp_path: Path) -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n', encoding="utf-8")
    return {"LAROPS_STACK_OS_RELEASE_PATH": str(os_release)}


def el9_env(tmp_path: Path, *, os_id: str = "rocky") -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text(f'ID="{os_id}"\nVERSION_ID="9.4"\n', encoding="utf-8")
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
    result = runner.invoke(app, ["--config", str(config), "security", "install"], env=ubuntu_env(tmp_path))
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
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 0
    assert jail_file.exists()
    assert filter_file.exists()
    jail_body = jail_file.read_text(encoding="utf-8")
    assert "[DEFAULT]\nbanaction = ufw" in jail_body
    assert "[sshd]\nenabled = true\nbackend = systemd" in jail_body
    assert "[larops-nginx-scan]\nenabled = true\nbackend = auto" in jail_body
    assert ["ufw", "allow", "22/tcp"] in calls
    assert ["ufw", "allow", "80/tcp"] in calls
    assert ["ufw", "allow", "443/tcp"] in calls
    assert ["ufw", "--force", "enable"] in calls
    assert ["systemctl", "enable", "--now", "fail2ban"] in calls
    assert ["systemctl", "restart", "fail2ban"] in calls


def test_security_install_apply_fails_when_fail2ban_systemctl_fails(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    jail_file = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    filter_file = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:3] == ["systemctl", "enable", "--now"]:
            if check:
                raise ShellCommandError("command failed (1): systemctl enable --now fail2ban")
            return CompletedProcess(command, 1, stdout="", stderr="boom")
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
            "--apply",
        ],
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 1
    assert "systemctl enable --now fail2ban" in result.stdout


def test_security_install_apply_restores_previous_fail2ban_files_on_failure(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    jail_file = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    filter_file = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    jail_file.parent.mkdir(parents=True, exist_ok=True)
    filter_file.parent.mkdir(parents=True, exist_ok=True)
    jail_file.write_text("[old-jail]\nenabled=true\n", encoding="utf-8")
    filter_file.write_text("[Definition]\nfailregex = old\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:3] == ["systemctl", "enable", "--now"] and check:
            raise ShellCommandError("command failed (1): systemctl enable --now fail2ban")
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
            "--apply",
        ],
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 1
    assert jail_file.read_text(encoding="utf-8") == "[old-jail]\nenabled=true\n"
    assert filter_file.read_text(encoding="utf-8") == "[Definition]\nfailregex = old\n"


def test_security_status_json(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["ufw", "status"]:
            return CompletedProcess(command, 0, stdout="Status: active\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"]:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(app, ["--config", str(config), "--json", "security", "status"], env=ubuntu_env(tmp_path))
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    payload = lines[-1]["report"]
    assert payload["firewall"]["backend"] == "ufw"
    assert payload["firewall"]["exit_code"] == 0
    assert "Status: active" in payload["firewall"]["raw"]


def test_security_status_errors_when_jail_is_missing(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    jail_file = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    filter_file = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    jail_file.parent.mkdir(parents=True, exist_ok=True)
    filter_file.parent.mkdir(parents=True, exist_ok=True)
    jail_file.write_text("[sshd]\nenabled=true\n", encoding="utf-8")
    filter_file.write_text("[Definition]\nfailregex = test\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["ufw", "status"]:
            return CompletedProcess(command, 0, stdout="Status: active\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"] and len(command) == 2:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "sshd"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: sshd\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "larops-nginx-scan"]:
            return CompletedProcess(command, 255, stdout="", stderr="Sorry but the jail does not exist\n")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "status",
            "--fail2ban-jail-file",
            str(jail_file),
            "--fail2ban-filter-file",
            str(filter_file),
        ],
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "error"


def test_security_posture_ok_when_controls_are_present(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    state_apps_dir = tmp_path / "state" / "apps"
    units_dir = tmp_path / "units"
    fail2ban_jail = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    fail2ban_filter = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    sshd_drop_in = tmp_path / "ssh" / "sshd_config.d" / "larops.conf"
    nginx_http = tmp_path / "nginx" / "conf.d" / "larops-security-http.conf"
    nginx_snippet = tmp_path / "nginx" / "snippets" / "larops-security-server.conf"
    nginx_server = tmp_path / "nginx" / "sites-enabled" / "example.conf"

    for path in (fail2ban_jail, fail2ban_filter, sshd_drop_in, nginx_http, nginx_snippet, nginx_server):
        path.parent.mkdir(parents=True, exist_ok=True)

    fail2ban_jail.write_text("[sshd]\nenabled=true\n", encoding="utf-8")
    fail2ban_filter.write_text("[Definition]\nfailregex = test\n", encoding="utf-8")
    sshd_drop_in.write_text("# Managed by LarOps\nPermitRootLogin no\n", encoding="utf-8")
    nginx_http.write_text("# Managed by LarOps\n", encoding="utf-8")
    nginx_snippet.write_text("# Managed by LarOps\n", encoding="utf-8")
    nginx_server.write_text(f"server {{\n    include {nginx_snippet};\n}}\n", encoding="utf-8")

    state_apps_dir.mkdir(parents=True, exist_ok=True)
    (state_apps_dir / "example.com.json").write_text("{}", encoding="utf-8")

    for name in (
        "larops-monitor-scan.service",
        "larops-monitor-scan.timer",
        "larops-monitor-fim.service",
        "larops-monitor-fim.timer",
        "larops-monitor-service.service",
        "larops-monitor-service.timer",
        "larops-notify-telegram.service",
        "larops-monitor-app-example-com.service",
        "larops-monitor-app-example-com.timer",
    ):
        (units_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (units_dir / name).write_text("[Unit]\nDescription=test\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["ufw", "status"]:
            return CompletedProcess(command, 0, stdout="Status: active\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"] and len(command) == 2:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "sshd"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: sshd\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "larops-nginx-scan"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: larops-nginx-scan\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "posture",
            "--fail2ban-jail-file",
            str(fail2ban_jail),
            "--fail2ban-filter-file",
            str(fail2ban_filter),
            "--sshd-drop-in-file",
            str(sshd_drop_in),
            "--nginx-http-config-file",
            str(nginx_http),
            "--nginx-server-snippet-file",
            str(nginx_snippet),
            "--nginx-server-config-file",
            str(nginx_server),
        ],
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    report = payload["report"]
    assert report["checks"]["secure_ssh"] == "ok"
    assert report["checks"]["secure_nginx"] == "ok"
    assert report["checks"]["scan_timer"] == "ok"
    assert report["checks"]["telegram_notifier"] == "ok"
    assert report["checks"]["app_timers"] == "ok"
    assert report["registered_apps"] == ["example.com"]


def test_security_posture_errors_when_hardening_files_are_missing(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    fail2ban_jail = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    fail2ban_filter = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    fail2ban_jail.parent.mkdir(parents=True, exist_ok=True)
    fail2ban_filter.parent.mkdir(parents=True, exist_ok=True)
    fail2ban_jail.write_text("[sshd]\nenabled=true\n", encoding="utf-8")
    fail2ban_filter.write_text("[Definition]\nfailregex = test\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["ufw", "status"]:
            return CompletedProcess(command, 0, stdout="Status: active\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"] and len(command) == 2:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "sshd"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: sshd\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "larops-nginx-scan"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: larops-nginx-scan\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "posture",
            "--fail2ban-jail-file",
            str(fail2ban_jail),
            "--fail2ban-filter-file",
            str(fail2ban_filter),
        ],
        env=ubuntu_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "error"
    report = payload["report"]
    assert report["checks"]["secure_ssh"] == "error"
    assert report["checks"]["secure_nginx"] == "error"
    assert report["checks"]["scan_timer"] == "error"


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
                '1.2.3.4 - - [03/Mar/2026:10:00:01 +0700] "GET /wp-login.php HTTP/1.1" 403 150 "-" "curl/8.0"',
                '9.9.9.9 - - [03/Mar/2026:10:00:02 +0700] "GET /health HTTP/1.1" 200 10 "-" "curl/8.0"',
                '5.6.7.8 - - [03/Mar/2026:10:00:03 +0700] "GET /phpmyadmin HTTP/1.1" 444 0 "-" "curl/8.0"',
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
    assert report["nginx_scan"]["suspicious_404_total"] == 3


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


def test_security_report_since_scans_full_window_not_tail_only(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    fail2ban_log = tmp_path / "fail2ban.log"
    nginx_log = tmp_path / "access.log"
    now_utc = datetime.now(UTC)
    recent_fail2ban = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    older_in_window_fail2ban = (now_utc - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    recent_nginx = now_utc.astimezone().strftime("%d/%b/%Y:%H:%M:%S %z")
    older_in_window_nginx = (now_utc - timedelta(minutes=20)).astimezone().strftime("%d/%b/%Y:%H:%M:%S %z")

    fail2ban_log.write_text(
        "\n".join(
            [
                f"{older_in_window_fail2ban},001 fail2ban.actions [123]: NOTICE [sshd] Ban 8.8.8.8",
                f"{recent_fail2ban},001 fail2ban.actions [123]: NOTICE [sshd] Ban 1.1.1.1",
                "2026-03-01 01:00:00,001 fail2ban.actions [123]: NOTICE [sshd] Ban 9.9.9.9",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nginx_log.write_text(
        "\n".join(
            [
                f'8.8.8.8 - - [{older_in_window_nginx}] "GET /.env HTTP/1.1" 404 150 "-" "curl/8.0"',
                f'1.1.1.1 - - [{recent_nginx}] "GET /wp-login.php HTTP/1.1" 403 150 "-" "curl/8.0"',
                '9.9.9.9 - - [01/Mar/2026:01:00:00 +0700] "GET /phpmyadmin HTTP/1.1" 444 0 "-" "curl/8.0"',
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
            "--max-lines",
            "1",
        ],
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    report = lines[-1]["report"]
    assert report["fail2ban"]["lines_scanned"] == 3
    assert report["nginx_scan"]["lines_scanned"] == 3
    assert [item["ip"] for item in report["fail2ban"]["top_banned_ips"]] == ["8.8.8.8", "1.1.1.1"]
    assert report["nginx_scan"]["suspicious_404_total"] == 2


def test_security_report_invalid_since_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "security", "report", "--since", "tomorrow"])
    assert result.exit_code == 2
    assert "Invalid --since format" in result.stdout


def test_security_install_el9_plan_uses_firewalld(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "--json", "security", "install"], env=el9_env(tmp_path))
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[0])
    assert payload["firewall_backend"] == "firewalld"
    assert ["systemctl", "enable", "--now", "firewalld"] in payload["firewall_commands"]
    assert ["firewall-cmd", "--reload"] in payload["firewall_commands"]


def test_security_install_rhel_plan_mentions_fail2ban_repo_requirement(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config), "--json", "security", "install"], env=el9_env(tmp_path, os_id="rhel"))
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[0])
    assert payload["firewall_backend"] == "firewalld"
    assert any("Fail2ban" in note and "repository" in note for note in payload["notes"])


def test_security_install_apply_el9_writes_firewalld_fail2ban_jail(tmp_path: Path, monkeypatch) -> None:
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
            "--apply",
        ],
        env=el9_env(tmp_path),
    )
    assert result.exit_code == 0
    jail_body = jail_file.read_text(encoding="utf-8")
    assert "[DEFAULT]\nbanaction = firewallcmd-rich-rules" in jail_body
    assert ["systemctl", "enable", "--now", "firewalld"] in calls
    assert ["firewall-cmd", "--reload"] in calls


def test_security_status_el9_uses_firewalld_state(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    jail_file = tmp_path / "fail2ban" / "jail.d" / "larops.conf"
    filter_file = tmp_path / "fail2ban" / "filter.d" / "larops-nginx-scan.conf"
    jail_file.parent.mkdir(parents=True, exist_ok=True)
    filter_file.parent.mkdir(parents=True, exist_ok=True)
    jail_file.write_text("[sshd]\nenabled=true\n[larops-nginx-scan]\nenabled=true\n", encoding="utf-8")
    filter_file.write_text("[Definition]\nfailregex = test\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:3] == ["systemctl", "is-active", "firewalld"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:3] == ["systemctl", "is-enabled", "firewalld"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        if command[:2] == ["firewall-cmd", "--state"]:
            return CompletedProcess(command, 0, stdout="running\n", stderr="")
        if command[:2] == ["fail2ban-client", "status"] and len(command) == 2:
            return CompletedProcess(command, 0, stdout="Status\n|- Number of jail: 2\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "sshd"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: sshd\n", stderr="")
        if command[:3] == ["fail2ban-client", "status", "larops-nginx-scan"]:
            return CompletedProcess(command, 0, stdout="Status for the jail: larops-nginx-scan\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.security_service.run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "--json",
            "security",
            "status",
            "--fail2ban-jail-file",
            str(jail_file),
            "--fail2ban-filter-file",
            str(filter_file),
        ],
        env=el9_env(tmp_path),
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["report"]["firewall"]["backend"] == "firewalld"
    assert payload["status"] == "ok"
