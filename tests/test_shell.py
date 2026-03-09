from larops.core.shell import ShellCommandError, run_command


def test_run_command_failure_includes_stdout_when_stderr_is_empty() -> None:
    try:
        run_command(["bash", "-lc", "printf 'laravel-error'; exit 1"], check=True)
    except ShellCommandError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ShellCommandError")

    assert "command failed (1)" in message
    assert "laravel-error" in message


def test_run_command_failure_includes_both_stderr_and_stdout() -> None:
    try:
        run_command(["bash", "-lc", "printf 'stdout-body'; printf 'stderr-body' >&2; exit 1"], check=True)
    except ShellCommandError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ShellCommandError")

    assert "stderr-body" in message
    assert "stdout:\nstdout-body" in message
