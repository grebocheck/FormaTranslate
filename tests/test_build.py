from __future__ import annotations

import importlib.metadata
import json
import runpy
import stat
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
def test_portable_build_bundles_native_credentials_without_user_data(
    tmp_path, system, machine, filename, backend
):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](
        tmp_path, system=system, machine=machine, desktop_only=system == "Windows"
    )
    assert artifact == tmp_path / "dist" / Path(filename).stem / filename
    assert "--onedir" in command and "--onefile" not in command
    assert command[command.index("--contents-directory") + 1] == "_internal"
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
    command = [sys.executable, "-m", "PyInstaller", "--onedir", "--contents-directory", "_internal", "--windowed",
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
    namespace = {"Analysis": analysis, "PYZ": lambda _: "python-code",
                 "EXE": lambda *a, **kw: SimpleNamespace(args=a, options=kw),
                 "COLLECT": lambda *a, **kw: a}
    exec(compile(spec.read_text(), str(spec), "exec"), namespace)
    frozen = namespace["a"]
    assert frozen.binaries == [desktop_binary]
    assert {name for name, _, _ in frozen.datas} == {
        "runtimes/ocr/_internal/torch_cpu.dll",
        "runtimes/babeldoc/_internal/MSVCP140.dll",
        "runtimes/docling-models/layout.onnx",
    }
    assert all(kind == "DATA" for _, _, kind in frozen.datas)
    assert namespace["exe"].options["exclude_binaries"] is True
    assert namespace["exe"].options["contents_directory"] == "_internal"
    assert namespace["coll"][1:] == (frozen.binaries, frozen.datas)


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
    runtime_files = {}
    monkeypatch.setitem(package_release.__globals__, "validate_bundle", lambda *a, **kw: {
        "files": runtime_files, "runtime_sha256": "test-runtime-digest",
    })
    for name in (
        "src/forma/__init__.py", "scripts/build.py", "pyproject.toml", "uv.lock",
        "tools/ocr/pyproject.toml", "tools/ocr/uv.lock", "tools/ocr/LICENSE",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('__version__ = "7.8.9"' if name.endswith("__init__.py") else "fixture")
    artifact = tmp_path / "dist/Forma-windows-x86_64-candidate/Forma-windows-x86_64-candidate.exe"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"fixture executable")
    support = artifact.parent / "_internal"
    worker = support / "runtimes/ocr/worker.exe"
    worker.parent.mkdir(parents=True)
    worker.write_bytes(b"exact prepared worker")
    (support / "Qt6Core.dll").write_bytes(b"desktop dependency")
    payload = tmp_path / "build/release/candidate/legal"
    payload.mkdir(parents=True)
    (payload / "LICENSE.txt").write_text("fixture license")
    source_folder = payload.parent / "runtime-sources"
    source_folder.mkdir()
    (source_folder / "worker-1.0.tar.gz").write_bytes(b"exact upstream source")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "BUNDLE.json").write_bytes(b"matching runtime manifest")
    manifest = support / "runtimes-meta/BUNDLE.json"
    manifest.parent.mkdir()
    manifest.write_bytes((runtime / "BUNDLE.json").read_bytes())
    runtime_files["runtime/ocr/worker.exe"] = {"bytes": worker.stat().st_size, "sha256": build["digest"](worker)}
    package = package_release(tmp_path, artifact, payload, runtime_bundle=runtime,
                              runtime_candidate=True, separate_runtime_sources=separate_sources)
    member = "runtime-sources/worker-1.0.tar.gz"
    with zipfile.ZipFile(package) as archive:
        metadata = json.loads(archive.read("RELEASE.json"))
        assert metadata["layout"] == "onedir" and metadata["contents_directory"] == "_internal"
        assert metadata["distribution_files"] == build["distribution_files"](artifact)
        assert metadata["distribution_sha256"] == build["distribution_digest"](metadata["distribution_files"])
        assert metadata["distribution_files"]["_internal"] == {"directory": True, "mode": 0o755}
        assert archive.read(artifact.name) == artifact.read_bytes()
        assert archive.read("_internal/runtimes/ocr/worker.exe") == b"exact prepared worker"
        assert archive.read("_internal/Qt6Core.dll") == b"desktop dependency"
        assert "LICENSE.txt" in archive.namelist()
        if separate_sources:
            assert member not in archive.namelist()
            delivery = metadata["runtime_sources"]
            companion = tmp_path / "dist" / delivery["archive"]
            assert build["digest"](companion) == delivery["sha256"]
            assert companion.stat().st_size == delivery["bytes"]
            with zipfile.ZipFile(companion) as sources:
                assert sources.read(member) == b"exact upstream source"
        else:
            assert archive.read(member) == b"exact upstream source"
            assert metadata["runtime_sources"]["delivery"] == "included"
    accepted_package = build["digest"](package)
    if sys.platform != "win32":
        # Windows inventory is identical when audited from a POSIX host with
        # different directory permissions.
        before = build["distribution_files"](artifact)
        support.chmod(0o700)
        assert build["distribution_files"](artifact) == before
    # Both delivery modes share one extracted namespace. A source parent must
    # not overwrite an application file, including in the companion ZIP case.
    source_parent = artifact.parent / "runtime-sources"
    source_parent.write_bytes(b"application file")
    with pytest.raises(ValueError, match="collide"):
        package_release(tmp_path, artifact, payload, runtime_bundle=runtime,
                        runtime_candidate=True, separate_runtime_sources=separate_sources)
    source_parent.unlink()
    assert build["digest"](package) == accepted_package
    if separate_sources:
        unexpected = worker.with_name("unlisted.dll")
        unexpected.write_bytes(b"not in the prepared inventory")
        with pytest.raises(RuntimeError, match="unexpected additions"):
            package_release(tmp_path, artifact, payload, runtime_bundle=runtime, runtime_candidate=True)
        unexpected.unlink()
        worker.write_bytes(b"changed worker")
        with pytest.raises(RuntimeError, match="missing or changed"):
            package_release(tmp_path, artifact, payload, runtime_bundle=runtime, runtime_candidate=True)
        worker.unlink()
        with pytest.raises(RuntimeError, match="missing or changed"):
            package_release(tmp_path, artifact, payload, runtime_bundle=runtime, runtime_candidate=True)
        assert build["digest"](package) == accepted_package


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX portable ZIP links and executable modes")
def test_portable_linux_zip_preserves_links_permissions_and_rejects_external_targets(tmp_path):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    for name in ("src/forma/__init__.py", "scripts/build.py", "pyproject.toml", "uv.lock"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('__version__ = "7.8.9"' if name.endswith("__init__.py") else "fixture")
    artifact = tmp_path / "dist/Forma-linux-x86_64/Forma-linux-x86_64"
    library = artifact.parent / "_internal/lib/sample.so.1"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"\x7fELF\x00GLIBC_2.31")
    artifact.write_bytes(b"fixture executable")
    artifact.chmod(0o755)
    link = artifact.parent / "_internal/sample.so"
    link.symlink_to("lib/sample.so.1")
    directory_link = artifact.parent / "_internal/alias"
    directory_link.symlink_to("lib", target_is_directory=True)
    empty = artifact.parent / "_internal/empty"
    empty.mkdir(mode=0o750)
    empty.chmod(0o750)
    empty_link = artifact.parent / "_internal/empty-alias"
    empty_link.symlink_to("empty", target_is_directory=True)
    payload = tmp_path / "legal"
    payload.mkdir()
    package = build["package_release"](tmp_path, artifact, payload)
    with zipfile.ZipFile(package) as archive:
        metadata = json.loads(archive.read("RELEASE.json"))
        assert metadata["minimum_glibc"] == "2.31"
        assert archive.getinfo(artifact.name).external_attr >> 16 & 0o111 == 0o111
        for name, target in (("_internal/sample.so", b"lib/sample.so.1"), ("_internal/alias", b"lib"),
                             ("_internal/empty-alias", b"empty")):
            assert stat.S_ISLNK(archive.getinfo(name).external_attr >> 16)
            assert archive.read(name) == target
        assert metadata["distribution_files"]["_internal/empty"] == {"directory": True, "mode": 0o750}
        empty_info = archive.getinfo("_internal/empty/")
        assert empty_info.is_dir() and stat.S_ISDIR(empty_info.external_attr >> 16)
        assert stat.S_IMODE(empty_info.external_attr >> 16) == 0o750
        assert archive.read("_internal/lib/sample.so.1") == library.read_bytes()
    # A regular application file or directory cannot shadow release documents.
    docs = payload / "docs"
    docs.mkdir()
    (docs / "BUILDING.md").write_text("rebuild instructions")
    blocker = artifact.parent / "docs"
    blocker.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="collide"):
        build["package_release"](tmp_path, artifact, payload)
    blocker.unlink()
    blocker = artifact.parent / "LICENSE.txt"
    blocker.mkdir()
    (blocker / "application-data").write_bytes(b"directory conflicts with release file")
    (payload / "LICENSE.txt").write_text("license")
    with pytest.raises(ValueError, match="collide"):
        build["package_release"](tmp_path, artifact, payload)
    (blocker / "application-data").unlink()
    blocker.rmdir()
    link.unlink()
    external = tmp_path / "private-library"
    external.write_bytes(b"must never be packaged")
    link.symlink_to(external)
    with pytest.raises(ValueError, match="Non-portable or broken"):
        build["package_release"](tmp_path, artifact, payload)
    link.unlink()
    link.symlink_to("missing.so")
    with pytest.raises(ValueError, match="Non-portable or broken"):
        build["package_release"](tmp_path, artifact, payload)
