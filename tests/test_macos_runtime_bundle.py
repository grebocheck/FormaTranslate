from __future__ import annotations

import json
import os
import plistlib
import runpy
import shutil
import struct
import sys
import zipfile
from pathlib import Path

import pytest

TOOLS = runpy.run_path(str(Path(__file__).parents[1] / "scripts/macos_runtime.py"))


def native_executable(path, *, cpu=0x0100000C):
    path.parent.mkdir(parents=True, exist_ok=True)
    version = 11 << 16
    command = struct.pack("<IIIIII", 0x32, 24, 1, version, version, 0)
    path.write_bytes(struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 2, 1, len(command), 0, 0) + command)
    path.chmod(0o755)


def write_manifest(root, *, validated=True):
    files = TOOLS["inventory"](root)
    identity = TOOLS["runtime_digest"](files)
    (root / "BUNDLE.json").write_text(json.dumps({
        "schema_version": 1, "platform": "macos-arm64", "executables": TOOLS["EXECUTABLES"],
        "files": files, "runtime_sha256": identity,
    }))
    if validated:
        # Synthetic fixture attestation, never release evidence.
        (root / "VALIDATION.json").write_text(json.dumps({
            "schema_version": 1, "platform": "macos-arm64", "runtime_sha256": identity,
            "native_macos": True, "architecture": "arm64", "network_disabled": True,
            "clean_cache": True, "babeldoc_offline_warmup": True, "licenses_reviewed": True,
            "ocr_languages": sorted(TOOLS["LANGUAGES"]),
            "tesseract_languages": sorted(TOOLS["TESSERACT_LANGUAGES"]),
        }))


@pytest.fixture
def macos_runtime(tmp_path):
    root = tmp_path / "prepared"
    root.mkdir()
    for relative in TOOLS["EXECUTABLES"].values():
        native_executable(root / relative)
    support = root / "runtime/ocr/_internal"
    support.mkdir()
    native_executable(support / "library.dylib")
    if sys.platform != "win32":
        (support / "alias.dylib").symlink_to("library.dylib")
    tessdata = root / "runtime/tesseract/tessdata"
    (tessdata / "configs").mkdir(parents=True)
    (tessdata / "configs/tsv").write_text("tessedit_create_tsv 1\n")
    for language in TOOLS["TESSERACT_LANGUAGES"]:
        (tessdata / f"{language}.traineddata").write_bytes(b"synthetic traineddata")
    sets = {"docling-layout": ["layout/model.safetensors"],
            "docling-tableformer": ["tableformer/model.safetensors"],
            "rapidocr-uk": ["rapidocr/eslav_PP-OCRv5.onnx"],
            **{"rapidocr-" + language: ["rapidocr/latin_PP-OCRv6.onnx"] for language in ("en", "pl", "de")}}
    models = root / "runtime/docling-models"
    for entries in sets.values():
        for relative in entries:
            path = models / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic model")
    (models / "MODELS.json").write_text(json.dumps(sets))
    assets = root / "runtime/babeldoc/assets"
    assets.mkdir()
    (assets / ("offline_assets_" + "a" * 64 + ".zip")).write_bytes(b"synthetic offline assets")
    legal = root / "legal"
    (legal / "licenses").mkdir(parents=True)
    (legal / "licenses/LICENSE").write_text("Synthetic test license")
    sources = root / "sources"
    sources.mkdir()
    dependencies, material = [], []
    for name in sorted(TOOLS["LEGAL_COMPONENTS"]):
        dependencies.append({"name": name, "version": "1.0", "license": "Synthetic",
                             "license_files": ["licenses/LICENSE"]})
        if name in TOOLS["MODEL_COMPONENTS"]:
            if name == "tesseract-traineddata":
                model_files = [f"runtime/tesseract/tessdata/{language}.traineddata" for language in TOOLS["TESSERACT_LANGUAGES"]]
            else:
                prefix = "docling-" if name == "docling-models" else "rapidocr-"
                model_files = sorted({"runtime/docling-models/" + relative for key, entries in sets.items()
                                      if key.startswith(prefix) for relative in entries})
            material.append({"component": name, "version": "1.0", "url": "https://example.invalid/models",
                             "revision": "synthetic-commit", "model_files": model_files})
        else:
            path = sources / f"{name}-1.0.tar.gz"
            path.write_bytes(b"synthetic source archive")
            material.append({"component": name, "version": "1.0", "archive": path.name,
                             "sha256": TOOLS["digest"](path)})
    (legal / "DEPENDENCIES.json").write_text(json.dumps(dependencies))
    (legal / "SOURCES.json").write_text(json.dumps({"sources": material}))
    write_manifest(root)
    return root


