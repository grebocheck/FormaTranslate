"""Stage and verify prepared Windows runtimes; never download or execute them implicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
from pathlib import Path, PurePosixPath

LANGUAGES = {"en", "uk", "pl", "de"}
EXECUTABLES = {
    "ocr": "runtime/ocr/forma-ocr.exe",
    "babeldoc": "runtime/babeldoc/babeldoc.exe",
    "tesseract": "runtime/tesseract/tesseract.exe",
}
LEGAL_COMPONENTS = {
    "docling", "docling-models", "rapidocr", "rapidocr-models", "onnxruntime", "torch",
    "pypdfium2", "python-docx", "tesseract", "tesseract-traineddata", "babeldoc",
}
PRIVATE_NAMES = {"preferences.json", "credentials.json", "token", "usage.sqlite3", "library.sqlite3"}
MODEL_COMPONENTS = {"docling-models", "rapidocr-models", "tesseract-traineddata",
                    "babeldoc-assets", "babeldoc-fonts", "babeldoc-layout-model"}
MODEL_SETS = {"docling-layout", "docling-tableformer", "rapidocr-en", "rapidocr-uk", "rapidocr-pl", "rapidocr-de"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Runtime inventory paths must be portable relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Runtime inventory paths must stay inside the prepared bundle")
    return path


def _json(path: Path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Runtime metadata is too large")
    return json.loads(path.read_text(encoding="utf-8"))


def inventory(root: Path) -> dict[str, dict]:
    files, windows_names = {}, set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Runtime bundles must not contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"BUNDLE.json", "VALIDATION.json"}:
            continue
        parts = _relative(relative).parts
        if parts[0] not in {"runtime", "legal", "sources"}:
            raise ValueError("Unexpected file outside runtime, legal or sources")
        if any(part.casefold() in {".git", "__pycache__", "translation-memory.sqlite3", *PRIVATE_NAMES} or part.casefold().startswith(".env") for part in parts) or path.name.casefold().endswith((".forma.json", ".forma.html", ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
            raise ValueError("User data or private configuration is not a runtime payload")
        if relative.casefold() in windows_names:
            raise ValueError("Case-insensitive Windows runtime path collision")
        windows_names.add(relative.casefold())
        files[relative] = {"sha256": digest(path), "bytes": path.stat().st_size}
    return files


def runtime_digest(files: dict) -> str:
    selected = {path: value for path, value in files.items() if path.startswith("runtime/")}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _windows_executable(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("Prepared components must be native Windows x86-64 executables")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        signature = stream.read(6)
        if signature != b"PE\x00\x00\x64\x86":
            raise ValueError("Prepared components must target Windows x86-64")


def validate_bundle(root: Path, *, require_validation: bool = True) -> dict:
    root = Path(root).resolve()
    try:
        metadata = _json(root / "BUNDLE.json")
        if metadata.get("schema_version") != 1 or metadata.get("platform") != "windows-x86_64":
            raise ValueError("Unsupported Windows runtime bundle format")
        files = inventory(root)
        if not files or metadata.get("files") != files:
            raise ValueError("Runtime inventory is stale or a file hash does not match")
        if metadata.get("runtime_sha256") != runtime_digest(files):
            raise ValueError("Runtime payload identity does not match its inventory")
        if metadata.get("executables") != EXECUTABLES:
            raise ValueError("The complete Windows bundle requires OCR, BabelDOC and Tesseract")
        for relative in EXECUTABLES.values():
            _windows_executable(root / relative)
        for language in ("eng", "ukr", "pol", "deu"):
            path = root / "runtime/tesseract/tessdata" / f"{language}.traineddata"
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("Tesseract language data are missing")
        tsv = root / "runtime/tesseract/tessdata/configs/tsv"
        if not tsv.is_file() or not tsv.stat().st_size:
            raise ValueError("Tesseract's TSV configuration is required by the OCR helper")
        if not any((root / "runtime/docling-models").rglob("*.onnx")) or not any((root / "runtime/docling-models").rglob("*.safetensors")):
            raise ValueError("Predownloaded Docling and RapidOCR model artifacts are required")
        models = _json(root / "runtime/docling-models/MODELS.json")
        if not isinstance(models, dict) or set(models) != MODEL_SETS:
            raise ValueError("MODELS.json must identify layout, TableFormer and each supported OCR language")
        for entries in models.values():
            if not isinstance(entries, list) or not entries:
                raise ValueError("Every model set must list its prepared artifact files")
            for entry in entries:
                relative = "runtime/docling-models/" + str(_relative(entry))
                if relative not in files or not files[relative]["bytes"]:
                    raise ValueError("A declared model artifact is missing")
        if not any("eslav" in entry.casefold() for entry in models["rapidocr-uk"]):
            raise ValueError("Ukrainian needs its East Slavic RapidOCR recognizer")
        for language in ("en", "pl", "de"):
            if not any("ocrv6" in entry.casefold() for entry in models["rapidocr-" + language]):
                raise ValueError("English, Polish and German need their PP-OCRv6 artifact set")
        packages = list((root / "runtime/babeldoc/assets").glob("offline_assets_*.zip"))
        if len(packages) != 1 or not packages[0].stat().st_size:
            raise ValueError("One official BabelDOC offline asset package is required")
        dependencies = _json(root / "legal/DEPENDENCIES.json")
        if not isinstance(dependencies, list):
            raise ValueError("Runtime dependency license inventory is required")
        names, versions = set(), {}
        for dependency in dependencies:
            names.add(dependency["name"].casefold())
            versions.setdefault(dependency["name"].casefold(), set()).add(dependency["version"])
            if not dependency.get("version") or not dependency.get("license") or not dependency.get("license_files"):
                raise ValueError("Each runtime/model dependency needs version and license materials")
            for relative in dependency["license_files"]:
                path = root / "legal" / str(_relative(relative))
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError("A runtime dependency license file is missing")
        if not LEGAL_COMPONENTS <= names:
            raise ValueError("Runtime code and model license materials are incomplete")
        sources = _json(root / "legal/SOURCES.json").get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("Runtime corresponding-source archive inventory is required")
        covered = set()
        model_sources = {
            "docling-models": {
                "runtime/docling-models/" + value
                for key in ("docling-layout", "docling-tableformer") for value in models[key]
            },
            "rapidocr-models": {
                "runtime/docling-models/" + value
                for key in ("rapidocr-en", "rapidocr-uk", "rapidocr-pl", "rapidocr-de") for value in models[key]
            },
            "tesseract-traineddata": {
                f"runtime/tesseract/tessdata/{language}.traineddata"
                for language in ("eng", "ukr", "pol", "deu")
            },
        }
        for source in sources:
            component = source["component"].casefold()
            if component not in versions or source["version"] not in versions[component]:
                raise ValueError("Corresponding sources must match the component name and version")
            if component in MODEL_COMPONENTS:
                if not source.get("url", "").startswith("https://") or not source.get("revision") or not source.get("model_files"):
                    raise ValueError("Model sources require an upstream URL, revision and included artifact files")
                for entry in source["model_files"]:
                    if str(_relative(entry)) not in files:
                        raise ValueError("A declared model source artifact is missing")
                if not model_sources.get(component, set()) <= set(source["model_files"]):
                    raise ValueError("Model source declarations must cover the required model sets")
            elif source.get("kind") == "vendor-redistributable":
                if (
                    not source.get("url", "").startswith("https://")
                    or not source.get("source_availability")
                    or not source.get("license_terms")
                ):
                    raise ValueError("Vendor redistributables require official provenance and license terms")
                terms = root / "legal" / str(_relative(source["license_terms"]))
                path = root / "sources" / str(_relative(source["redistributable"]))
                if not terms.is_file() or not terms.stat().st_size:
                    raise ValueError("Vendor redistribution license terms are missing")
                if not path.is_file() or digest(path) != source["sha256"]:
                    raise ValueError("The original vendor redistributable is missing or has the wrong hash")
            else:
                path = root / "sources" / str(_relative(source["archive"]))
                if not path.is_file() or digest(path) != source["sha256"]:
                    raise ValueError("A corresponding-source archive is missing or has the wrong hash")
            covered.add((component, source["version"]))
        required_sources = {(component, version) for component, values in versions.items() for version in values}
        if not required_sources <= covered:
            raise ValueError("Each dependency needs matching corresponding-source material")
        if require_validation:
            validation = _json(root / "VALIDATION.json")
            if (
                validation.get("schema_version") != 1
                or validation.get("platform") != "windows-x86_64"
                or validation.get("runtime_sha256") != metadata["runtime_sha256"]
                or validation.get("native_windows") is not True
                or validation.get("network_disabled") is not True
                or validation.get("clean_cache") is not True
                or validation.get("babeldoc_offline_warmup") is not True
                or validation.get("ocr_languages") != sorted(LANGUAGES)
                or validation.get("licenses_reviewed") is not True
            ):
                raise ValueError("Complete Windows builds require a matching native offline validation record")
        return metadata
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Windows runtimes are not prepared; follow docs/WINDOWS-RUNTIME.md") from exc


def _copy(source: Path, destination: Path) -> None:
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError(f"Prepared directory is missing: {source}")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Prepared inputs must contain real files, not symlinks")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".cache", "__pycache__", "*.pyc"))


def stage_bundle(output: Path, *, ocr: Path, models: Path, tesseract: Path, babeldoc: Path,
                 babeldoc_assets: Path, legal: Path, sources: Path) -> dict:
    """Copy explicit prepared inputs into a NEW staging folder and inventory them."""
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a new staging directory; prepared bundles are never overwritten")
    for source in (ocr, models, tesseract, babeldoc, legal, sources):
        if Path(source).resolve() in output.resolve().parents:
            raise ValueError("The staging destination must not be inside an input directory")
    output.mkdir(parents=True)
    try:
        for source, relative in ((ocr, "runtime/ocr"), (models, "runtime/docling-models"),
                                 (tesseract, "runtime/tesseract"), (babeldoc, "runtime/babeldoc"),
                                 (legal, "legal"), (sources, "sources")):
            _copy(source, output / relative)
        assets = output / "runtime/babeldoc/assets"
        assets.mkdir(exist_ok=True)
        if not re.fullmatch(r"offline_assets_[0-9a-f]+\.zip", babeldoc_assets.name) or babeldoc_assets.is_symlink():
            raise ValueError("Use the unchanged official BabelDOC offline package filename")
        destination = assets / babeldoc_assets.name
        if destination.exists():
            if digest(destination) != digest(babeldoc_assets):
                raise ValueError("Conflicting BabelDOC offline asset packages")
        else:
            shutil.copy2(babeldoc_assets, destination)
        files = inventory(output)
        metadata = {"schema_version": 1, "platform": "windows-x86_64", "executables": EXECUTABLES,
                    "runtime_sha256": runtime_digest(files), "files": files}
        (output / "BUNDLE.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        validate_bundle(output, require_validation=False)
        return metadata
    except BaseException:
        shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="Stage already-built components and already-downloaded models")
    stage.add_argument("--output", type=Path, required=True)
    for name in ("ocr", "models", "tesseract", "babeldoc", "babeldoc-assets", "legal", "sources"):
        stage.add_argument("--" + name, type=Path, required=True)
    verify = sub.add_parser("verify", help="Verify hashes, required files and native smoke attestation")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()
    if args.command == "stage":
        values = vars(args).copy()
        values.pop("command")
        metadata = stage_bundle(**values)
        print("Staged runtime SHA256: " + metadata["runtime_sha256"])
        print("Native offline smoke validation is still required; see docs/WINDOWS-RUNTIME.md")
    else:
        validate_bundle(args.bundle)
        print("Runtime inventory and recorded native validation match")


if __name__ == "__main__":
    main()
