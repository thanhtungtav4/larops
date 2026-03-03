from __future__ import annotations

import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from larops.core.shell import run_command
from larops.services.app_lifecycle import get_app_paths
from larops.services.runtime_process import (
    RuntimeProcessError,
    disable_process,
    ensure_app_registered,
    service_name,
)

_PROCESS_TYPES = ("worker", "scheduler", "horizon")


class SiteDeleteError(RuntimeError):
    pass


def default_checkpoint_dir(state_path: Path, domain: str) -> Path:
    return state_path / "backups" / "site-delete" / domain


def create_delete_checkpoint(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    checkpoint_dir: Path | None = None,
) -> Path:
    ensure_app_registered(base_releases_path, state_path, domain)
    paths = get_app_paths(base_releases_path, state_path, domain)
    runtime_dir = state_path / "runtime" / domain
    credential_file = state_path / "secrets" / "db" / f"{domain}.cnf"

    target_dir = checkpoint_dir or default_checkpoint_dir(state_path, domain)
    target_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = target_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.tar.gz"

    added_entries = 0
    with tarfile.open(checkpoint, mode="w:gz") as archive:
        if paths.root.exists():
            archive.add(paths.root, arcname=f"apps/{domain}")
            added_entries += 1
        if paths.metadata.exists():
            archive.add(paths.metadata, arcname=f"state/apps/{domain}.json")
            added_entries += 1
        if runtime_dir.exists():
            archive.add(runtime_dir, arcname=f"state/runtime/{domain}")
            added_entries += 1
        if credential_file.exists():
            archive.add(credential_file, arcname=f"state/secrets/db/{domain}.cnf")
            added_entries += 1

    if added_entries == 0:
        raise SiteDeleteError(f"No artifacts found to checkpoint for {domain}.")
    return checkpoint


def purge_site(
    *,
    base_releases_path: Path,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    domain: str,
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    paths = get_app_paths(base_releases_path, state_path, domain)
    runtime_dir = state_path / "runtime" / domain
    removed_units: list[str] = []
    disabled_runtime: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for process_type in _PROCESS_TYPES:
        spec_path = runtime_dir / f"{process_type}.json"
        if spec_path.exists():
            try:
                disabled_runtime[process_type] = disable_process(
                    base_releases_path=base_releases_path,
                    state_path=state_path,
                    systemd_manage=systemd_manage,
                    domain=domain,
                    process_type=process_type,
                )
            except RuntimeProcessError as exc:
                errors.append(f"{process_type}: {exc}")

        unit_path = unit_dir / service_name(domain, process_type)
        if unit_path.exists():
            unit_path.unlink()
            removed_units.append(str(unit_path))

    if systemd_manage and removed_units:
        run_command(["systemctl", "daemon-reload"], check=False)

    removed_paths: list[str] = []
    for path in (runtime_dir, paths.metadata, paths.root):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed_paths.append(str(path))
            continue
        if path.exists() or path.is_symlink():
            path.unlink()
            removed_paths.append(str(path))

    if errors:
        raise SiteDeleteError("; ".join(errors))

    return {
        "domain": domain,
        "disabled_runtime": disabled_runtime,
        "removed_units": removed_units,
        "removed_paths": removed_paths,
    }
