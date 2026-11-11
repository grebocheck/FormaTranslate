from __future__ import annotations

import importlib.metadata
import json
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("system", "machine", "filename", "backend"),
    [
        ("Windows", "AMD64", "Forma-windows-x86_64-desktop-only.exe", "keyring.backends.Windows"),
        ("Linux", "x86_64", "Forma-linux-x86_64", "keyring.backends.SecretService"),
    ],
)
def test_onefile_build_bundles_native_credentials_without_user_data(
    tmp_path, system, machine, filename, backend
):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](
        tmp_path, system=system, machine=machine, desktop_only=system == "Windows"
    )
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


def test_build_cli_help_does_not_depend_on_cwd_or_pythonpath(tmp_path):
    script = Path(__file__).parents[1] / "scripts/build.py"
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            check=True, capture_output=True, text=True)
    assert "--runtime-bundle" in result.stdout
    assert "--desktop-only" in result.stdout


def test_windows_full_build_refuses_missing_prepared_runtimes(tmp_path):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    with pytest.raises(ValueError, match="not prepared"):
        build["build_command"](tmp_path, system="Windows", machine="AMD64")


def test_windows_release_packaging_requires_embedded_workers(tmp_path, monkeypatch):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: SimpleNamespace(toc={}))
    package = tmp_path / "src/forma"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "7.8.9"\n')
    with pytest.raises(ValueError, match="complete embedded runtime"):
        build["package_release"](tmp_path, tmp_path / "dist/Forma-windows-x86_64.exe", tmp_path / "legal")


def test_worker_dlls_enter_spec_after_analysis_without_changing_desktop_dependencies(
    tmp_path, monkeypatch
):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    runtime = tmp_path / "prepared"
    for name in ("ocr/_internal/torch_cpu.dll", "babeldoc/_internal/MSVCP140.dll", "docling-models/layout.onnx"):
        path = runtime / "runtime" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"prepared runtime file")
    command = [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed",
               "--noconfirm", "--clean", "--name", "isolated-workers",
               "--specpath", str(tmp_path / "specs"), "--distpath", str(tmp_path / "dist"),
               "--workpath", str(tmp_path / "work"), str(Path(__file__).parents[1] / "scripts/desktop.py")]
    generated = build["runtime_build_command"](command, runtime)
    spec = Path(generated[-1])
    assert generated[:3] == command[:3]
    assert "--add-data" not in generated
    analyzed = False
    desktop_binary = ("Qt6Core.dll", "desktop-Qt6Core.dll", "BINARY")

    def analysis(scripts, **options):
        nonlocal analyzed
        assert not options["datas"]  # Workers must never seed dependency discovery.
        analyzed = True
        return SimpleNamespace(scripts=scripts, pure=[], binaries=[desktop_binary], datas=[])

    def tree(path, *, prefix, typecode):
        assert analyzed
        return [(prefix + "/" + item.relative_to(path).as_posix(), str(item), typecode)
                for item in sorted(Path(path).rglob("*")) if item.is_file()]

    monkeypatch.setattr("PyInstaller.building.datastruct.Tree", tree)
    namespace = {"Analysis": analysis, "PYZ": lambda _: "python-code", "EXE": lambda *a, **kw: a}
    exec(compile(spec.read_text(), str(spec), "exec"), namespace)
    frozen = namespace["a"]
    assert frozen.binaries == [desktop_binary]
    assert {name for name, _, _ in frozen.datas} == {
        "runtimes/ocr/_internal/torch_cpu.dll",
        "runtimes/babeldoc/_internal/MSVCP140.dll",
        "runtimes/docling-models/layout.onnx",
    }
    assert all(kind == "DATA" for _, _, kind in frozen.datas)


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


