from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from larops.core.shell import run_command
from larops.services.app_lifecycle import get_app_paths
from larops.services.runtime_process import (
    RuntimeProcessError,
    disable_process,
    enable_process,
    ensure_app_registered,
    service_name,
)

_PROCESS_TYPES = ("worker", "scheduler", "horizon")


class SiteDeleteError(RuntimeError):
    pass


def _secret_files(state_path: Path, domain: str) -> list[Path]:
    return [
        state_path / "secrets" / "db" / f"{domain}.cnf",
        state_path / "secrets" / "db" / f"{domain}.pgpass",
    ]


def default_checkpoint_dir(state_path: Path, domain: str) -> Path:
    return state_path / "backups" / "site-delete" / domain


def create_delete_checkpoint(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    checkpoint_dir: Path | None = None,
    include_secrets: bool = False,
) -> Path:
    ensure_app_registered(base_releases_path, state_path, domain)
    paths = get_app_paths(base_releases_path, state_path, domain)
    runtime_dir = state_path / "runtime" / domain
    credential_files = _secret_files(state_path, domain)

    target_dir = checkpoint_dir or default_checkpoint_dir(state_path, domain)
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_dir.chmod(0o700)
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
        if include_secrets:
            for credential_file in credential_files:
                if credential_file.exists():
                    archive.add(credential_file, arcname=f"state/secrets/db/{credential_file.name}")
                    added_entries += 1

    if added_entries == 0:
        raise SiteDeleteError(f"No artifacts found to checkpoint for {domain}.")
    checkpoint.chmod(0o600)
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
    credential_files = _secret_files(state_path, domain)
    removed_units: list[str] = []
    disabled_runtime: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for process_type in _PROCESS_TYPES:
        spec_path = runtime_dir / f"{process_type}.json"
        unit_names = [service_name(domain, process_type)]
        if spec_path.exists():
            try:
                disabled_runtime[process_type] = disable_process(
                    base_releases_path=base_releases_path,
                    state_path=state_path,
                    systemd_manage=systemd_manage,
                    domain=domain,
                    process_type=process_type,
                )
                raw_names = disabled_runtime[process_type].get("service_names", [])
                if isinstance(raw_names, list):
                    unit_names = [str(item) for item in raw_names if str(item).strip()] or unit_names
            except RuntimeProcessError as exc:
                errors.append(f"{process_type}: {exc}")

        for unit_name in unit_names:
            unit_path = unit_dir / unit_name
            if unit_path.exists():
                unit_path.unlink()
                removed_units.append(str(unit_path))

    if systemd_manage and removed_units:
        run_command(["systemctl", "daemon-reload"], check=False)

    removed_paths: list[str] = []
    for path in (runtime_dir, paths.metadata, paths.root, *credential_files):
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


def restore_site_checkpoint(
    *,
    base_releases_path: Path,
    state_path: Path,
    unit_dir: Path,
    systemd_manage: bool,
    service_user: str,
    domain: str,
    checkpoint_file: Path,
    force: bool,
    restore_runtime: bool,
    restore_secrets: bool,
) -> dict[str, Any]:
    if not checkpoint_file.exists():
        raise SiteDeleteError(f"Checkpoint file not found: {checkpoint_file}")
    paths = get_app_paths(base_releases_path, state_path, domain)
    runtime_dir = state_path / "runtime" / domain
    secret_files = _secret_files(state_path, domain)

    if not force and (paths.root.exists() or paths.metadata.exists() or runtime_dir.exists()):
        raise SiteDeleteError(f"Restore target already exists for {domain}. Use --force to overwrite.")

    with tempfile.TemporaryDirectory(prefix="larops-site-restore-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        with tarfile.open(checkpoint_file, mode="r:gz") as archive:
            try:
                archive.extractall(temp_dir, filter="tar")
            except TypeError:
                archive.extractall(temp_dir)

        extracted_root = temp_dir / "apps" / domain
        extracted_metadata = temp_dir / "state" / "apps" / f"{domain}.json"
        extracted_runtime = temp_dir / "state" / "runtime" / domain
        extracted_secret_files = [temp_dir / "state" / "secrets" / "db" / path.name for path in secret_files]

        restored_paths: list[str] = []
        for target in (paths.root, paths.metadata, runtime_dir, *secret_files):
            if target.exists() or target.is_symlink():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)

        if extracted_root.exists():
            paths.root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted_root, paths.root, symlinks=True)
            restored_paths.append(str(paths.root))
        if extracted_metadata.exists():
            paths.metadata.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extracted_metadata, paths.metadata)
            restored_paths.append(str(paths.metadata))
        if extracted_runtime.exists():
            runtime_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted_runtime, runtime_dir, symlinks=True)
            restored_paths.append(str(runtime_dir))
        if restore_secrets:
            for extracted_secret, secret_file in zip(extracted_secret_files, secret_files, strict=False):
                if extracted_secret.exists():
                    secret_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(extracted_secret, secret_file)
                    restored_paths.append(str(secret_file))

    recreated_runtime: dict[str, Any] = {}
    if restore_runtime and runtime_dir.exists():
        for spec_path in sorted(runtime_dir.glob("*.json")):
            try:
                payload = json.loads(spec_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SiteDeleteError(f"Invalid runtime spec in checkpoint: {spec_path}") from exc
            if not isinstance(payload, dict):
                continue
            process_type = str(payload.get("process_type") or spec_path.stem)
            options = payload.get("options")
            policy = payload.get("policy")
            if not isinstance(options, dict):
                options = {}
            if payload.get("enabled") is not True:
                continue
            recreated_runtime[process_type] = enable_process(
                base_releases_path=base_releases_path,
                state_path=state_path,
                unit_dir=unit_dir,
                systemd_manage=systemd_manage,
                service_user=service_user,
                domain=domain,
                process_type=process_type,
                options=options,
                policy=policy if isinstance(policy, dict) else None,
            )

    return {
        "domain": domain,
        "checkpoint_file": str(checkpoint_file),
        "restored_paths": restored_paths,
        "recreated_runtime": recreated_runtime,
        "restore_runtime": restore_runtime,
        "restore_secrets": restore_secrets and any(path.exists() for path in secret_files),
    }
