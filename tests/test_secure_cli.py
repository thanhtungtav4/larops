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


def test_secure_ssh_apply_writes_drop_in_and_validates(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    sshd_drop_in = tmp_path / "ssh" / "sshd_config.d" / "larops.conf"
    sshd_config = tmp_path / "ssh" / "sshd_config"
    sshd_config.parent.mkdir(parents=True, exist_ok=True)
    sshd_config.write_text("Include sshd_config.d/*.conf\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command[:2] == ["sshd", "-t"]:
            return CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.secure_service.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "secure",
            "ssh",
            "--sshd-drop-in-file",
            str(sshd_drop_in),
            "--sshd-config-file",
            str(sshd_config),
            "--ssh-key-only",
            "--allow-user",
            "deploy",
            "--allow-user",
            "ops",
            "--allow-group",
            "wheel",
            "--max-startups",
            "10:30:60",
            "--no-reload",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    body = sshd_drop_in.read_text(encoding="utf-8")
    assert "PermitRootLogin no" in body
    assert "PasswordAuthentication no" in body
    assert "AllowAgentForwarding no" in body
    assert "AllowUsers deploy ops" in body
    assert "AllowGroups wheel" in body
    assert "MaxStartups 10:30:60" in body


def test_secure_ssh_rejects_whitespace_in_allow_user(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    sshd_drop_in = tmp_path / "ssh" / "sshd_config.d" / "larops.conf"
    sshd_config = tmp_path / "ssh" / "sshd_config"
    sshd_config.parent.mkdir(parents=True, exist_ok=True)
    sshd_config.write_text("Include sshd_config.d/*.conf\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "secure",
            "ssh",
            "--sshd-drop-in-file",
            str(sshd_drop_in),
            "--sshd-config-file",
            str(sshd_config),
            "--allow-user",
            "deploy user",
            "--no-reload",
            "--apply",
        ],
    )
    assert result.exit_code == 2
    assert "cannot contain whitespace" in result.stdout


def test_secure_nginx_apply_writes_files_and_injects_include(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    http_config = tmp_path / "nginx" / "conf.d" / "larops-security-http.conf"
    server_snippet = tmp_path / "nginx" / "snippets" / "larops-security-server.conf"
    server_config = tmp_path / "nginx" / "sites-enabled" / "example.conf"
    server_config.parent.mkdir(parents=True, exist_ok=True)
    server_config.write_text(
        "\n".join(
            [
                "server {",
                "    listen 80;",
                "    server_name example.test;",
                "    root /var/www/example.test/current/public;",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command == ["nginx", "-t"]:
            return CompletedProcess(command, 0, stdout="ok", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.secure_service.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "secure",
            "nginx",
            "--http-config-file",
            str(http_config),
            "--server-snippet-file",
            str(server_snippet),
            "--server-config-file",
            str(server_config),
            "--no-reload",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert http_config.exists()
    assert server_snippet.exists()
    http_body = http_config.read_text(encoding="utf-8")
    assert "limit_req_zone $larops_login_limit_key zone=larops_login:10m rate=5r/m;" in http_body
    snippet_body = server_snippet.read_text(encoding="utf-8")
    assert "location = /wp-login.php { return 404; }" in snippet_body
    server_body = server_config.read_text(encoding="utf-8")
    assert f"include {server_snippet};" in server_body


def test_secure_nginx_strict_profile_applies_stricter_defaults_and_extra_blocks(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    http_config = tmp_path / "nginx" / "conf.d" / "larops-security-http.conf"
    server_snippet = tmp_path / "nginx" / "snippets" / "larops-security-server.conf"
    server_config = tmp_path / "nginx" / "sites-enabled" / "example.conf"
    server_config.parent.mkdir(parents=True, exist_ok=True)
    server_config.write_text("server {\n    listen 80;\n}\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        if command == ["nginx", "-t"]:
            return CompletedProcess(command, 0, stdout="ok", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("larops.services.secure_service.run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config),
            "secure",
            "nginx",
            "--profile",
            "strict",
            "--block-path",
            "/private/",
            "--http-config-file",
            str(http_config),
            "--server-snippet-file",
            str(server_snippet),
            "--server-config-file",
            str(server_config),
            "--no-reload",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    http_body = http_config.read_text(encoding="utf-8")
    assert "rate=3r/m" in http_body
    assert "rate=30r/m" in http_body
    snippet_body = server_snippet.read_text(encoding="utf-8")
    assert "location = /adminer.php { return 404; }" in snippet_body
    assert "location ^~ /vendor/ { return 404; }" in snippet_body
    assert "location ^~ /private/ { return 404; }" in snippet_body
