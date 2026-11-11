from __future__ import annotations

import json
import runpy
import struct
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from forma.documents import bundled_runtime
from forma.documents.ocr import find_scan_converter
from forma.documents.pdf import find_babeldoc

TOOLS = runpy.run_path(str(Path(__file__).parents[1] / "scripts/windows_runtime.py"))


def windows_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(64)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    path.write_bytes(header + b"PE\x00\x00\x64\x86")


@pytest.fixture
def runtime_bundle(tmp_path):
    root = tmp_path / "prepared"
    root.mkdir()
    for value in TOOLS["EXECUTABLES"].values():
        windows_executable(root / value)
    tessdata = root / "runtime/tesseract/tessdata"
    (tessdata / "configs").mkdir(parents=True)
    (tessdata / "configs/tsv").write_text("tessedit_create_tsv 1\n")
    for code in ("eng", "ukr", "pol", "deu"):
        (tessdata / f"{code}.traineddata").write_bytes(b"synthetic test data")
    models = root / "runtime/docling-models"
    models.mkdir()
    sets = {
        "docling-layout": ["layout/model.safetensors"],
        "docling-tableformer": ["tableformer/model.safetensors"],
        "rapidocr-uk": ["rapidocr/eslav_PP-OCRv5.onnx"],
        **{"rapidocr-" + language: ["rapidocr/latin_PP-OCRv6.onnx"] for language in ("en", "pl", "de")},
    }
    for values in sets.values():
        for relative in values:
            path = models / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic model")
    (models / "MODELS.json").write_text(json.dumps(sets))
    assets = root / "runtime/babeldoc/assets"
    assets.mkdir()
    (assets / ("offline_assets_" + "a" * 64 + ".zip")).write_bytes(b"synthetic package")
    legal = root / "legal"
    (legal / "licenses").mkdir(parents=True)
    (legal / "licenses/LICENSE").write_text("Synthetic license for structural tests only")
    sources = root / "sources"
    sources.mkdir()
    dependencies, material = [], []
    for name in sorted(TOOLS["LEGAL_COMPONENTS"]):
        dependencies.append({"name": name, "version": "1.0", "license": "Synthetic",
                             "license_files": ["licenses/LICENSE"]})
        if name in TOOLS["MODEL_COMPONENTS"]:
            if name == "tesseract-traineddata":
                model_files = [f"runtime/tesseract/tessdata/{code}.traineddata" for code in ("eng", "ukr", "pol", "deu")]
            else:
                prefix = "docling-" if name == "docling-models" else "rapidocr-"
                model_files = sorted({"runtime/docling-models/" + value
                                      for key, values in sets.items() if key.startswith(prefix)
                                      for value in values})
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


def write_manifest(root):
    files = TOOLS["inventory"](root)
    identity = TOOLS["runtime_digest"](files)
    (root / "BUNDLE.json").write_text(json.dumps({
        "schema_version": 1, "platform": "windows-x86_64", "files": files,
        "runtime_sha256": identity, "executables": TOOLS["EXECUTABLES"],
    }))
    (root / "VALIDATION.json").write_text(json.dumps({
        "schema_version": 1, "platform": "windows-x86_64", "runtime_sha256": identity,
        "native_windows": True, "network_disabled": True, "clean_cache": True,
        "babeldoc_offline_warmup": True, "ocr_languages": sorted(TOOLS["LANGUAGES"]),
        "licenses_reviewed": True,
    }))


def test_full_windows_build_embeds_verified_payload_and_keeps_sources_outside_exe(runtime_bundle, tmp_path):
    metadata = TOOLS["validate_bundle"](runtime_bundle)
    assert metadata["runtime_sha256"]
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](
        tmp_path, system="Windows", machine="AMD64", runtime_bundle=runtime_bundle
    )
    assert artifact.name == "Forma-windows-x86_64.exe"
    assert f"{runtime_bundle / 'runtime'}:runtimes" in command
    assert f"{runtime_bundle / 'BUNDLE.json'}:runtimes-meta" in command
    assert not any(str(runtime_bundle / "sources") in item for item in command)
    with pytest.raises(ValueError, match="x86-64 only"):
        build["build_command"](tmp_path, system="Windows", machine="ARM64", runtime_bundle=runtime_bundle)


def test_complete_candidate_never_uses_the_publishable_filename_or_claims_native_validation(runtime_bundle, tmp_path):
    (runtime_bundle / "VALIDATION.json").unlink()
    build = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build.py"))
    command, artifact = build["build_command"](
        tmp_path, system="Windows", machine="AMD64", runtime_bundle=runtime_bundle,
        runtime_candidate=True,
    )
    assert artifact.name == "Forma-windows-x86_64-candidate.exe"
    assert f"{runtime_bundle / 'runtime'}:runtimes" in command
    with pytest.raises(ValueError, match="not prepared"):
        build["build_command"](tmp_path, system="Windows", machine="AMD64", runtime_bundle=runtime_bundle)
    with pytest.raises(ValueError, match="complete Windows"):
        build["build_command"](tmp_path, system="Windows", machine="AMD64", desktop_only=True,
                                runtime_candidate=True)
    with pytest.raises(ValueError, match="complete Windows"):
        build["build_command"](tmp_path, system="Linux", machine="x86_64", runtime_candidate=True)


