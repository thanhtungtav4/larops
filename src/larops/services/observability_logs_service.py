from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Any

import yaml

from larops.core.shell import ShellCommandError, run_command
from larops.services.host_layout_service import default_nginx_access_logs as default_host_nginx_access_logs
from larops.services.host_layout_service import default_nginx_error_logs as default_host_nginx_error_logs
from larops.services.selinux_service import SelinuxServiceError, relabel_managed_paths_for_selinux


class ObservabilityLogsError(RuntimeError):
    pass


def observability_logs_service_name() -> str:
    return "larops-observability-logs.service"


def _managed_marker_path(data_dir: Path) -> Path:
    return data_dir / ".larops-managed"


def _unit_path(unit_dir: Path) -> Path:
    return unit_dir / observability_logs_service_name()


def supported_sinks() -> list[str]:
    return ["http", "vector"]


def default_laravel_log_patterns(releases_path: Path) -> list[str]:
    return [str(releases_path / "*" / "current" / "storage" / "logs" / "*.log")]


def default_nginx_access_logs() -> list[str]:
    return default_host_nginx_access_logs()


def default_nginx_error_logs() -> list[str]:
    return default_host_nginx_error_logs()


def _validate_sink_config(
    *,
    sink: str,
    vector_address: str | None,
    http_uri: str | None,
    http_bearer_token_env_var: str,
    http_env_file: Path | None,
) -> None:
    normalized = sink.strip().lower()
    if normalized not in supported_sinks():
        supported = ", ".join(supported_sinks())
        raise ObservabilityLogsError(f"Unsupported sink: {sink}. Supported: {supported}.")
    if normalized == "vector" and not (vector_address or "").strip():
        raise ObservabilityLogsError("--vector-address is required when --sink=vector.")
    if normalized == "http":
        if not (http_uri or "").strip():
            raise ObservabilityLogsError("--http-uri is required when --sink=http.")
        if http_env_file is None:
            raise ObservabilityLogsError("--http-env-file is required when --sink=http.")
        if not http_bearer_token_env_var.strip():
            raise ObservabilityLogsError("--http-bearer-token-env-var cannot be empty when --sink=http.")
        if not http_env_file.exists() or not http_env_file.is_file():
            raise ObservabilityLogsError(f"HTTP env file does not exist: {http_env_file}")
        env_lines = http_env_file.read_text(encoding="utf-8").splitlines()
        prefix = f"{http_bearer_token_env_var.strip()}="
        if not any(line.strip().startswith(prefix) and line.strip()[len(prefix) :].strip() for line in env_lines):
            raise ObservabilityLogsError(
                f"HTTP env file does not define a non-empty {http_bearer_token_env_var.strip()} variable: {http_env_file}"
            )


def _ensure_within_root(*, path: Path, allowed_root: Path, label: str) -> None:
    resolved_root = allowed_root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ObservabilityLogsError(f"{label} must stay within {resolved_root}: {resolved_path}") from exc


def _normalize_patterns(patterns: list[str]) -> list[str]:
    normalized: list[str] = []
    for pattern in patterns:
        cleaned = pattern.strip()
        if cleaned:
            normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _file_source_component(
    *,
    include: list[str],
    data_dir: Path,
) -> dict[str, Any]:
    return {
        "type": "file",
        "include": include,
        "ignore_not_found": True,
        "read_from": "end",
        "data_dir": str(data_dir),
    }


def _tag_transform_component(*, source_name: str, stream_name: str) -> dict[str, Any]:
    return {
        "type": "remap",
        "inputs": [source_name],
        "source": "\n".join(
            [
                f'.larops_stream = "{stream_name}"',
                '.larops_component = "observability_logs"',
            ]
        ),
    }


