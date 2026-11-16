"""Stage and verify complete, isolated native macOS arm64 worker runtimes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
from pathlib import Path, PurePosixPath

LANGUAGES = {"en", "uk", "pl", "de"}
TESSERACT_LANGUAGES = {"eng", "ukr", "pol", "deu"}
EXECUTABLES = {
    "ocr": "runtime/ocr/forma-ocr",
    "babeldoc": "runtime/babeldoc/babeldoc",
    "tesseract": "runtime/tesseract/tesseract",
}
LEGAL_COMPONENTS = {
    "docling", "docling-models", "rapidocr", "rapidocr-models", "onnxruntime", "torch",
    "pypdfium2", "python-docx", "tesseract", "tesseract-traineddata", "babeldoc",
}
MODEL_COMPONENTS = {"docling-models", "rapidocr-models", "tesseract-traineddata",
                    "babeldoc-assets", "babeldoc-fonts", "babeldoc-layout-model"}
MODEL_SETS = {"docling-layout", "docling-tableformer", "rapidocr-en", "rapidocr-uk", "rapidocr-pl", "rapidocr-de"}
PRIVATE_NAMES = {"preferences.json", "credentials.json", "token", "usage.sqlite3", "library.sqlite3",
                 "translation-memory.sqlite3", ".git", "__pycache__"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(value: str) -> PurePosixPath:
    if (not isinstance(value, str) or "\\" in value or ":" in value or "\0" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Runtime inventory paths must be portable relative paths")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("Runtime inventory paths must stay inside the prepared bundle")
    return path


def _json(path: Path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Runtime metadata is too large")
    return json.loads(path.read_text(encoding="utf-8"))


def inventory(root: Path) -> dict[str, dict]:
    root = Path(root).resolve()
    files, names = {}, set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in {"BUNDLE.json", "VALIDATION.json"}:
            if path.is_symlink():
                raise ValueError("Runtime metadata must not be symlinked")
            continue
        parts = _relative(relative).parts
        if parts[0] not in {"runtime", "legal", "sources"}:
            raise ValueError("Unexpected file outside runtime, legal or sources")
        if (any(part.casefold() in PRIVATE_NAMES or part.casefold().startswith(".env") for part in parts)
                or path.name.casefold().endswith((".forma.json", ".forma.html", ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"))):
            raise ValueError("User data or private configuration is not a runtime payload")
        if relative.casefold() in names:
            raise ValueError("Case-insensitive macOS runtime path collision")
        names.add(relative.casefold())
        if path.is_symlink():
            target = os.readlink(path)
            try:
                portable = (parts[0] == "runtime" and not Path(target).is_absolute()
                            and path.resolve(strict=True).is_relative_to(root / "runtime"))
            except (OSError, RuntimeError):
                portable = False
            if not portable:
                raise ValueError("Non-portable or broken runtime symlink: " + relative)
            files[relative] = {"symlink": target}
        elif path.is_file():
            files[relative] = {"sha256": digest(path), "bytes": path.stat().st_size,
                               "mode": stat.S_IMODE(path.stat().st_mode)}
        elif path.is_dir():
            files[relative] = {"directory": True, "mode": stat.S_IMODE(path.stat().st_mode)}
        else:
            raise ValueError("Unsupported runtime file: " + relative)
    return files


def runtime_digest(files: dict) -> str:
    selected = {name: entry for name, entry in files.items() if name.startswith("runtime/")}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _arm64_executable(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(8)
    if (len(header) != 8 or header[:4] != b"\xcf\xfa\xed\xfe"
            or struct.unpack_from("<I", header, 4)[0] != 0x0100000C or not os.access(path, os.X_OK)):
        raise ValueError("Prepared launchers must be native thin macOS arm64 executables with executable permissions")


def _validate_assets(root: Path, files: dict) -> dict:
    for language in TESSERACT_LANGUAGES:
        path = root / "runtime/tesseract/tessdata" / f"{language}.traineddata"
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Tesseract language data are missing")
    tsv = root / "runtime/tesseract/tessdata/configs/tsv"
    if not tsv.is_file() or not tsv.stat().st_size:
        raise ValueError("Tesseract's TSV configuration is required by the OCR helper")
    models = _json(root / "runtime/docling-models/MODELS.json")
    if not isinstance(models, dict) or set(models) != MODEL_SETS:
        raise ValueError("MODELS.json must identify layout, TableFormer and each supported OCR language")
    for entries in models.values():
        if not isinstance(entries, list) or not entries:
            raise ValueError("Every model set must list its prepared artifact files")
        for entry in entries:
            relative = "runtime/docling-models/" + str(_relative(entry))
            if relative not in files or not files[relative].get("bytes"):
                raise ValueError("A declared model artifact is missing")
    if not any((root / "runtime/docling-models").rglob("*.onnx")) or not any((root / "runtime/docling-models").rglob("*.safetensors")):
        raise ValueError("Predownloaded Docling and RapidOCR model artifacts are required")
    if not any("eslav" in entry.casefold() for entry in models["rapidocr-uk"]):
        raise ValueError("Ukrainian needs its East Slavic RapidOCR recognizer")
    for language in ("en", "pl", "de"):
        if not any("ocrv6" in entry.casefold() for entry in models["rapidocr-" + language]):
            raise ValueError("English, Polish and German need their PP-OCRv6 artifact set")
    assets = list((root / "runtime/babeldoc/assets").glob("offline_assets_*.zip"))
    if len(assets) != 1 or not assets[0].stat().st_size:
        raise ValueError("One official BabelDOC offline asset package is required")
    return models


def _validate_legal(root: Path, files: dict, models: dict) -> None:
    dependencies = _json(root / "legal/DEPENDENCIES.json")
    versions = {}
    for dependency in dependencies:
        name, version = dependency["name"].casefold(), dependency["version"]
        versions.setdefault(name, set()).add(version)
        if not version or not dependency.get("license") or not dependency.get("license_files"):
            raise ValueError("Each runtime/model dependency needs version and license materials")
        for value in dependency["license_files"]:
            path = root / "legal" / str(_relative(value))
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("A runtime dependency license file is missing")
    if not LEGAL_COMPONENTS <= set(versions):
        raise ValueError("Runtime code and model license materials are incomplete")
    sources = _json(root / "legal/SOURCES.json").get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Runtime corresponding-source archive inventory is required")
    model_sources = {
        "docling-models": {"runtime/docling-models/" + value for key in ("docling-layout", "docling-tableformer") for value in models[key]},
        "rapidocr-models": {"runtime/docling-models/" + value for key in ("rapidocr-en", "rapidocr-uk", "rapidocr-pl", "rapidocr-de") for value in models[key]},
        "tesseract-traineddata": {f"runtime/tesseract/tessdata/{language}.traineddata" for language in TESSERACT_LANGUAGES},
    }
    covered = set()
    for source in sources:
        component, version = source["component"].casefold(), source["version"]
        if component not in versions or version not in versions[component]:
            raise ValueError("Corresponding sources must match the component name and version")
        if component in MODEL_COMPONENTS:
            if not source.get("url", "").startswith("https://") or not source.get("revision") or not source.get("model_files"):
                raise ValueError("Model sources require an upstream URL, revision and included artifact files")
            for value in source["model_files"]:
                if str(_relative(value)) not in files:
                    raise ValueError("A declared model source artifact is missing")
            if not model_sources.get(component, set()) <= set(source["model_files"]):
                raise ValueError("Model source declarations must cover the required model sets")
        else:
            path = root / "sources" / str(_relative(source["archive"]))
            if not path.is_file() or digest(path) != source["sha256"]:
                raise ValueError("A corresponding-source archive is missing or has the wrong hash")
        covered.add((component, version))
    if not {(name, version) for name, values in versions.items() for version in values} <= covered:
        raise ValueError("Each dependency needs matching corresponding-source material")


def validate_bundle(root: Path, *, require_validation: bool = True) -> dict:
    root = Path(root).resolve()
    try:
        metadata = _json(root / "BUNDLE.json")
        if metadata.get("schema_version") != 1 or metadata.get("platform") != "macos-arm64":
            raise ValueError("Unsupported macOS runtime bundle format")
        files = inventory(root)
        if not files or metadata.get("files") != files or metadata.get("runtime_sha256") != runtime_digest(files):
            raise ValueError("Runtime inventory is stale or a file hash, permission or link does not match")
        if metadata.get("executables") != EXECUTABLES:
            raise ValueError("The complete macOS bundle requires OCR, BabelDOC and Tesseract")
        for relative in EXECUTABLES.values():
            _arm64_executable(root / relative)
        models = _validate_assets(root, files)
        _validate_legal(root, files, models)
        if require_validation:
            validation = _json(root / "VALIDATION.json")
            if (validation.get("schema_version") != 1 or validation.get("platform") != "macos-arm64"
                    or validation.get("runtime_sha256") != metadata["runtime_sha256"]
                    or validation.get("native_macos") is not True or validation.get("architecture") != "arm64"
                    or validation.get("network_disabled") is not True or validation.get("clean_cache") is not True
                    or validation.get("babeldoc_offline_warmup") is not True or validation.get("licenses_reviewed") is not True
                    or validation.get("ocr_languages") != sorted(LANGUAGES)
                    or validation.get("tesseract_languages") != sorted(TESSERACT_LANGUAGES)):
                raise ValueError("Complete macOS builds require a matching native offline validation record")
        return metadata
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("macOS runtimes are not prepared; stage and natively validate the complete runtime bundle") from exc


def stage_bundle(output: Path, *, ocr: Path, models: Path, tesseract: Path, babeldoc: Path,
                 babeldoc_assets: Path, legal: Path, sources: Path) -> dict:
    """Copy explicit prebuilt inputs without executing them or claiming validation."""
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
            if not Path(source).is_dir():
                raise ValueError("Prepared directory is missing: " + str(source))
            shutil.copytree(source, output / relative, symlinks=True,
                            ignore=shutil.ignore_patterns(".cache", "__pycache__", "*.pyc"))
        assets = output / "runtime/babeldoc/assets"
        assets.mkdir(exist_ok=True)
        if not re.fullmatch(r"offline_assets_[0-9a-f]+\.zip", babeldoc_assets.name) or babeldoc_assets.is_symlink():
            raise ValueError("Use the unchanged official BabelDOC offline package filename")
        destination = assets / babeldoc_assets.name
        if destination.exists() and digest(destination) != digest(babeldoc_assets):
            raise ValueError("Conflicting BabelDOC offline asset packages")
        if not destination.exists():
            shutil.copy2(babeldoc_assets, destination)
        files = inventory(output)
        metadata = {"schema_version": 1, "platform": "macos-arm64", "executables": EXECUTABLES,
                    "runtime_sha256": runtime_digest(files), "files": files}
        (output / "BUNDLE.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        validate_bundle(output, require_validation=False)
        return metadata
    except BaseException:
        shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="Stage prepared frozen workers, models and legal materials")
    stage.add_argument("--output", type=Path, required=True)
    for name in ("ocr", "models", "tesseract", "babeldoc", "babeldoc-assets", "legal", "sources"):
        stage.add_argument("--" + name, type=Path, required=True)
    verify = commands.add_parser("verify", help="Check complete inventory and native offline validation")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--allow-unvalidated", action="store_true", help="Inspect staging before native validation")
    args = parser.parse_args()
    if args.command == "stage":
        values = vars(args).copy()
        values.pop("command")
        metadata = stage_bundle(**values)
        print("Staged runtime SHA256: " + metadata["runtime_sha256"])
        print("Native offline validation is still required; no attestation was generated")
    else:
        validate_bundle(args.bundle, require_validation=not args.allow_unvalidated)
        print("Complete runtime inventory verified")


if __name__ == "__main__":
    main()
