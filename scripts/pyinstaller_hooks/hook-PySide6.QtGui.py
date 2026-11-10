"""Keep native GUI integration while excluding unused optional Qt plugins."""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


def keep_gui_plugin(source):
    path = Path(source)
    name = path.name.lower()
    if "platforminputcontexts" in path.parts and "virtualkeyboard" in name:
        return False
    # Forma renders PDFs through the external document engine, never QImage.
    return not ("imageformats" in path.parts and name.startswith(("qpdf.", "libqpdf.")))


binaries = [entry for entry in binaries if keep_gui_plugin(entry[0])]
datas = [entry for entry in datas if keep_gui_plugin(entry[0])]
