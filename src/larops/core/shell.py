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
        raise ShellCommandError(
            f"command failed ({completed.returncode}): {quoted}\n{completed.stderr.strip()}"
        )
    return completed
