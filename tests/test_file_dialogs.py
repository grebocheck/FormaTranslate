from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit

from forma import file_dialogs
from forma.file_dialogs import FilePickers


def finish_dialogs(qapp, pickers):
    for dialog in list(pickers.dialogs):
        dialog.reject()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("dark", [False, True])
def test_file_picker_preserves_native_defaults_and_platform_palette(qapp, tmp_path, dark):
    previous = qapp.palette()
    palette = QPalette(previous)
    palette.setColor(QPalette.ColorRole.Base, QColor("#202020" if dark else "#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee" if dark else "#202020"))
    qapp.setPalette(palette)
    received = []
    pickers = FilePickers()
    dialog = pickers.choose(
        "Open document", received.extend, directory=str(tmp_path), multiple=True
    )
    try:
        qapp.processEvents()
        assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        assert dialog.styleSheet() == ""
        assert dialog.fileMode() == QFileDialog.FileMode.ExistingFiles
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.Base):
            assert dialog.palette().color(role) == palette.color(role)
            assert qapp.palette().color(role) == palette.color(role)
        # A native/platform helper need not expose Qt's filename widgets. Test
        # its policy and cancellation without relying on those private children.
        dialog.reject()
        qapp.processEvents()
        assert not received and not pickers.dialogs
    finally:
        finish_dialogs(qapp, pickers)
        qapp.setPalette(previous)


@pytest.mark.parametrize("multiple", [False, True])
def test_file_picker_returns_actual_selected_paths(qapp, tmp_path, monkeypatch, multiple):
    class WidgetFileDialog(QFileDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Deterministic interaction with Qt's genuine fallback in this test
            # only. Production and the native-policy test above keep OS defaults.
            self.setOption(QFileDialog.Option.DontUseNativeDialog, True)

    monkeypatch.setattr(file_dialogs, "QFileDialog", WidgetFileDialog)
    paths = [tmp_path / "file with spaces.docx"]
    if multiple:
        paths.append(tmp_path / "budget.xlsx")
    for path in paths:
        path.write_text("fixture")
    received = []
    pickers = FilePickers()
    dialog = pickers.choose(
        "Open document",
        received.extend,
        directory=str(tmp_path),
        name_filter="Documents (*.docx *.xlsx *.pdf)",
        multiple=multiple,
    )
    try:
        qapp.processEvents()
        filename = dialog.findChild(QLineEdit, "fileNameEdit")
        assert filename is not None
        filename.selectAll()
        QTest.keyClicks(filename, " ".join(f'"{path.name}"' for path in paths))
        qapp.processEvents()
        dialog.accept()
        assert received == [str(path) for path in paths]
        assert not pickers.dialogs
    finally:
        finish_dialogs(qapp, pickers)


def test_folder_picker_cancel_changes_nothing(qapp, tmp_path):
    received = []
    pickers = FilePickers()
    dialog = pickers.choose("Output", received.extend, directory=str(tmp_path), folder=True)
    try:
        assert dialog.fileMode() == QFileDialog.FileMode.Directory
        assert dialog.testOption(QFileDialog.Option.ShowDirsOnly)
        assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.reject()
        qapp.processEvents()
        assert not received and not pickers.dialogs
    finally:
        finish_dialogs(qapp, pickers)


def corrupt_utf8(value, passes=1):
    for _ in range(passes):
        value = value.encode("utf-8").decode("latin-1")
    return value


def test_restore_only_missing_bookmarks_with_proven_existing_targets(tmp_path):
    from forma.file_dialogs import restore_directory_path

    target = tmp_path / "Документи"
    target.mkdir()
    corrupted = corrupt_utf8(str(target), 8)
    assert restore_directory_path(corrupted) == str(target)
    assert restore_directory_path(str(target)) == str(target)
    assert restore_directory_path(corrupt_utf8(str(tmp_path / "Відсутня"), 2)) == corrupt_utf8(
        str(tmp_path / "Відсутня"), 2
    )
    # An unusual but real filename is authoritative, not something to rename/retarget.
    legitimate = tmp_path / corrupt_utf8("Дані")
    legitimate.mkdir()
    assert restore_directory_path(str(legitimate)) == str(legitimate)
    assert restore_directory_path("relative/Ã¤") == "relative/Ã¤"


def test_dialog_repairs_unicode_locations_without_changing_valid_or_remote_urls(qapp, tmp_path):
    from PySide6.QtCore import QUrl

    from forma.file_dialogs import restore_dialog_locations

    target = tmp_path / "Завантажене"
    target.mkdir()
    valid = QUrl.fromLocalFile(str(tmp_path))
    remote = QUrl("sftp://example.org/shared")
    corrupted = QUrl.fromLocalFile(corrupt_utf8(str(target), 2))

    class Locations:
        def __init__(self):
            self.urls = [valid, corrupted, remote]
            self.directory = corrupted

        def sidebarUrls(self):
            return self.urls

        def setSidebarUrls(self, values):
            self.urls = values

        def directoryUrl(self):
            return self.directory

        def setDirectoryUrl(self, value):
            self.directory = value

    state = Locations()
    restore_dialog_locations(state)
    assert state.urls == [valid, QUrl.fromLocalFile(str(target)), remote]
    assert state.directory.toLocalFile() == str(target)
    restore_dialog_locations(state)
    assert state.urls == [valid, QUrl.fromLocalFile(str(target)), remote]


def test_fallback_dialog_labels_follow_application_language(qapp):
    from PySide6.QtCore import QCoreApplication

    from forma.app import install_qt_translations

    translator = install_qt_translations(qapp)
    try:
        assert translator is not None
        assert QCoreApplication.translate("QFileDialog", "Look in:") != "Look in:"
        assert QCoreApplication.translate("QFileDialog", "File &name:") != "File &name:"
    finally:
        if translator:
            qapp.removeTranslator(translator)


def test_normal_and_network_bookmarks_do_not_trigger_filesystem_probes(monkeypatch):
    from pathlib import Path

    from forma.file_dialogs import restore_directory_path

    def unexpected_probe(_):
        raise AssertionError("Opening a picker must not probe ordinary/network bookmarks")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_dir", unexpected_probe)
        for path in (
            "/mnt/shared/reports",
            "/home/Документи",
            "//server/" + corrupt_utf8("Документи"),
        ):
            assert restore_directory_path(path) == path
