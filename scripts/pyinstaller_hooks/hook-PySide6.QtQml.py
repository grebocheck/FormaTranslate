"""Collect Forma's QML runtime, without unrelated optional Qt products.

PyInstaller's default hook collects every installed QML plugin, including
GPL-only modules that Forma does not use. Keep only the imports used by our
QtQuick/Controls UI and their runtime dependencies. QtWidgets native dialogs
are collected separately by PyInstaller's ordinary hook.
"""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# Debug/profiler plugins are not used by the release UI. In particular the
# Quick3D profiler otherwise brings GPL-only Quick3DUtils back into the bundle.
binaries = [entry for entry in binaries if "qmltooling" not in Path(entry[0]).parts]
datas = [entry for entry in datas if "qmltooling" not in Path(entry[0]).parts]
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()
qml_root = Path(pyside6_library_info.location["QmlImportsPath"]).resolve()


def keep_qml(source):
    parts = Path(source).resolve().relative_to(qml_root).parts
    if "designer" in parts:
        return False
    if parts[0] == "QtQml":
        return (len(parts) <= 2 and not Path(source).is_dir()) or parts[1] in {"Models", "WorkerScript"}
    if parts[0] == "QtQuick":
        if len(parts) <= 2 and not Path(source).is_dir():
            return True
        return parts[1] in {
            "Controls", "Layouts", "Templates", "Window", "Effects", "NativeStyle",
        } or (parts[1] == "Shapes" and len(parts) <= 3 and not Path(source).is_dir())
    return False


binaries += [entry for entry in qml_binaries if keep_qml(entry[0])]
datas += [entry for entry in qml_datas if keep_qml(entry[0])]
