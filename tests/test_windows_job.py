from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from forma.documents import windows_job, worker_process


class Kernel:
    def __init__(self, failure=""):
        self.failure, self.calls = failure, []

    def CreateJobObjectW(self, security, name):
        assert security is name is None  # No inheritable job handle.
        self.calls.append("create")
        return 1

    def SetInformationJobObject(self, job, kind, pointer, size):
        limits = ctypes.cast(pointer, ctypes.POINTER(windows_job._ExtendedLimits)).contents
        assert job == 1 and kind == 9 and size == ctypes.sizeof(limits)
        assert limits.BasicLimitInformation.LimitFlags == 0x2000
        self.calls.append("configure")
        return self.failure != "configure"

    def OpenProcess(self, access, inherit, pid):
        assert access == 0x0101 and not inherit and pid == 123
        self.calls.append("open_process")
        return 2

    def AssignProcessToJobObject(self, job, process):
        assert (job, process) == (1, 2)
        self.calls.append("assign")
        return self.failure != "assign"

    def CreateToolhelp32Snapshot(self, flags, pid):
        assert flags == 4 and pid == 0
        self.calls.append("snapshot")
        return windows_job.HANDLE(-1).value if self.failure == "snapshot" else 3

    def Thread32First(self, snapshot, pointer):
        assert snapshot == 3
        entry = ctypes.cast(pointer, ctypes.POINTER(windows_job._ThreadEntry)).contents
        assert entry.dwSize == ctypes.sizeof(entry)
        entry.th32OwnerProcessID, entry.th32ThreadID = 999, 111
        return 1

    def Thread32Next(self, snapshot, pointer):
        assert snapshot == 3
        entry = ctypes.cast(pointer, ctypes.POINTER(windows_job._ThreadEntry)).contents
        entry.th32OwnerProcessID, entry.th32ThreadID = 123, 456
        return self.failure != "missing_thread"

    def OpenThread(self, access, inherit, tid):
        assert access == 2 and not inherit and tid == 456
        self.calls.append("open_thread")
        return 0 if self.failure == "open_thread" else 4

    def ResumeThread(self, thread):
        assert thread == 4
        self.calls.append("resume")
        return 0xFFFFFFFF if self.failure == "resume" else 1

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1


@pytest.mark.parametrize("failure", ["", "configure", "assign", "snapshot", "missing_thread", "open_thread", "resume"])
def test_windows_job_assigns_before_resume_and_closes_owned_handles(monkeypatch, failure):
    kernel = Kernel(failure)
    monkeypatch.setattr(windows_job, "_kernel", lambda: kernel)
    job = None
    try:
        if failure:
            with pytest.raises(OSError):
                job = windows_job.WindowsJob()
                job.assign_and_resume(123)
        else:
            job = windows_job.WindowsJob()
            job.assign_and_resume(123)
            assert kernel.calls.index("assign") < kernel.calls.index("resume")
    finally:
        if job:
            job.close()
            job.close()
    assert kernel.calls.count(("close", 1)) == 1
    if "open_process" in kernel.calls:
        assert kernel.calls.count(("close", 2)) == 1
    if "snapshot" in kernel.calls and failure != "snapshot":
        assert kernel.calls.count(("close", 3)) == 1
    if "open_thread" in kernel.calls and failure != "open_thread":
        assert kernel.calls.count(("close", 4)) == 1
    if failure in {"configure", "assign", "snapshot", "missing_thread", "open_thread"}:
        assert "resume" not in kernel.calls


@pytest.mark.parametrize("failure", ["", "launch", "assign"])
def test_windows_worker_is_suspended_until_owned_and_failure_never_leaves_it_suspended(
    monkeypatch, failure,
):
    events = []

    class Job:
        def assign_and_resume(self, pid):
            assert pid == 123
            events.append("assign")
            if failure == "assign":
                raise OSError("assignment failed")

        def close(self):
            events.append("close_job")

    class Process:
        pid = 123
        returncode = None

        def kill(self):
            events.append("kill")
            self.returncode = -1

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            assert timeout == 5
            events.append("wait")
            return self.returncode

    def launch(arguments, **kwargs):
        assert arguments == ["worker"]
        assert kwargs["creationflags"] == 0x08000004
        assert not kwargs["start_new_session"]
        events.append("launch")
        if failure == "launch":
            raise OSError("launch failed")
        return Process()

    monkeypatch.setattr(windows_job, "WindowsJob", Job)
    monkeypatch.setattr(worker_process, "os", SimpleNamespace(name="nt"))
    if failure:
        with pytest.raises(OSError):
            worker_process.start_worker_tree(launch, ["worker"], creationflags=0x08000000)
        assert events.count("close_job") == 1
        if failure == "assign":
            assert events[-2:] == ["kill", "wait"]
    else:
        process = worker_process.start_worker_tree(launch, ["worker"], creationflags=0x08000000)
        # A departed parent must not prevent the job from cleaning its children.
        process.returncode = 0
        worker_process.stop_worker_tree(process)
        worker_process.stop_worker_tree(process)
        assert events == ["launch", "assign", "close_job", "wait"]