def render_vector_logs_config(
    *,
    data_dir: Path,
    events_path: Path,
    laravel_logs: list[str],
    nginx_access_logs: list[str],
    nginx_error_logs: list[str],
    extra_logs: list[str],
    sink: str,
    vector_address: str | None,
    http_uri: str | None,
    http_bearer_token_env_var: str,
) -> str:
    sources: dict[str, Any] = {}
    transforms: dict[str, Any] = {}
    inputs: list[str] = []

    source_sets = [
        ("larops_events", [str(events_path)]),
        ("laravel_logs", _normalize_patterns(laravel_logs)),
        ("nginx_access_logs", _normalize_patterns(nginx_access_logs)),
        ("nginx_error_logs", _normalize_patterns(nginx_error_logs)),
        ("extra_logs", _normalize_patterns(extra_logs)),
    ]
    for source_name, include in source_sets:
        if not include:
            continue
        sources[source_name] = _file_source_component(include=include, data_dir=data_dir / source_name)
        transform_name = f"{source_name}_tag"
        transforms[transform_name] = _tag_transform_component(source_name=source_name, stream_name=source_name)
        inputs.append(transform_name)

    if not inputs:
        raise ObservabilityLogsError("At least one log source must be configured.")

    sink_name = "ship_logs"
    sink_payload: dict[str, Any]
    if sink == "vector":
        sink_payload = {
            "type": "vector",
            "inputs": inputs,
            "address": vector_address,
            "buffer": {"type": "disk", "max_size": 536870912},
        }
    else:
        sink_payload = {
            "type": "http",
            "inputs": inputs,
            "uri": http_uri,
            "method": "post",
            "compression": "none",
            "encoding": {"codec": "json"},
            "framing": {"method": "newline_delimited"},
            "buffer": {"type": "disk", "max_size": 536870912},
        }
        if http_bearer_token_env_var.strip():
            sink_payload["auth"] = {
                "strategy": "bearer",
                "token": f"${{{http_bearer_token_env_var}}}",
            }

    payload = {
        "data_dir": str(data_dir),
        "sources": sources,
        "transforms": transforms,
        "sinks": {sink_name: sink_payload},
    }
    return yaml.safe_dump(payload, sort_keys=False)


