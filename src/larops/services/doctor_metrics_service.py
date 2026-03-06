from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class DoctorMetricsError(RuntimeError):
    pass


def _status_code(status: str) -> int:
    return {"ok": 0, "warn": 1, "error": 2}.get(str(status).strip().lower(), 2)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric_line(name: str, value: int | float, **labels: str) -> str:
    if labels:
        encoded = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
        return f"{name}{{{encoded}}} {value}"
    return f"{name} {value}"


def render_prometheus_metrics(report: dict[str, Any], *, include_checks: bool) -> str:
    lines = [
        "# HELP larops_fleet_status Overall LarOps fleet status code (0=ok,1=warn,2=error).",
        "# TYPE larops_fleet_status gauge",
        _metric_line("larops_fleet_status", _status_code(report["overall"])),
        "# HELP larops_fleet_registered_apps Total registered application count on this host.",
        "# TYPE larops_fleet_registered_apps gauge",
        _metric_line("larops_fleet_registered_apps", int(report.get("registered_apps") and len(report["registered_apps"]) or 0)),
        "# HELP larops_fleet_targets_total Total target count included in the fleet report.",
        "# TYPE larops_fleet_targets_total gauge",
        _metric_line("larops_fleet_targets_total", int(report.get("target_count", 0))),
        "# HELP larops_target_status Overall status code for each target (0=ok,1=warn,2=error).",
        "# TYPE larops_target_status gauge",
        "# HELP larops_target_checks_total Number of checks per target and status.",
        "# TYPE larops_target_checks_total gauge",
    ]

    if include_checks:
        lines.extend(
            [
                "# HELP larops_check_status Status code for each exported check (0=ok,1=warn,2=error).",
                "# TYPE larops_check_status gauge",
            ]
        )

    for target in report.get("targets", []):
        target_name = str(target["target"])
        lines.append(_metric_line("larops_target_status", _status_code(target["overall"]), target=target_name))
        for status_name in ("ok", "warn", "error"):
            lines.append(
                _metric_line(
                    "larops_target_checks_total",
                    int(target["counts"].get(status_name, 0)),
                    target=target_name,
                    status=status_name,
                )
            )
        if include_checks:
            for check in target.get("checks", []):
                lines.append(
                    _metric_line(
                        "larops_check_status",
                        _status_code(check["status"]),
                        target=target_name,
                        check=str(check["name"]),
                    )
                )

    return "\n".join(lines) + "\n"


def write_metrics_file(*, output_file: Path, metrics_text: str) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=output_file.parent, prefix=".larops-metrics-", delete=False) as handle:
            handle.write(metrics_text)
            temp_path = Path(handle.name)
        temp_path.replace(output_file)
    except OSError as exc:
        raise DoctorMetricsError(str(exc)) from exc
    return output_file
