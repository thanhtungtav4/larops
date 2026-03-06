from pathlib import Path
from subprocess import CompletedProcess

from larops.services.app_lifecycle import deploy_release, get_app_paths, initialize_app
from larops.services.runtime_process import enable_process, reconcile_process, service_name, status_process


def seed_deployed_app(tmp_path: Path, domain: str) -> tuple[Path, Path]:
    base_releases = tmp_path / "apps with space"
    state_path = tmp_path / "state"
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.txt").write_text("runtime", encoding="utf-8")

    paths = get_app_paths(base_releases, state_path, domain)
    initialize_app(paths, {"domain": domain}, overwrite=False)
    deploy_release(paths, source, "main")
    return base_releases, state_path


def test_enable_process_calls_systemd_and_writes_quoted_unit(monkeypatch, tmp_path: Path) -> None:
    domain = "demo.test"
    base_releases, state_path = seed_deployed_app(tmp_path, domain)
    unit_dir = tmp_path / "units"
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.runtime_process.run_command", fake_run_command)
    spec = enable_process(
        base_releases_path=base_releases,
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=True,
        service_user="www-data",
        domain=domain,
        process_type="worker",
        options={"queue": "emails", "tries": 3, "timeout": 90, "concurrency": 1},
    )

    service = service_name(domain, "worker")
    unit_path = unit_dir / service
    unit_body = unit_path.read_text(encoding="utf-8")
    expected_cd = f'cd "{base_releases / domain / "current"}"'

    assert spec["enabled"] is True
    assert unit_path.exists()
    assert expected_cd in unit_body
    assert "NoNewPrivileges=true" in unit_body
    assert "ProtectSystem=full" in unit_body
    assert "UMask=0027" in unit_body
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", service] in calls


def test_worker_concurrency_renders_multiple_units(monkeypatch, tmp_path: Path) -> None:
    domain = "demo.test"
    base_releases, state_path = seed_deployed_app(tmp_path, domain)
    unit_dir = tmp_path / "units"

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.runtime_process.run_command", fake_run_command)
    enable_process(
        base_releases_path=base_releases,
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=False,
        service_user="www-data",
        domain=domain,
        process_type="worker",
        options={"queue": "emails", "tries": 3, "timeout": 90, "concurrency": 3},
    )
    unit_bodies = []
    for replica in range(1, 4):
        unit_path = unit_dir / service_name(domain, "worker", replica=replica)
        assert unit_path.exists()
        unit_bodies.append(unit_path.read_text(encoding="utf-8"))
    assert all("queue:work" in body for body in unit_bodies)
    assert all("for i in $(seq 1 3)" not in body for body in unit_bodies)


def test_status_process_reads_systemd_state(monkeypatch, tmp_path: Path) -> None:
    domain = "demo.test"
    base_releases, state_path = seed_deployed_app(tmp_path, domain)
    unit_dir = tmp_path / "units"

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout="active\n", stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.runtime_process.run_command", fake_run_command)
    enable_process(
        base_releases_path=base_releases,
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=False,
        service_user="www-data",
        domain=domain,
        process_type="horizon",
        options={},
    )

    status = status_process(
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=True,
        domain=domain,
        process_type="horizon",
    )
    assert status["systemd"]["active"] == "active"
    assert status["systemd"]["enabled"] == "enabled"


def test_status_process_is_read_only_for_failed_service(monkeypatch, tmp_path: Path) -> None:
    domain = "demo.test"
    base_releases, state_path = seed_deployed_app(tmp_path, domain)
    unit_dir = tmp_path / "units"
    restarted = {"value": False}

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        if command[:2] == ["systemctl", "is-active"]:
            state = "active\n" if restarted["value"] else "failed\n"
            return CompletedProcess(command, 0, stdout=state, stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.runtime_process.run_command", fake_run_command)
    enable_process(
        base_releases_path=base_releases,
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=False,
        service_user="www-data",
        domain=domain,
        process_type="worker",
        options={"queue": "default", "tries": 3, "timeout": 90, "concurrency": 1},
        policy={"max_restarts": 2, "window_seconds": 300, "cooldown_seconds": 120, "auto_heal": True},
    )

    status = status_process(
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=True,
        domain=domain,
        process_type="worker",
        policy={"max_restarts": 2, "window_seconds": 300, "cooldown_seconds": 120, "auto_heal": True},
    )
    assert status["auto_heal"]["status"] == "degraded"
    assert status["systemd"]["active"] == "failed"
    assert restarted["value"] is False
    assert status["restart_count"] == 0


def test_reconcile_process_restarts_failed_service(monkeypatch, tmp_path: Path) -> None:
    domain = "demo.test"
    base_releases, state_path = seed_deployed_app(tmp_path, domain)
    unit_dir = tmp_path / "units"
    restarted = {"value": False}
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["systemctl", "is-active"]:
            state = "active\n" if restarted["value"] else "failed\n"
            return CompletedProcess(command, 0, stdout=state, stderr="")
        if command[:2] == ["systemctl", "is-enabled"]:
            return CompletedProcess(command, 0, stdout="enabled\n", stderr="")
        if command[:2] == ["systemctl", "restart"]:
            restarted["value"] = True
            return CompletedProcess(command, 0, stdout="", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.runtime_process.run_command", fake_run_command)
    enable_process(
        base_releases_path=base_releases,
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=False,
        service_user="www-data",
        domain=domain,
        process_type="worker",
        options={"queue": "default", "tries": 3, "timeout": 90, "concurrency": 1},
    )

    result = reconcile_process(
        state_path=state_path,
        unit_dir=unit_dir,
        systemd_manage=True,
        domain=domain,
        process_type="worker",
    )
    assert result["action"] == "restart"
    assert result["before"]["systemd"]["active"] == "failed"
    assert result["after"]["systemd"]["active"] == "active"
    assert ["systemctl", "restart", service_name(domain, "worker")] in calls
