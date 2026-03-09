from __future__ import annotations

import os
import pwd
import grp
import re
from pathlib import Path
from typing import Any

from larops.services.app_lifecycle import get_app_paths
from larops.services.runtime_process import ensure_app_registered


class PermissionServiceError(RuntimeError):
    pass


def _parse_mode(raw: str, label: str) -> int:
    value = raw.strip()
    if not re.fullmatch(r"[0-7]{3,4}", value):
        raise PermissionServiceError(f"Invalid {label}: {raw}. Use octal format like 755.")
    return int(value, 8)


def _iter_tree(root: Path) -> list[Path]:
    paths = [root]
    for item in root.rglob("*"):
        if item.is_symlink():
            continue
        paths.append(item)
    return paths


def _resolve_owner_group(owner: str | None, group: str | None) -> tuple[int | None, int | None]:
    if (owner is None) != (group is None):
        raise PermissionServiceError("Use --owner and --group together, or omit both.")
    if owner is None or group is None:
        return None, None
    try:
        uid = pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise PermissionServiceError(f"Owner user not found: {owner}") from exc
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise PermissionServiceError(f"Group not found: {group}") from exc
    return uid, gid


def _resolve_owner_group_best_effort(owner: str | None, group: str | None) -> tuple[int | None, int | None]:
    if (owner is None) != (group is None):
        raise PermissionServiceError("Use --owner and --group together, or omit both.")
    if owner is None or group is None:
        return None, None
    try:
        uid = pwd.getpwnam(owner).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return None, None
    return uid, gid


def _apply_owner(path: Path, uid: int | None, gid: int | None) -> bool:
    if uid is None or gid is None:
        return False
    os.chown(path, uid, gid, follow_symlinks=False)
    return True


def _set_mode(path: Path, mode: int) -> bool:
    current = path.stat().st_mode & 0o777
    if current == mode:
        return False
    os.chmod(path, mode, follow_symlinks=False)
    return True


def _resolve_default_writable_paths(app_root: Path, current_path: Path) -> list[Path]:
    return [
        app_root / "shared" / "storage",
        app_root / "shared" / "bootstrap",
        current_path / "storage",
        current_path / "bootstrap" / "cache",
    ]


def reassign_site_permissions(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    owner: str | None,
    group: str | None,
    dir_mode_raw: str,
    file_mode_raw: str,
    writable_mode_raw: str,
    writable_paths: list[str],
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    app_paths = get_app_paths(base_releases_path, state_path, domain)
    if not app_paths.root.exists():
        raise PermissionServiceError(f"App root is missing: {app_paths.root}")

    dir_mode = _parse_mode(dir_mode_raw, "dir mode")
    file_mode = _parse_mode(file_mode_raw, "file mode")
    writable_mode = _parse_mode(writable_mode_raw, "writable mode")
    uid, gid = _resolve_owner_group(owner, group)

    changed_mode = 0
    changed_owner = 0
    for path in _iter_tree(app_paths.root):
        changed_owner += int(_apply_owner(path, uid, gid))
        if path.is_dir():
            changed_mode += int(_set_mode(path, dir_mode))
        elif path.is_file():
            changed_mode += int(_set_mode(path, file_mode))

    current_path = app_paths.current.resolve(strict=False) if app_paths.current.exists() else app_paths.current
    resolved_writable: list[Path] = []
    if writable_paths:
        for raw in writable_paths:
            candidate = (app_paths.root / raw).resolve(strict=False)
            if candidate.exists():
                resolved_writable.append(candidate)
    else:
        resolved_writable = [path for path in _resolve_default_writable_paths(app_paths.root, current_path) if path.exists()]

    writable_changed = 0
    for writable_root in resolved_writable:
        for path in _iter_tree(writable_root):
            writable_changed += int(_set_mode(path, writable_mode))

    return {
        "domain": domain,
        "root": str(app_paths.root),
        "owner": owner,
        "group": group,
        "dir_mode": oct(dir_mode),
        "file_mode": oct(file_mode),
        "writable_mode": oct(writable_mode),
        "writable_paths": [str(path) for path in resolved_writable],
        "changed_mode_count": changed_mode,
        "changed_owner_count": changed_owner,
        "changed_writable_count": writable_changed,
    }


def ensure_site_writable_permissions(
    *,
    base_releases_path: Path,
    state_path: Path,
    domain: str,
    owner: str | None,
    group: str | None,
    writable_mode_raw: str = "775",
) -> dict[str, Any]:
    ensure_app_registered(base_releases_path, state_path, domain)
    app_paths = get_app_paths(base_releases_path, state_path, domain)
    current_path = app_paths.current.resolve(strict=False) if app_paths.current.exists() else app_paths.current
    writable_mode = _parse_mode(writable_mode_raw, "writable mode")
    uid, gid = _resolve_owner_group_best_effort(owner, group)
    targets = [path for path in _resolve_default_writable_paths(app_paths.root, current_path) if path.exists()]

    changed_mode = 0
    changed_owner = 0
    for target in targets:
        for path in _iter_tree(target):
            changed_owner += int(_apply_owner(path, uid, gid))
            changed_mode += int(_set_mode(path, writable_mode))

    return {
        "domain": domain,
        "writable_paths": [str(path) for path in targets],
        "owner": owner,
        "group": group,
        "owner_group_applied": uid is not None and gid is not None,
        "writable_mode": oct(writable_mode),
        "changed_mode_count": changed_mode,
        "changed_owner_count": changed_owner,
    }
