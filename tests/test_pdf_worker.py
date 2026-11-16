from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from forma.core.models import Cancelled, FormaError
from forma.documents import pdf
from forma.providers.client import JobControl
from forma.settings.config import Settings


def _running(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # An orphan may briefly remain as an already dead zombie until init reaps it.
    status = pdf.Path(f"/proc/{pid}/stat")
    try:
        return not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


@pytest.mark.parametrize("failure", ["cancel", "log_limit"])
def test_pdf_stops_real_worker_tree_and_bounds_log(tmp_path, monkeypatch, failure):
    pidfile = tmp_path / "processes.json"
    logfile = tmp_path / "engine.log"
    control = JobControl(2)
    processes = []
    start = pdf.start_worker
    child_code = (
        "import json, os, signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"marker = Path({str(pidfile)!r})\n"
        "temporary = marker.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps([os.getppid(), os.getpid()]))\n"
        "temporary.replace(marker)\n"
        "time.sleep(60)\n"
    )
    parent_code = (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"while not Path({str(pidfile)!r}).exists(): time.sleep(0.01)\n"
        + ("os.write(1, b'x' * 131072)\n" if failure == "log_limit" else "")
        + "time.sleep(60)\n"
    )

    def launch(*args, **kwargs):
        process = start(*args, **kwargs)
        processes.append(process)
        return process

    def cancel_after_start():
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if failure == "log_limit" and pidfile.exists():
            # The cap should abort this run; cancellation is only a test failsafe.
            control.cancelled.wait(5)
        control.cancelled.set()

    monkeypatch.setattr(pdf, "start_worker", launch)
    monkeypatch.setattr(pdf, "MAX_LOG_BYTES", 4096)
    watcher = threading.Thread(target=cancel_after_start, daemon=True)
    watcher.start()
    try:
        expected = Cancelled if failure == "cancel" else FormaError
        message = "зупинено" if failure == "cancel" else "забагато діагностики"
        with pytest.raises(expected, match=message):
            pdf._run_babeldoc(
                [sys.executable, "-c", parent_code], logfile,
                Settings(model="test"), control, lambda *_: None, 2,
            )
        pids = json.loads(pidfile.read_text())
        deadline = time.monotonic() + 3
        while any(_running(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(_running(pid) for pid in pids)
        assert logfile.stat().st_size <= 4096
        control.cancelled.clear()
        with control.request_slots(2):
            pass
    finally:
        control.cancelled.set()
        watcher.join(timeout=1)
        for process in processes:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif pidfile.exists():
                for pid in json.loads(pidfile.read_text()):
                    if _running(pid):
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       capture_output=True, timeout=5, check=False)
            process.wait(timeout=5)


@pytest.mark.parametrize("state", ["silent", "paused", "active_request", "completed_requests", "resumed"])
def test_pdf_watchdog_excludes_pause_and_provider_activity(tmp_path, monkeypatch, state):
    control = JobControl()
    clock = [0.0]
    stopped = []

    class Process:
        returncode = 0
        polls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            self.polls += 1
            if state == "resumed":
                control.paused.set() if self.polls < 3 else control.paused.clear()
                clock[0] += 1 if self.polls == 3 else 40
            else:
                clock[0] += 40
                if state == "paused":
                    control.paused.set()
            return None if self.polls <= 4 else 0

    instance = Process()
    api = SimpleNamespace(
        check_error=lambda: None,
        activity=lambda: (
            instance.polls if state == "completed_requests" else 0,
            int(state == "active_request"),
        ),
    )
    monkeypatch.setattr(pdf, "start_worker_tree", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(pdf, "stop_worker_tree", lambda process: stopped.append(process))
    monkeypatch.setattr(pdf, "INACTIVITY_SECONDS", 30)
    monkeypatch.setattr(pdf, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(control.cancelled, "wait", lambda _: False)

    def run():
        pdf._run_babeldoc(
            ["fake-worker"], tmp_path / "engine.log", Settings(model="test"),
            control, lambda *_: None, 1, api,
        )

    if state in {"silent", "resumed"}:
        with pytest.raises(FormaError, match="30 хвилин"):
            run()
    else:
        run()
    assert stopped