def make_app(root):
    for name in ("src/forma/__init__.py", "scripts/build.py", "pyproject.toml", "uv.lock",
                 "tools/ocr/pyproject.toml", "tools/ocr/uv.lock", "tools/ocr/LICENSE"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('__version__ = "7.8.9"' if name.endswith("__init__.py") else "fixture")
    app = root / "dist/Forma-macos-arm64.app"
    artifact = app / "Contents/MacOS/Forma-macos-arm64"
    native_executable(artifact)
    (app / "Contents/Frameworks").mkdir()
    (app / "Contents/Resources").mkdir()
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": artifact.name}))
    return artifact


def test_complete_macos_build_requires_validated_workers_and_never_desktop_only(macos_runtime, tmp_path):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](tmp_path, system="Darwin", machine="arm64", runtime_bundle=macos_runtime)
    assert artifact.name == "Forma-macos-arm64"
    assert not any(str(macos_runtime / "runtime") in value for value in command)
    with pytest.raises(ValueError, match="complete embedded"):
        build["build_command"](tmp_path, system="Darwin", machine="arm64", desktop_only=True)
    with pytest.raises(ValueError, match="not prepared"):
        build["build_command"](tmp_path, system="Darwin", machine="arm64")
    (macos_runtime / "VALIDATION.json").unlink()
    TOOLS["validate_bundle"](macos_runtime, require_validation=False)
    with pytest.raises(ValueError, match="not prepared"):
        TOOLS["validate_bundle"](macos_runtime)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS symlink and executable permissions")
def test_staging_preserves_isolated_workers_links_modes_without_claiming_native_validation(macos_runtime, tmp_path):
    output = tmp_path / "staged"
    TOOLS["stage_bundle"](
        output, ocr=macos_runtime / "runtime/ocr", models=macos_runtime / "runtime/docling-models",
        tesseract=macos_runtime / "runtime/tesseract", babeldoc=macos_runtime / "runtime/babeldoc",
        babeldoc_assets=next((macos_runtime / "runtime/babeldoc/assets").glob("*.zip")),
        legal=macos_runtime / "legal", sources=macos_runtime / "sources",
    )
    TOOLS["validate_bundle"](output, require_validation=False)
    assert not (output / "VALIDATION.json").exists()
    assert os.readlink(output / "runtime/ocr/_internal/alias.dylib") == "library.dylib"
    assert (output / "runtime/ocr/forma-ocr").stat().st_mode & 0o111 == 0o111
    assert json.loads((output / "BUNDLE.json").read_text()) == json.loads((macos_runtime / "BUNDLE.json").read_text())