def test_release_collects_standalone_python_stdlib_license(tmp_path, monkeypatch):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    prepare = build["prepare_release_materials"]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "docs/BUILDING.md", "src/forma/__init__.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('__version__ = "7.8.9"' if name.endswith("__init__.py") else "fixture")
    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party/SOURCES.json").write_text(json.dumps({"sources": [
        {"component": "Qt", "version": "6.12.0"},
        {"component": "PySide6", "version": "6.12.0"},
    ]}))
    stdlib = tmp_path / "standalone/lib/python3.12"
    stdlib.mkdir(parents=True)
    (stdlib / "LICENSE.txt").write_text("Exact standalone Python license")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "standalone"))
    monkeypatch.setattr("sysconfig.get_path", lambda _: str(stdlib))
    monkeypatch.setattr("importlib.metadata.version", lambda _: "6.12.0")
    monkeypatch.setitem(prepare.__globals__, "_dependency_names", lambda _: [])
    payload = prepare(tmp_path, tmp_path / "dist/Forma-linux-x86_64")
    assert (payload / "licenses/Python/LICENSE.txt").read_text() == "Exact standalone Python license"


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


@pytest.mark.parametrize("optional_installed", [False, True])
def test_release_inventory_covers_optional_hook_imports(tmp_path, monkeypatch, optional_installed):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["pypdf"]\n')

    optional = {"Pillow", "pytest", "Pygments"}

    def distribution(name):
        if name in optional and not optional_installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return SimpleNamespace(metadata={"Name": name}, requires=[])

    monkeypatch.setattr("importlib.metadata.distribution", distribution)
    names = build["_dependency_names"](tmp_path)
    assert {"pypdf", "pyinstaller"} <= set(names)
    assert set(names) & optional == (optional if optional_installed else set())


def test_gui_hook_preserves_native_dialogs_without_virtual_keyboard_or_pdf(tmp_path, monkeypatch):
    files = ["platformthemes/libqgtk3.so", "platforms/qwindows.dll", "imageformats/qsvg.dll",
             "platforminputcontexts/libqtvirtualkeyboardplugin.so", "imageformats/libqpdf.so"]
    entries = [(str(tmp_path / name), ".") for name in files]
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks.qt", SimpleNamespace(
        add_qt6_dependencies=lambda _: ([], entries, []),
    ))
    hook = runpy.run_path(str(Path(__file__).parents[1] / "scripts/pyinstaller_hooks/hook-PySide6.QtGui.py"))
    assert hook["binaries"] == entries[:3]


@pytest.mark.parametrize("separate_sources", [False, True])
def test_release_delivers_matching_runtime_sources(tmp_path, monkeypatch, separate_sources):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    package_release = build["package_release"]
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: SimpleNamespace(toc={}))
    monkeypatch.setitem(package_release.__globals__, "validate_bundle", lambda *a, **kw: {
        "files": {}, "runtime_sha256": "test-runtime-digest",
    })
    for name in (
        "src/forma/__init__.py", "scripts/build.py", "pyproject.toml", "uv.lock",
        "tools/ocr/pyproject.toml", "tools/ocr/uv.lock", "tools/ocr/LICENSE",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('__version__ = "7.8.9"' if name.endswith("__init__.py") else "fixture")
    artifact = tmp_path / "dist/Forma-windows-x86_64-candidate.exe"
    artifact.parent.mkdir()
    artifact.write_bytes(b"fixture executable")
    payload = tmp_path / "build/release/candidate/legal"
    payload.mkdir(parents=True)
    (payload / "LICENSE.txt").write_text("fixture license")
    source_folder = payload.parent / "runtime-sources"
    source_folder.mkdir()
    (source_folder / "worker-1.0.tar.gz").write_bytes(b"exact upstream source")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    package = package_release(tmp_path, artifact, payload, runtime_bundle=runtime,
                              runtime_candidate=True, separate_runtime_sources=separate_sources)
    member = "runtime-sources/worker-1.0.tar.gz"
    with zipfile.ZipFile(package) as archive:
        metadata = json.loads(archive.read("RELEASE.json"))
        assert "LICENSE.txt" in archive.namelist()
        if separate_sources:
            assert member not in archive.namelist()
            delivery = metadata["runtime_sources"]
            companion = artifact.parent / delivery["archive"]
            assert build["digest"](companion) == delivery["sha256"]
            assert companion.stat().st_size == delivery["bytes"]
            with zipfile.ZipFile(companion) as sources:
                assert sources.read(member) == b"exact upstream source"
        else:
            assert archive.read(member) == b"exact upstream source"
            assert metadata["runtime_sources"]["delivery"] == "included"