def render_vector_logs_unit(
    *,
    vector_bin: str,
    config_file: Path,
    user: str,
    env_file: Path | None,
) -> str:
    command = [vector_bin, "--config", str(config_file), "--watch-config"]
    exec_start = shlex.join(command)
    lines = [
        "[Unit]",
        "Description=LarOps observability log shipper (Vector)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=full",
        "ProtectHome=read-only",
        "ProtectControlGroups=true",
        "ProtectKernelModules=true",
        "ProtectKernelTunables=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "RestrictRealtime=true",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "UMask=0027",
        "Restart=always",
        "RestartSec=5",
        "WorkingDirectory=/",
        f"ExecStart={exec_start}",
    ]
    if env_file is not None:
        escaped = str(env_file).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'EnvironmentFile=-"{escaped}"')
    lines.extend(
        [
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    return "\n".join(lines)


def _run_systemctl(args: list[str], *, check: bool = True) -> str:
    completed = run_command(["systemctl", *args], check=check)
    return (completed.stdout or completed.stderr or "").strip()


def _systemd_status(service: str) -> dict[str, Any]:
    active = run_command(["systemctl", "is-active", service], check=False)
    enabled = run_command(["systemctl", "is-enabled", service], check=False)
    return {
        "active": (active.stdout or active.stderr or "").strip(),
        "enabled": (enabled.stdout or enabled.stderr or "").strip(),
    }


def _relabel_managed_system_paths(paths: list[Path]) -> None:
    try:
        relabel_managed_paths_for_selinux(
            paths,
            run_command=run_command,
            which=shutil.which,
            roots=[Path("/etc"), Path("/etc/systemd/system"), Path("/usr/lib/systemd/system")],
        )
    except SelinuxServiceError as exc:
        raise ObservabilityLogsError(str(exc)) from exc


def enable_logs_shipping(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    service_user: str,
    vector_bin: str,
    config_file: Path,
    data_dir: Path,
    events_path: Path,
    laravel_logs: list[str],
    nginx_access_logs: list[str],
    nginx_error_logs: list[str],
    extra_logs: list[str],
    sink: str,
    vector_address: str | None,
    http_uri: str | None,
    http_bearer_token_env_var: str,
    http_env_file: Path | None,
) -> dict[str, Any]:
    _validate_sink_config(
        sink=sink,
        vector_address=vector_address,
        http_uri=http_uri,
        http_bearer_token_env_var=http_bearer_token_env_var,
        http_env_file=http_env_file,
    )
    if not shutil.which(vector_bin):
        raise ObservabilityLogsError(f"Vector binary not found: {vector_bin}")

    config_body = render_vector_logs_config(
        data_dir=data_dir,
        events_path=events_path,
        laravel_logs=laravel_logs,
        nginx_access_logs=nginx_access_logs,
        nginx_error_logs=nginx_error_logs,
        extra_logs=extra_logs,
        sink=sink,
        vector_address=vector_address,
        http_uri=http_uri,
        http_bearer_token_env_var=http_bearer_token_env_var if http_env_file is not None else "",
    )
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    _managed_marker_path(data_dir).write_text("larops-observability-logs\n", encoding="utf-8")
    config_file.write_text(config_body, encoding="utf-8")

    unit_path = _unit_path(unit_dir)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        render_vector_logs_unit(
            vector_bin=vector_bin,
            config_file=config_file,
            user=service_user,
            env_file=http_env_file,
        ),
        encoding="utf-8",
    )
    _relabel_managed_system_paths([config_file, unit_path])

    if systemd_manage:
        try:
            _run_systemctl(["daemon-reload"], check=True)
            _run_systemctl(["enable", "--now", observability_logs_service_name()], check=True)
        except ShellCommandError as exc:
            raise ObservabilityLogsError(str(exc)) from exc

    return {
        "service_name": observability_logs_service_name(),
        "unit_path": str(unit_path),
        "config_file": str(config_file),
        "data_dir": str(data_dir),
        "sink": sink,
        "vector_address": vector_address,
        "http_uri": http_uri,
        "systemd_managed": systemd_manage,
    }


def disable_logs_shipping(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    remove_files: bool,
    config_file: Path,
    data_dir: Path,
    allowed_data_root: Path,
) -> dict[str, Any]:
    service = observability_logs_service_name()
    unit_path = _unit_path(unit_dir)
    removed_paths: list[str] = []
    if systemd_manage and unit_path.exists():
        try:
            _run_systemctl(["disable", "--now", service], check=True)
        except (ShellCommandError, FileNotFoundError) as exc:
            raise ObservabilityLogsError(str(exc)) from exc

    if remove_files:
        _ensure_within_root(path=data_dir, allowed_root=allowed_data_root, label="data_dir")
        for path in (unit_path, config_file):
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
        if data_dir.exists():
            marker_path = _managed_marker_path(data_dir)
            if not marker_path.exists():
                raise ObservabilityLogsError(f"Refusing to remove unmanaged data_dir without marker: {data_dir}")
            shutil.rmtree(data_dir)
            removed_paths.append(str(data_dir))
        if systemd_manage and removed_paths:
            _run_systemctl(["daemon-reload"], check=False)

    return {
        "service_name": service,
        "enabled": False,
        "removed_paths": removed_paths,
        "systemd_managed": systemd_manage,
    }


def status_logs_shipping(
    *,
    unit_dir: Path,
    systemd_manage: bool,
    config_file: Path,
    data_dir: Path,
    vector_bin: str,
) -> dict[str, Any]:
    service = observability_logs_service_name()
    unit_path = _unit_path(unit_dir)
    return {
        "service_name": service,
        "unit_path": str(unit_path),
        "unit_exists": unit_path.exists(),
        "config_file": str(config_file),
        "config_exists": config_file.exists(),
        "data_dir": str(data_dir),
        "data_dir_exists": data_dir.exists(),
        "vector_bin": vector_bin,
        "vector_bin_exists": shutil.which(vector_bin) is not None,
        "systemd": _systemd_status(service)
        if systemd_manage
        else {"active": "unmanaged", "enabled": "unmanaged"},
    }
