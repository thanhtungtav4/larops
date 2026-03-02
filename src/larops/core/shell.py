import shlex
import subprocess


class ShellCommandError(RuntimeError):
    pass


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        quoted = shlex.join(command)
        raise ShellCommandError(
            f"command failed ({completed.returncode}): {quoted}\n{completed.stderr.strip()}"
        )
    return completed

