from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_FIM_PATTERNS = [
    ".env",
    "composer.lock",
    "public/index.php",
    "routes/*",
    "config/*",
]


class MonitorFimError(RuntimeError):
    pass


def _contains_glob(pattern: str) -> bool:
    return any(char in pattern for char in ("*", "?", "["))


def _collect_files(root: Path, patterns: list[str]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        normalized = pattern.strip()
        if not normalized:
            continue
        if _contains_glob(normalized):
            for path in root.glob(normalized):
                if path.is_file():
                    files.add(path)
            continue
        target = root / normalized
        if target.is_file():
            files.add(target)
        elif target.is_dir():
            for path in target.rglob("*"):
                if path.is_file():
                    files.add(path)
    return sorted(files)


def _hash_file(path: Path, *, algorithm: str) -> str:
    if algorithm != "sha256":
        raise MonitorFimError(f"Unsupported hash algorithm: {algorithm}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot(*, root: Path, patterns: list[str], algorithm: str) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in _collect_files(root, patterns):
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = _hash_file(path, algorithm=algorithm)
    return snapshot


def init_fim_baseline(
    *,
    root: Path,
    baseline_path: Path,
    patterns: list[str],
    algorithm: str,
) -> dict[str, Any]:
    root_resolved = root.resolve()
    if not root_resolved.exists() or not root_resolved.is_dir():
        raise MonitorFimError(f"Root path does not exist or is not a directory: {root_resolved}")
    selected_patterns = patterns or list(DEFAULT_FIM_PATTERNS)
    snapshot = build_snapshot(root=root_resolved, patterns=selected_patterns, algorithm=algorithm)
    payload = {
        "root": str(root_resolved),
        "algorithm": algorithm,
        "patterns": selected_patterns,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "files": snapshot,
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "baseline_path": str(baseline_path),
        "root": str(root_resolved),
        "algorithm": algorithm,
        "patterns": selected_patterns,
        "file_count": len(snapshot),
    }


def _load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MonitorFimError(f"Baseline file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MonitorFimError(f"Baseline is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MonitorFimError(f"Baseline payload must be a JSON object: {path}")
    return payload


def run_fim_check(
    *,
    baseline_path: Path,
    root: Path | None,
    update_baseline: bool,
) -> dict[str, Any]:
    baseline = _load_baseline(baseline_path)
    root_path = (root or Path(str(baseline.get("root") or ""))).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise MonitorFimError(f"Root path does not exist or is not a directory: {root_path}")

    algorithm = str(baseline.get("algorithm") or "sha256")
    patterns_raw = baseline.get("patterns")
    if not isinstance(patterns_raw, list):
        raise MonitorFimError("Baseline is missing patterns list.")
    patterns = [str(item) for item in patterns_raw]

    baseline_files_raw = baseline.get("files")
    if not isinstance(baseline_files_raw, dict):
        raise MonitorFimError("Baseline is missing files map.")
    baseline_files = {str(key): str(value) for key, value in baseline_files_raw.items()}

    current_files = build_snapshot(root=root_path, patterns=patterns, algorithm=algorithm)
    created = sorted([path for path in current_files if path not in baseline_files])
    deleted = sorted([path for path in baseline_files if path not in current_files])
    changed = sorted(
        [
            {
                "path": path,
                "old_hash": baseline_files[path],
                "new_hash": current_files[path],
            }
            for path in current_files
            if path in baseline_files and baseline_files[path] != current_files[path]
        ],
        key=lambda item: str(item["path"]),
    )

    if update_baseline:
        baseline["root"] = str(root_path)
        baseline["algorithm"] = algorithm
        baseline["patterns"] = patterns
        baseline["updated_at"] = datetime.now(UTC).isoformat()
        baseline["files"] = current_files
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")

    return {
        "baseline_path": str(baseline_path),
        "root": str(root_path),
        "algorithm": algorithm,
        "patterns": patterns,
        "counts": {
            "baseline_files": len(baseline_files),
            "current_files": len(current_files),
            "created": len(created),
            "deleted": len(deleted),
            "changed": len(changed),
        },
        "created": created,
        "deleted": deleted,
        "changed": changed,
        "has_changes": bool(created or deleted or changed),
        "baseline_updated": update_baseline,
    }
