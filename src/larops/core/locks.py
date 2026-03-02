import fcntl
from pathlib import Path
from typing import TextIO


class CommandLockError(RuntimeError):
    pass


class CommandLock:
    def __init__(self, lock_name: str) -> None:
        self.lock_path = Path("/tmp") / f"larops-{lock_name}.lock"
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CommandLockError(f"lock already held: {self.lock_path}") from exc

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "CommandLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

