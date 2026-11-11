"""OS file pickers, independent of the Qt Quick application's colors and controls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QFileDialog

from forma.localization.catalog import translate_ui_message as tr


def restore_directory_path(path: str) -> str:
    """Recover stale UTF-8-as-Latin-1 bookmarks only when a real directory proves it.

    QFileDialog shares QtProject bookmarks with other Qt programs. Never rename
    folders, guess at nonexistent destinations, or change an existing valid path.
    """
    if not path or path.startswith(("//", "\\\\")) or not Path(path).is_absolute():
        return path

    def exists(value):
        try:
            return Path(value).is_dir()
        except (OSError, ValueError):
            return False

    try:
        first_decode = path.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return path
    # Ordinary paths need no filesystem probe (especially mapped/network drives).
    if len(first_decode) >= len(path) or exists(path):
        return path
    candidate = path
    # A corrupted shared setting can have been encoded more than once. Each
    # strict reversal must shrink it; malformed/ambiguous inputs stay untouched.
    for _ in range(12):
        try:
            decoded = candidate.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if len(decoded) >= len(candidate):
            break
        if exists(decoded):
            return decoded
        candidate = decoded
    return path


def restore_dialog_locations(dialog: QFileDialog) -> None:
    original = dialog.sidebarUrls()
    restored = []
    for url in original:
        if url.isLocalFile() and not url.host():
            path = url.toLocalFile()
            repaired = restore_directory_path(path)
            url = QUrl.fromLocalFile(repaired) if repaired != path else url
        restored.append(url)
    if restored != original:
        dialog.setSidebarUrls(restored)
    directory = dialog.directoryUrl()
    if directory.isLocalFile() and not directory.host():
        path = directory.toLocalFile()
        repaired = restore_directory_path(path)
        if repaired != path:
            dialog.setDirectoryUrl(QUrl.fromLocalFile(repaired))


class FilePickers(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.window: QWindow | None = None
        self.dialogs: list[QFileDialog] = []

    def choose(
        self,
        title: str,
        callback: Callable[[list[str]], None],
        *,
        directory: str = "",
        name_filter: str = "",
        multiple: bool = False,
        folder: bool = False,
        save: bool = False,
    ) -> QFileDialog:
        dialog = QFileDialog(None, tr(title), directory, tr(name_filter))
        restore_dialog_locations(dialog)
        # Keep platform defaults: no stylesheet, palette, custom delegates, or
        # DontUseNativeDialog. Qt's widget fallback also retains the OS palette.
        dialog.setAcceptMode(
            QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen
        )
        dialog.setFileMode(
            QFileDialog.FileMode.Directory
            if folder
            else QFileDialog.FileMode.AnyFile
            if save
            else QFileDialog.FileMode.ExistingFiles
            if multiple
            else QFileDialog.FileMode.ExistingFile
        )
        if folder:
            dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        if save:
            dialog.setDefaultSuffix("png")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if self.window is not None:
            dialog.winId()
            dialog.windowHandle().setTransientParent(self.window)
        self.dialogs.append(dialog)

        def finished(result):
            try:
                if result == QFileDialog.DialogCode.Accepted:
                    selected = dialog.selectedFiles()
                    if selected:
                        callback(selected)
            finally:
                self.dialogs.remove(dialog)
                dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()
        return dialog
