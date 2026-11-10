from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("system", "machine", "filename", "backend"),
    [
        ("Windows", "AMD64", "Forma-windows-x86_64.exe", "keyring.backends.Windows"),
        ("Linux", "x86_64", "Forma-linux-x86_64", "keyring.backends.SecretService"),
    ],
)
def test_onefile_build_bundles_native_credentials_without_user_data(
    tmp_path, system, machine, filename, backend
):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](tmp_path, system=system, machine=machine)
    assert artifact == tmp_path / "dist" / filename
    assert "--onefile" in command and "--onedir" not in command
    assert command[command.index("--hidden-import") + 1] == backend
    # Explicit payloads contain UI/locales and generated legal notices only.
    payloads = [command[i + 1] for i, value in enumerate(command) if value == "--add-data"]
    name = Path(filename).stem
    assert payloads == [f"{tmp_path / 'src/forma/ui'}:forma/ui",
                        f"{tmp_path / 'src/forma/locales'}:forma/locales",
                        f"{tmp_path / 'build/release' / name / 'legal'}:legal"]
    assert not any(".env" in value for value in command)
    assert "PySide6.QtWidgets" not in command  # Never exclude native file dialogs.
    assert artifact != tmp_path / "dist" / "Forma"  # Preserve old onedir releases.
    assert "--additional-hooks-dir" in command
    for module in ("QtCharts", "QtGraphs", "QtQuick3D", "QtVirtualKeyboard"):
        assert "PySide6." + module in command
    assert ("--version-file" in command) == (system == "Windows")


def test_version_comes_from_package_without_executing_it(tmp_path):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    package = tmp_path / "src/forma"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "7.8.9"\nraise RuntimeError()\n')
    assert build["release_version"](tmp_path) == "7.8.9"


def test_release_refuses_stale_corresponding_source_manifest(tmp_path, monkeypatch):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party/SOURCES.json").write_text(json.dumps({"sources": [
        {"component": "Qt", "version": "6.12.0"},
        {"component": "PySide6", "version": "6.12.0"},
    ]}))
    monkeypatch.setattr("importlib.metadata.version", lambda _: "6.13.0")
    with pytest.raises(ValueError, match="corresponding source archives"):
        build["prepare_release_materials"](tmp_path, tmp_path / "dist/Forma-linux-x86_64")


def test_qml_hook_keeps_controls_and_excludes_unrelated_products(tmp_path, monkeypatch):
    files = [
        "QtQml/qmldir", "QtQml/Models/modelsplugin.dll", "QtQuick/qtquick2plugin.dll",
        "QtQuick/Controls/Basic/Button.qml", "QtQuick/Layouts/qquicklayoutsplugin.dll",
        "QtQuick/Templates/qmldir", "QtQuick/Controls/designer/ButtonSpecifics.qml",
        "QtGraphs/graphsplugin.dll", "QtCharts/qtchartsqml2.dll",
        "QtQuick3D/qtquick3dplugin.dll", "QtQuick/Timeline/qtquicktimelineplugin.dll",
        "QtQuick/VirtualKeyboard/qtquickvirtualkeyboardplugin.dll", "QtWebEngine/qmldir",
    ]
    entries = []
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        entries.append((str(path), str(path.parent)))
    qt = SimpleNamespace(
        add_qt6_dependencies=lambda _: ([], [(str(tmp_path / "qmltooling/libqmldbg_quick3dprofiler.so"), ".")], []),
        pyside6_library_info=SimpleNamespace(
            location={"QmlImportsPath": str(tmp_path)},
            collect_qtqml_files=lambda: ([], entries),
        ),
    )
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks.qt", qt)
    hook = runpy.run_path(str(Path(__file__).parents[1] / "scripts/pyinstaller_hooks/hook-PySide6.QtQml.py"))
    included = {Path(source).relative_to(tmp_path).as_posix() for source, _ in hook["datas"]}
    assert included == set(files[:6])
    assert hook["binaries"] == []


def test_gui_hook_preserves_native_dialogs_without_virtual_keyboard_or_pdf(tmp_path, monkeypatch):
    files = ["platformthemes/libqgtk3.so", "platforms/qwindows.dll", "imageformats/qsvg.dll",
             "platforminputcontexts/libqtvirtualkeyboardplugin.so", "imageformats/libqpdf.so"]
    entries = [(str(tmp_path / name), ".") for name in files]
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks.qt", SimpleNamespace(
        add_qt6_dependencies=lambda _: ([], entries, []),
    ))
    hook = runpy.run_path(str(Path(__file__).parents[1] / "scripts/pyinstaller_hooks/hook-PySide6.QtGui.py"))
    assert hook["binaries"] == entries[:3]
