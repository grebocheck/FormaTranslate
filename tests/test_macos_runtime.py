"""Desktop tool discovery with Finder's restricted executable search path."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from forma import app
from forma.documents import preview_office
from forma.documents.bundled_runtime import worker_environment
from forma.translation.image_geometry import _configuration


@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX executable")
def test_finder_launch_discovers_optional_ocr_and_worker_inherits_path(tmp_path, monkeypatch):
    installed = tmp_path / "homebrew/bin"
    installed.mkdir(parents=True)
    executable = installed / "tesseract"
    executable.write_text(f"#!{sys.executable}\nprint('offline OCR fixture')\n")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(app, "_MACOS_TOOL_DIRECTORIES", (str(installed),))
    monkeypatch.setenv("PATH", str(tmp_path / "system-bin"))
    monkeypatch.setattr("forma.translation.image_geometry.runtime_root", lambda: None)
    assert not _configuration()[0]
    app._configure_tool_path()
    found, environment = _configuration()
    assert found == str(executable)
    result = subprocess.run(
        [sys.executable, "-c", "import subprocess; subprocess.run(['tesseract'], check=True)"],
        env=worker_environment(sys.executable, environment),
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "offline OCR fixture"
    # A shell/user-selected tool remains ahead of the optional fallback.
    preferred = tmp_path / "custom-bin"
    preferred.mkdir()
    chosen = preferred / "tesseract"
    chosen.write_bytes(executable.read_bytes())
    chosen.chmod(0o755)
    monkeypatch.setenv("PATH", str(preferred) + os.pathsep + environment["PATH"])
    app._configure_tool_path()
    app._configure_tool_path()
    assert _configuration()[0] == str(chosen)
    assert os.environ["PATH"].split(os.pathsep).count(str(installed)) == 1


def test_macos_finds_libreoffice_installed_for_current_user(tmp_path, monkeypatch):
    executable = tmp_path / "Applications/LibreOffice.app/Contents/MacOS/soffice"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(preview_office.shutil, "which", lambda _: None)
    monkeypatch.setattr(preview_office.Path, "home", lambda: tmp_path)
    is_file = preview_office.Path.is_file
    monkeypatch.setattr(preview_office.Path, "is_file", lambda path: path == executable and is_file(path))
    assert preview_office.find_libreoffice() == str(executable)