def test_sources_cover_every_version_when_workers_bundle_different_dependency_versions(runtime_bundle):
    dependencies_path = runtime_bundle / "legal/DEPENDENCIES.json"
    dependencies = json.loads(dependencies_path.read_text())
    dependencies.append({**dependencies[0], "version": "2.0"})
    dependencies_path.write_text(json.dumps(dependencies))
    write_manifest(runtime_bundle)
    with pytest.raises(ValueError, match="Each dependency"):
        TOOLS["validate_bundle"](runtime_bundle)
    sources_path = runtime_bundle / "legal/SOURCES.json"
    sources = json.loads(sources_path.read_text())
    source = next(value for value in sources["sources"] if value["component"] == dependencies[0]["name"])
    sources["sources"].append({**source, "version": "2.0"})
    sources_path.write_text(json.dumps(sources))
    write_manifest(runtime_bundle)
    TOOLS["validate_bundle"](runtime_bundle)


@pytest.mark.parametrize("changed", ["model", "native", "source", "ukrainian", "tesseract", "tesseract-empty"])
def test_full_bundle_fails_closed_on_incomplete_or_stale_assets(runtime_bundle, changed):
    if changed == "model":
        (runtime_bundle / "runtime/docling-models/layout/model.safetensors").write_bytes(b"modified")
    elif changed == "native":
        path = runtime_bundle / "VALIDATION.json"
        values = json.loads(path.read_text())
        values["native_windows"] = False
        path.write_text(json.dumps(values))
    elif changed == "source":
        path = runtime_bundle / "legal/SOURCES.json"
        path.write_text(json.dumps({"sources": []}))
        write_manifest(runtime_bundle)
    elif changed == "ukrainian":
        path = runtime_bundle / "runtime/docling-models/MODELS.json"
        values = json.loads(path.read_text())
        values["rapidocr-uk"] = values["rapidocr-en"]
        path.write_text(json.dumps(values))
        write_manifest(runtime_bundle)
    elif changed == "tesseract":
        (runtime_bundle / "runtime/tesseract/tessdata/configs/tsv").unlink()
        write_manifest(runtime_bundle)
    else:
        (runtime_bundle / "runtime/tesseract/tessdata/configs/tsv").write_bytes(b"")
        write_manifest(runtime_bundle)
    with pytest.raises(ValueError):
        TOOLS["validate_bundle"](runtime_bundle)


@pytest.mark.parametrize("name", [".env", "translation-memory.sqlite3", "sample.docx.forma.json"])
def test_inventory_rejects_private_application_data(runtime_bundle, name):
    (runtime_bundle / "runtime" / name).write_text("must not be packaged")
    with pytest.raises(ValueError, match="private configuration"):
        TOOLS["inventory"](runtime_bundle)


def test_staging_constructs_manifest_but_never_claims_native_operability(runtime_bundle, tmp_path):
    assets = next((runtime_bundle / "runtime/babeldoc/assets").glob("*.zip"))
    output = tmp_path / "staged"
    TOOLS["stage_bundle"](
        output, ocr=runtime_bundle / "runtime/ocr", models=runtime_bundle / "runtime/docling-models",
        tesseract=runtime_bundle / "runtime/tesseract", babeldoc=runtime_bundle / "runtime/babeldoc",
        babeldoc_assets=assets, legal=runtime_bundle / "legal", sources=runtime_bundle / "sources",
    )
    TOOLS["validate_bundle"](output, require_validation=False)
    with pytest.raises(ValueError, match="not prepared"):
        TOOLS["validate_bundle"](output)


def test_embedded_workers_take_precedence_and_use_only_their_local_assets(tmp_path, monkeypatch):
    root = tmp_path / "runtimes"
    windows_executable(root / "ocr/forma-ocr.exe")
    windows_executable(root / "babeldoc/babeldoc.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert find_scan_converter() == str(root / "ocr/forma-ocr.exe")
    assert find_babeldoc() == str(root / "babeldoc/babeldoc.exe")
    original = {"PATH": "existing-path", "DOCLING_ARTIFACTS_PATH": "old-models"}
    environment = bundled_runtime.worker_environment(find_scan_converter(), original)
    assert environment["DOCLING_ARTIFACTS_PATH"] == str(root / "docling-models")
    assert environment["TESSDATA_PREFIX"] == str(root / "tesseract/tessdata")
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert original["DOCLING_ARTIFACTS_PATH"] == "old-models"
    external = tmp_path / "external.exe"
    external.touch()
    assert find_scan_converter(str(external)) == str(external)
    assert bundled_runtime.worker_environment(str(external), original) == original


def test_windows_worker_launch_restores_dll_search_even_on_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "application-runtime", raising=False)
    monkeypatch.setattr("ctypes.windll", SimpleNamespace(kernel32=SimpleNamespace(SetDllDirectoryW=calls.append)), raising=False)

    def failed(*_args, **_kwargs):
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(bundled_runtime.subprocess, "Popen", failed)
    with pytest.raises(OSError):
        bundled_runtime.start_worker(["worker.exe"])
    assert calls == [None, "application-runtime"]


def test_babeldoc_entry_restores_assets_then_calls_the_public_sync_cli(tmp_path, monkeypatch):
    calls = []
    assets = tmp_path / "assets"
    assets.mkdir()
    package = assets / ("offline_assets_" + "a" * 64 + ".zip")
    package.touch()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "babeldoc.exe"))
    for name in ("babeldoc", "babeldoc.assets", "babeldoc.assets.assets", "babeldoc.main"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["babeldoc.assets.assets"].restore_offline_assets_package = lambda path: calls.append(("restore", path))
    sys.modules["babeldoc.main"].cli = lambda: calls.append(("cli", None)) or 0
    entry = runpy.run_path(str(Path(__file__).parents[1] / "scripts/babeldoc_entry.py"))
    assert entry["main"]() == 0
    assert calls == [("restore", package), ("cli", None)]
