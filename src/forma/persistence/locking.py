"""Process-owned file locks shared by desktop and command-line writers."""

from __future__ import annotations

import errno
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class FileLockBusy(TimeoutError):
    """Another writer owns this lock."""


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        # Windows permits locking beyond EOF; no shared byte needs rewriting.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def file_lock(path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    """Hold an exclusive lock until exit or process death.

    Keep the empty lock file in place: unlinking it would let another writer
    lock a different inode while an existing owner still holds the original.
    The descriptor is not inherited by exec'd workers. A leftover file after
    a crash is harmless because ownership lives in the operating system.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Invalid file lock timeout")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fd)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FileLockBusy("Another writer is using this file") from None
                time.sleep(min(0.025, remaining))
        yield
    finally:
        # Closing releases both flock and Windows byte-range ownership.
        os.close(fd)
