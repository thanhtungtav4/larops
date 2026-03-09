import shlex
import subprocess


class ShellCommandError(RuntimeError):
    pass


def run_command(
    command: list[str],
    *,
    check: bool = True,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds if timeout_seconds and timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        quoted = shlex.join(command)
        raise ShellCommandError(
            f"command timed out after {timeout_seconds}s: {quoted}"
        ) from exc
    if check and completed.returncode != 0:
        quoted = shlex.join(command)
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail_parts: list[str] = []
        if stderr:
            detail_parts.append(stderr)
        if stdout:
            detail_parts.append(stdout if not stderr else f"stdout:\n{stdout}")
        detail = "\n".join(detail_parts).strip()
        raise ShellCommandError(
            f"command failed ({completed.returncode}): {quoted}" + (f"\n{detail}" if detail else "")
        )
    return completed
