"""Own a Windows worker tree before its first instruction can spawn children.

Uses documented Job Objects, CREATE_SUSPENDED, Tool Help thread enumeration and
ResumeThread. The job handle is not inherited; closing it kills all descendants,
including those whose immediate parent has already exited.
"""

from __future__ import annotations

import ctypes

DWORD = ctypes.c_uint32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
CREATE_SUSPENDED = 0x00000004


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", SIZE_T),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD), ("cntUsage", DWORD), ("th32ThreadID", DWORD),
        ("th32OwnerProcessID", DWORD), ("tpBasePri", ctypes.c_int32),
        ("tpDeltaPri", ctypes.c_int32), ("dwFlags", DWORD),
    ]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": (HANDLE, (HANDLE, ctypes.c_wchar_p)),
        "SetInformationJobObject": (ctypes.c_int, (HANDLE, ctypes.c_int, HANDLE, DWORD)),
        "AssignProcessToJobObject": (ctypes.c_int, (HANDLE, HANDLE)),
        "OpenProcess": (HANDLE, (DWORD, ctypes.c_int, DWORD)),
        "CreateToolhelp32Snapshot": (HANDLE, (DWORD, DWORD)),
        "Thread32First": (ctypes.c_int, (HANDLE, ctypes.POINTER(_ThreadEntry))),
        "Thread32Next": (ctypes.c_int, (HANDLE, ctypes.POINTER(_ThreadEntry))),
        "OpenThread": (HANDLE, (DWORD, ctypes.c_int, DWORD)),
        "ResumeThread": (DWORD, (HANDLE,)),
        "CloseHandle": (ctypes.c_int, (HANDLE,)),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    return kernel


def _error(message):
    return OSError(getattr(ctypes, "get_last_error", lambda: 0)(), message)


class WindowsJob:
    def __init__(self):
        self.kernel = _kernel()
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise _error("Could not create a worker job")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            self.close()
            raise _error("Could not configure the worker job")

    def assign_and_resume(self, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE are required for job assignment.
        process = self.kernel.OpenProcess(0x0101, False, pid)
        if not process:
            raise _error("Could not open the suspended worker")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise _error("Could not assign the suspended worker to its job")
        finally:
            self.kernel.CloseHandle(process)
        self._resume(pid)

    def _resume(self, pid):
        # Popen closes CreateProcess's primary thread handle. Find that thread
        # through the documented snapshot API while the process is suspended.
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == HANDLE(-1).value or not snapshot:
            raise _error("Could not inspect the suspended worker thread")
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = self.kernel.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise _error("Could not open the suspended worker thread")
                    try:
                        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise _error("Could not resume the worker thread")
                    finally:
                        self.kernel.CloseHandle(thread)
                    return
                entry.dwSize = ctypes.sizeof(entry)
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise _error("Could not find the suspended worker thread")
        finally:
            self.kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            self.kernel.CloseHandle(handle)
