from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class SelinuxServiceError(RuntimeError):
    pass


def _resolved(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _within_any_root(path: Path, roots: list[Path]) -> bool:
    resolved_path = _resolved(path)
    for root in roots:
        try:
            resolved_path.relative_to(_resolved(root))
            return True
        except ValueError:
            continue
    return False


def detect_selinux_mode(*, run_command: Callable[..., Any]) -> str:
    try:
        completed = run_command(["getenforce"], check=False)
    except FileNotFoundError:
        return "disabled"
    mode = (completed.stdout or completed.stderr or "").strip().lower()
    if mode in {"enforcing", "permissive", "disabled"}:
        return mode
    return "disabled"


def relabel_managed_paths_for_selinux(
    paths: list[Path],
    *,
    run_command: Callable[..., Any],
    which: Callable[[str], str | None],
    roots: list[Path] | None = None,
) -> dict[str, Any]:
    unique_paths = list(dict.fromkeys(paths))
    if roots is not None:
        unique_paths = [path for path in unique_paths if _within_any_root(path, roots)]
        if not unique_paths:
            return {"mode": "skipped", "relabelled_paths": []}

    mode = detect_selinux_mode(run_command=run_command)
    if mode == "disabled":
        return {"mode": mode, "relabelled_paths": []}

    restorecon_bin = which("restorecon")
    if not restorecon_bin:
        raise SelinuxServiceError("SELinux is active but restorecon is not available.")

    relabelled_paths: list[str] = []
    for path in unique_paths:
        try:
            run_command([restorecon_bin, "-F", str(path)], check=True)
        except FileNotFoundError as exc:
            raise SelinuxServiceError(str(exc)) from exc
        relabelled_paths.append(str(path))
    return {"mode": mode, "relabelled_paths": relabelled_paths}
