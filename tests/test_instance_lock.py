from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from forma.persistence.instance import application_lock
from forma.persistence.locking import FileLockBusy, file_lock
from forma.settings.config import data_directory, save_preferences


def test_lock_excludes_other_processes_and_keeps_independent_files_available(tmp_path):
    path = tmp_path / "shared.lock"
    with file_lock(path):
        with pytest.raises(FileLockBusy):
            with file_lock(path, timeout=0.05):
                pytest.fail("Acquired another writer's lock")
        with file_lock(tmp_path / "independent.lock"):
            pass
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from forma.persistence.locking import file_lock, FileLockBusy\n"
            "try:\n"
            "    with file_lock(Path(sys.argv[1])): pass\n"
            "except FileLockBusy:\n"
            "    sys.exit(3)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=10)
        assert result.returncode == 3
    # The empty file remains; it must not be mistaken for an active lock.
    assert path.is_file()
    with file_lock(path):
        pass


def test_process_death_releases_lock_without_removing_file(tmp_path):
    path, ready = tmp_path / "crash.lock", tmp_path / "ready"
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from forma.persistence.locking import file_lock\n"
        "with file_lock(Path(sys.argv[1])):\n"
        "    Path(sys.argv[2]).touch()\n"
        "    time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(path), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Lock owner did not start"
        with pytest.raises(FileLockBusy):
            with file_lock(path):
                pytest.fail("Acquired a live process's lock")
        process.kill()
        process.wait(timeout=10)
        assert path.is_file()
        with file_lock(path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def test_lock_released_on_exception_and_storage_errors_are_not_contention(tmp_path):
    path = tmp_path / "state.lock"
    with pytest.raises(RuntimeError, match="failed work"):
        with file_lock(path):
            raise RuntimeError("failed work")
    with file_lock(path):
        pass
    with pytest.raises(FileNotFoundError):
        with file_lock(tmp_path / "missing" / "state.lock"):
            pytest.fail("Opened a missing directory")


def test_desktop_owns_lock_before_loading_state_and_releases_on_exit(monkeypatch):
    from forma import app

    def run(files, screenshot):
        assert files == ["document.docx"] and screenshot is None
        with pytest.raises(FileLockBusy):
            with file_lock(data_directory() / "application.lock"):
                pytest.fail("Desktop did not hold the data lock")
        return 17

    monkeypatch.setattr(app, "_run_application", run)
    assert app.main(["document.docx"]) == 17
    with application_lock():
        pass


def test_second_desktop_exits_before_backend_initialization(monkeypatch):
    from forma import app

    def unexpected(*args):
        pytest.fail("Second desktop loaded application state")

    errors = []
    monkeypatch.setattr(app, "_run_application", unexpected)
    monkeypatch.setattr(app, "_startup_error", lambda message: errors.append(message) or 1)
    with application_lock():
        assert app.main() == 1
    assert len(errors) == 1 and "Інший екземпляр" in errors[0]


def test_startup_error_uses_saved_language_without_loading_credentials(qapp, monkeypatch):
    from forma import app

    monkeypatch.delenv("FORMA_UI_LANGUAGE", raising=False)
    save_preferences({"ui_language": "en"})
    messages = []
    monkeypatch.setattr(app.QMessageBox, "warning", lambda parent, title, text: messages.append(text))
    with application_lock():
        assert app.main() == 1
    assert len(messages) == 1 and "Another instance" in messages[0]


def test_cli_translation_refuses_shared_data_but_read_only_inspection_works(docx_file):
    command = [sys.executable, "-m", "forma"]
    with application_lock():
        result = subprocess.run(
            [*command, "translate", str(docx_file), "--env", str(docx_file.parent / "missing.env")],
            capture_output=True, text=True, timeout=15, env=dict(os.environ),
        )
        assert result.returncode == 1
        assert "Інший екземпляр" in result.stderr
        inspected = subprocess.run(
            [*command, "inspect", str(docx_file)], capture_output=True, text=True, timeout=15,
        )
    assert inspected.returncode == 0
    assert '"segments"' in inspected.stdout
