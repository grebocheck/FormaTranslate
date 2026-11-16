"""Bounded diagnostics and owned process-tree cleanup for external workers."""

from __future__ import annotations

import os
import signal
import subprocess
import threading


def start_worker_tree(start, arguments, **kwargs):
    """Launch in an owned POSIX session or a Windows job before executing code."""
    job = None
    process = None
    if os.name == "nt":
        from forma.documents.windows_job import CREATE_SUSPENDED, WindowsJob

        job = WindowsJob()
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_SUSPENDED
    kwargs["start_new_session"] = os.name != "nt"
    try:
        process = start(arguments, **kwargs)
        process._forma_job = job
        process._forma_tree_stopped = False
        if job:
            job.assign_and_resume(process.pid)
        return process
    except BaseException:
        if job:
            job.close()
        # An assignment/resume failure must never leave a suspended worker.
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        raise


class BoundedWorkerLog:
    """Drain stdout continuously while keeping at most ``limit`` bytes on disk."""

    def __init__(self, path, limit: int):
        self.path, self.limit = path, limit
        self.exceeded = threading.Event()
        self.failed = threading.Event()

    def __enter__(self):
        destination = self.path.open("wb", buffering=0)
        try:
            read_fd, write_fd = os.pipe()
        except OSError:
            destination.close()
            raise
        self.output = os.fdopen(write_fd, "wb", buffering=0)
        self.reader = threading.Thread(
            target=self._copy, args=(read_fd, destination), daemon=True,
        )
        self.reader.start()
        return self

    def _copy(self, descriptor, destination):
        written = 0
        try:
            with os.fdopen(descriptor, "rb", buffering=0) as source, destination:
                while chunk := source.read(65536):
                    remaining = max(0, self.limit - written)
                    if len(chunk) > remaining:
                        self.exceeded.set()
                    if remaining:
                        kept = chunk[:remaining]
                        destination.write(kept)
                        written += len(kept)
        except OSError:
            self.failed.set()

    def finish(self):
        if not self.output.closed:
            self.output.close()
        self.reader.join(timeout=5)
        if self.reader.is_alive():
            self.failed.set()

    def __exit__(self, *_):
        self.finish()


def stop_worker_tree(process: subprocess.Popen) -> None:
    """Stop a worker from start_worker_tree exactly once, including its children."""
    if getattr(process, "_forma_tree_stopped", False):
        return
    process._forma_tree_stopped = True
    if os.name == "nt":
        # Closing the job works even if the immediate parent has exited.
        process._forma_job.close()
        process.wait(timeout=5)
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The parent may exit before an uncooperative child. Killing the entire
        # owned group still matters after wait() has reaped the parent.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)
