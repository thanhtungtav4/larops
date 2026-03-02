from pathlib import Path
from subprocess import CompletedProcess

from larops.services.app_lifecycle import deploy_release, get_app_paths, initialize_app
from larops.services.runtime_process import enable_process, service_name, status_process


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
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", service] in calls


def test_worker_concurrency_renders_multi_process_execstart(monkeypatch, tmp_path: Path) -> None:
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
    service = service_name(domain, "worker")
    unit_body = (unit_dir / service).read_text(encoding="utf-8")
    assert "for i in $(seq 1 3)" in unit_body
    assert "queue:work" in unit_body


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
