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

