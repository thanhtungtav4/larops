from pathlib import Path
from subprocess import CompletedProcess

from larops.services.notify_systemd import (
    enable_telegram_daemon,
    status_telegram_daemon,
    telegram_service_name,
)


def test_enable_telegram_daemon_writes_unit_and_calls_systemctl(monkeypatch, tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    config_path = tmp_path / "larops.yaml"
    config_path.write_text("environment: test\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.notify_systemd.run_command", fake_run_command)
    result = enable_telegram_daemon(
        unit_dir=unit_dir,
        systemd_manage=True,
        user="www-data",
        larops_bin="/usr/local/bin/larops",
        config_path=config_path,
        interval_seconds=15,
        batch_size=30,
        env_file=Path("/etc/larops/telegram.env"),
    )

    service = telegram_service_name()
    unit_path = unit_dir / service
    unit_text = unit_path.read_text(encoding="utf-8")
    assert result["service_name"] == service
    assert unit_path.exists()
    assert "notify telegram watch --interval 15 --iterations 0 --apply --batch-size 30" in unit_text
    assert 'EnvironmentFile=-"/etc/larops/telegram.env"' in unit_text
    assert "NoNewPrivileges=true" in unit_text
    assert "ProtectSystem=full" in unit_text
    assert "UMask=0027" in unit_text
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", service] in calls


def test_status_telegram_daemon_managed(monkeypatch, tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    service = telegram_service_name()
    unit_path = unit_dir / service
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nDescription=x\n", encoding="utf-8")

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.notify_systemd.run_command", fake_run_command)
    status = status_telegram_daemon(unit_dir=unit_dir, systemd_manage=True)
    assert status["unit_exists"] is True
    assert status["systemd"]["active"] == "active"
    assert status["systemd"]["enabled"] == "enabled"
