from larops.core.locks import CommandLock, CommandLockError


def test_command_lock_conflict() -> None:
    lock_name = "test-lock"
    lock_one = CommandLock(lock_name)
    lock_two = CommandLock(lock_name)

    lock_one.acquire()
    try:
        try:
            lock_two.acquire()
            raised = False
        except CommandLockError:
            raised = True
        assert raised is True
    finally:
        lock_one.release()

