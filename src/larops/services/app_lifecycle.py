from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class AppLifecycleError(RuntimeError):
    pass


@dataclass(slots=True)
class AppPaths:
    root: Path
    releases: Path
    shared: Path
    metadata: Path
    current: Path


def get_app_paths(base_releases_path: Path, state_path: Path, domain: str) -> AppPaths:
    app_root = base_releases_path / domain
    return AppPaths(
        root=app_root,
        releases=app_root / "releases",
        shared=app_root / "shared",
        metadata=state_path / "apps" / f"{domain}.json",
        current=app_root / "current",
    )


def load_metadata(metadata_path: Path) -> dict:
    if not metadata_path.exists():
        raise AppLifecycleError(f"Application is not registered: {metadata_path.stem}")
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    return raw


def save_metadata(metadata_path: Path, payload: dict) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def initialize_app(paths: AppPaths, payload: dict, *, overwrite: bool = False) -> None:
    if paths.metadata.exists() and not overwrite:
        raise AppLifecycleError("Application already exists. Use --force to recreate metadata.")

    paths.releases.mkdir(parents=True, exist_ok=True)
    paths.shared.mkdir(parents=True, exist_ok=True)
    (paths.shared / "storage").mkdir(parents=True, exist_ok=True)
    (paths.shared / "bootstrap").mkdir(parents=True, exist_ok=True)

    save_metadata(paths.metadata, payload)


def next_release_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def copy_release(paths: AppPaths, source_path: Path, ref: str) -> tuple[str, Path]:
    if not source_path.exists() or not source_path.is_dir():
        raise AppLifecycleError(f"Source path does not exist or is not a directory: {source_path}")

    release_id = next_release_id()
    release_dir = paths.releases / release_id
    shutil.copytree(
        source_path,
        release_dir,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".venv",
            ".ruff_cache",
            ".mypy_cache",
            "node_modules",
        ),
    )
    marker = {
        "release_id": release_id,
        "ref": ref,
        "deployed_at": datetime.now(UTC).isoformat(),
    }
    (release_dir / ".larops-release.json").write_text(
        json.dumps(marker, indent=2),
        encoding="utf-8",
    )
    return release_id, release_dir


def activate_release(paths: AppPaths, release_dir: Path) -> None:
    if not release_dir.exists():
        raise AppLifecycleError(f"Release directory is missing: {release_dir}")
    switch_current_symlink(paths.current, release_dir)


def deploy_release(paths: AppPaths, source_path: Path, ref: str) -> str:
    release_id, release_dir = copy_release(paths, source_path, ref)
    activate_release(paths, release_dir)
    return release_id


def switch_current_symlink(current_symlink: Path, target_release: Path) -> None:
    current_symlink.parent.mkdir(parents=True, exist_ok=True)
    tmp_link = current_symlink.parent / ".current_tmp"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    tmp_link.symlink_to(target_release)
    tmp_link.replace(current_symlink)


def list_releases(paths: AppPaths) -> list[str]:
    if not paths.releases.exists():
        return []
    return sorted([item.name for item in paths.releases.iterdir() if item.is_dir()])


def get_current_release(paths: AppPaths) -> str | None:
    if not paths.current.exists() or not paths.current.is_symlink():
        return None
    try:
        target = paths.current.resolve(strict=True)
    except FileNotFoundError:
        return None
    return target.name


def resolve_rollback_target(paths: AppPaths, requested_target: str) -> str:
    releases = list_releases(paths)
    if not releases:
        raise AppLifecycleError("No releases available for rollback.")

    current = get_current_release(paths)
    if requested_target == "previous":
        candidates = [item for item in releases if item != current]
        if not candidates:
            raise AppLifecycleError("No previous release available for rollback.")
        return candidates[-1]

    if requested_target not in releases:
        raise AppLifecycleError(f"Requested release does not exist: {requested_target}")
    if requested_target == current:
        raise AppLifecycleError("Requested release is already active.")
    return requested_target


def rollback_release(paths: AppPaths, target_release_id: str) -> None:
    release_dir = paths.releases / target_release_id
    if not release_dir.exists():
        raise AppLifecycleError(f"Rollback target is missing: {target_release_id}")
    switch_current_symlink(paths.current, release_dir)


def prune_releases(paths: AppPaths, keep_releases: int) -> list[str]:
    if keep_releases < 1:
        return []

    releases = list_releases(paths)
    current = get_current_release(paths)
    if len(releases) <= keep_releases:
        return []

    deleted: list[str] = []
    for release_id in releases:
        if len(releases) - len(deleted) <= keep_releases:
            break
        if release_id == current:
            continue
        target = paths.releases / release_id
        shutil.rmtree(target, ignore_errors=True)
        deleted.append(release_id)
    return deleted