@pytest.mark.parametrize("changed", ["hash", "permission", "architecture", "model", "tesseract", "license", "source", "native"])
def test_complete_macos_runtime_rejects_changed_or_incomplete_payload(macos_runtime, changed):
    if changed == "hash":
        (macos_runtime / "runtime/ocr/forma-ocr").write_bytes(b"modified")
    elif changed == "permission":
        (macos_runtime / "runtime/ocr/forma-ocr").chmod(0o644)
    elif changed == "architecture":
        native_executable(macos_runtime / "runtime/ocr/forma-ocr", cpu=0x01000007)
        write_manifest(macos_runtime)
    elif changed == "model":
        path = macos_runtime / "runtime/docling-models/MODELS.json"
        values = json.loads(path.read_text())
        values["rapidocr-uk"] = values["rapidocr-en"]
        path.write_text(json.dumps(values))
        write_manifest(macos_runtime)
    elif changed == "tesseract":
        (macos_runtime / "runtime/tesseract/tessdata/configs/tsv").unlink()
        write_manifest(macos_runtime)
    elif changed == "license":
        (macos_runtime / "legal/licenses/LICENSE").write_text("")
        write_manifest(macos_runtime)
    elif changed == "source":
        (macos_runtime / "legal/SOURCES.json").write_text('{"sources": []}')
        write_manifest(macos_runtime)
    else:
        path = macos_runtime / "VALIDATION.json"
        values = json.loads(path.read_text())
        values["native_macos"] = False
        path.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        TOOLS["validate_bundle"](macos_runtime)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink checks")
def test_runtime_inventory_rejects_external_or_broken_links_and_private_data(macos_runtime, tmp_path):
    link = macos_runtime / "runtime/ocr/_internal/alias.dylib"
    link.unlink()
    private = tmp_path / "external"
    private.write_bytes(b"must not be bundled")
    link.symlink_to(private)
    with pytest.raises(ValueError, match="Non-portable"):
        TOOLS["inventory"](macos_runtime)
    link.unlink()
    link.symlink_to("missing")
    with pytest.raises(ValueError, match="Non-portable"):
        TOOLS["inventory"](macos_runtime)
    link.unlink()
    (macos_runtime / "runtime/.env").write_text("private")
    with pytest.raises(ValueError, match="private configuration"):
        TOOLS["inventory"](macos_runtime)


@pytest.mark.skipif(sys.platform == "win32", reason="Native macOS bundle layout")
def test_macos_install_and_package_preserve_worker_bytes_and_bind_complete_inventory(macos_runtime, tmp_path, monkeypatch):
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    artifact = make_app(tmp_path)
    commands = []
    monkeypatch.setitem(build["install_macos_runtime"].__globals__, "macos_minimum_version", lambda _: "26.0.0")
    before = TOOLS["inventory"](macos_runtime)
    with monkeypatch.context() as signing:
        signing.setattr(build["subprocess"], "run", lambda command, **kw: commands.append(command))
        build["install_macos_runtime"](artifact, macos_runtime)
    app = artifact.parents[2]
    assert commands[0][:4] == ["codesign", "--force", "--sign", "-"]
    assert "--deep" not in commands[0]
    assert commands[1][0:2] == ["codesign", "--verify"]
    assert (app / "Contents/Frameworks/runtimes").resolve() == app / "Contents/Resources/runtimes"
    worker = app / "Contents/Resources/runtimes/ocr/forma-ocr"
    assert worker.read_bytes() == (macos_runtime / "runtime/ocr/forma-ocr").read_bytes()
    assert TOOLS["inventory"](macos_runtime) == before
    payload = tmp_path / "legal"
    payload.mkdir()
    (payload / "LICENSE.txt").write_text("fixture")
    sources = payload.parent / "runtime-sources"
    shutil.copytree(macos_runtime / "sources", sources)
    archive = build["package_release"](tmp_path, artifact, payload, runtime_bundle=macos_runtime, separate_runtime_sources=True)
    with zipfile.ZipFile(archive) as bundle:
        metadata = json.loads(bundle.read("RELEASE.json"))
        assert metadata["runtime_mode"] == "complete-embedded" and metadata["publication_ready"] is True
        assert metadata["runtime_validation"]["native_macos"] is True
        assert metadata["runtime_sources"]["delivery"] == "separate-archive"
        assert bundle.read("Forma-macos-arm64.app/Contents/Resources/runtimes/ocr/forma-ocr") == worker.read_bytes()
    worker.chmod(0o644)
    with pytest.raises(RuntimeError, match="missing or changed"):
        build["package_release"](tmp_path, artifact, payload, runtime_bundle=macos_runtime)
    with pytest.raises(ValueError, match="complete embedded"):
        build["package_release"](tmp_path, artifact, payload)
