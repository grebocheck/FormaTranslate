"""Build one executable on the target OS: uv run --group build python scripts/build.py."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import shutil
import ssl
import subprocess
import sys
import sysconfig
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

_runtime_spec = importlib.util.spec_from_file_location(
    "_forma_windows_runtime", Path(__file__).with_name("windows_runtime.py")
)
if _runtime_spec is None or _runtime_spec.loader is None:
    raise RuntimeError("Missing Windows runtime packaging helper")
_runtime_tools = importlib.util.module_from_spec(_runtime_spec)
_runtime_spec.loader.exec_module(_runtime_tools)
digest = _runtime_tools.digest
validate_bundle = _runtime_tools.validate_bundle

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_QT = (
    "QtCharts", "QtGraphs", "QtGraphsWidgets", "QtDataVisualization", "QtQuick3D",
    "QtQuick3DAssetImport", "QtQuick3DUtils", "QtQuickTimeline", "QtLocation",
    "QtVirtualKeyboard", "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick",
    "QtWebView", "QtPdf", "QtPdfWidgets",
)


def release_version(root: Path = ROOT) -> str:
    tree = ast.parse((root / "src/forma/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise ValueError("Missing Forma release version")


def _dependency_names(root: Path) -> list[str]:
    from packaging.requirements import Requirement

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pending = [Requirement(value) for value in project["project"]["dependencies"]]
    pending.append(Requirement("pyinstaller"))
    # Hooks discover optional image, test and highlighting imports when these
    # packages are installed. Collect their notices before freezing, including
    # Pillow's complete bundled image/font library notices, without requiring
    # optional packages in a clean application build environment.
    for optional in ("Pillow", "pytest", "Pygments"):
        try:
            importlib.metadata.distribution(optional)
        except importlib.metadata.PackageNotFoundError:
            continue
        pending.append(Requirement(optional))
    names = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        distribution = importlib.metadata.distribution(requirement.name)
        name = distribution.metadata["Name"]
        normalized = name.lower().replace("_", "-")
        if normalized in names:
            continue
        names[normalized] = name
        pending.extend(Requirement(value) for value in distribution.requires or [])
    return sorted(names.values(), key=str.lower)


def prepare_release_materials(
    root: Path, artifact: Path, *, system_legal: Path | None = None,
    runtime_bundle: Path | None = None, runtime_candidate: bool = False,
) -> Path:
    """Only repository notices and installed package licenses enter this folder."""
    sources = json.loads((root / "third_party/SOURCES.json").read_text(encoding="utf-8"))
    versions = {entry["component"]: entry["version"] for entry in sources["sources"]}
    installed = importlib.metadata.version("PySide6")
    if versions.get("PySide6") != installed or versions.get("Qt") != installed:
        raise ValueError("Update third_party/SOURCES.json and the corresponding source archives for this PySide6/Qt version")
    payload = root / "build" / "release" / artifact.stem / "legal"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    for source, name in ((root / "LICENSE", "LICENSE.txt"),
                         (root / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                         (root / "docs/BUILDING.md", "BUILDING.md")):
        shutil.copy2(source, payload / name)
    public_docs = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE"]
    public_docs += [path.relative_to(root).as_posix() for path in (root / "docs").glob("*.md")]
    public_docs += ["docs/interface.png", "docs/quick-translate.png"]
    for name in public_docs:
        source = root / name
        if source.is_file():
            destination = payload / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    shutil.copytree(root / "third_party", payload / "third_party")
    if runtime_bundle is not None:
        validate_bundle(runtime_bundle, require_validation=not runtime_candidate)
        shutil.copytree(runtime_bundle / "legal", payload / "runtimes")
        shutil.copy2(runtime_bundle / "BUNDLE.json", payload / "runtimes/BUNDLE.json")
        if (runtime_bundle / "VALIDATION.json").is_file():
            shutil.copy2(runtime_bundle / "VALIDATION.json", payload / "runtimes/VALIDATION.json")
        runtime_sources = payload.parent / "runtime-sources"
        if runtime_sources.exists():
            shutil.rmtree(runtime_sources)
        shutil.copytree(runtime_bundle / "sources", runtime_sources)
    if system_legal is not None:
        destination = payload / "third_party" / "linux-system"
        destination.mkdir(parents=True)
        shutil.copy2(system_legal / "SOURCES.json", destination / "SOURCES.json")
        shutil.copytree(system_legal / "licenses", destination / "licenses")
    inventory = []
    for name in _dependency_names(root):
        distribution = importlib.metadata.distribution(name)
        licenses = []
        for entry in distribution.files or []:
            leaf = Path(str(entry)).name.lower()
            if not any(word in leaf for word in ("license", "copying", "copyright", "notice")):
                continue
            # Distribution RECORDs can refer outside site-packages. Never walk them.
            source = Path(distribution.locate_file(entry))
            if not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".so", ".dll"}:
                continue
            destination = payload / "licenses" / name / Path(str(entry)).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                digest = hashlib.sha256(str(entry).encode()).hexdigest()[:8]
                destination = destination.with_name(digest + "-" + destination.name)
            shutil.copy2(source, destination)
            licenses.append(destination.relative_to(payload).as_posix())
        inventory.append({"name": name, "version": distribution.version,
                          "license": distribution.metadata.get("License-Expression")
                          or distribution.metadata.get("License") or "See third_party/licenses",
                          "license_files": sorted(set(licenses))})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if python_license.is_file():
        destination = payload / "licenses" / "Python"
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, destination / "LICENSE.txt")
    (payload / "DEPENDENCIES.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    version = release_version(root)
    parts = tuple(int(part) for part in version.split(".")) + (0,)
    version_text = (
        "VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(parts) + ", prodvers=" + repr(parts)
        + ", mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), kids=["
        "StringFileInfo([StringTable('040904B0', ["
        "StringStruct('FileDescription', 'FormaTranslate'),"
        "StringStruct('ProductName', 'FormaTranslate'),"
        f"StringStruct('FileVersion', {version!r}),StringStruct('ProductVersion', {version!r}),"
        "StringStruct('LegalCopyright', 'FormaTranslate contributors; MIT; third-party licenses apply')"
        "])]),VarFileInfo([VarStruct('Translation', [1033,1200])])])\n"
    )
    (payload.parent / "windows-version.txt").write_text(version_text, encoding="utf-8")
    return payload


def build_command(
    root: Path = ROOT, *, system: str | None = None, machine: str | None = None,
    runtime_bundle: Path | None = None, desktop_only: bool = False,
    runtime_candidate: bool = False,
) -> tuple[list[str], Path]:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    backends = {
        "windows": "keyring.backends.Windows",
        "linux": "keyring.backends.SecretService",
    }
    if system not in backends:
        raise SystemExit("One-file builds are currently supported on Windows and Linux.")
    if runtime_candidate and (system != "windows" or desktop_only):
        raise ValueError("Runtime candidates require a complete Windows build")
    name = f"Forma-{system}-{machine}"
    if system == "windows":
        if desktop_only:
            name += "-desktop-only"
        else:
            if machine != "x86_64":
                raise ValueError("Complete Windows runtime bundles currently support x86-64 only")
            runtime_bundle = Path(runtime_bundle or root / "build/windows-runtime").expanduser().resolve()
            validate_bundle(runtime_bundle, require_validation=not runtime_candidate)
            if runtime_candidate:
                name += "-candidate"
    artifact = root / "dist" / (name + (".exe" if system == "windows" else ""))
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onefile",
        "--noupx",
        "--name",
        name,
        "--distpath",
        str(root / "dist"),
        "--workpath",
        str(root / "build" / "onefile" / name),
        "--specpath",
        str(root / "build" / "specs"),
        "--paths",
        str(root / "src"),
        "--additional-hooks-dir",
        str(root / "scripts/pyinstaller_hooks"),
        "--add-data",
        f"{root / 'src/forma/ui'}:forma/ui",
        "--add-data",
        f"{root / 'src/forma/locales'}:forma/locales",
        "--add-data",
        f"{root / 'build/release' / name / 'legal'}:legal",
        # Native credential backends are selected dynamically at runtime.
        "--hidden-import",
        backends[system],
        "--copy-metadata",
        "keyring",
        str(root / "scripts/desktop.py"),
    ]
    for module in EXCLUDED_QT:
        command[-1:-1] = ["--exclude-module", "PySide6." + module]
    if system == "windows":
        if not desktop_only:
            command[-1:-1] = [
                "--add-data", f"{runtime_bundle / 'BUNDLE.json'}:runtimes-meta",
            ]
        command[-1:-1] = ["--icon", str(root / "src/forma/ui/forma.ico"), "--version-file",
                         str(root / "build/release" / name / "windows-version.txt")]
    return command, artifact


def runtime_build_command(command: list[str], runtime_bundle: Path) -> list[str]:
    """Add isolated worker files after dependency analysis, preserving their DLL scope."""
    from PyInstaller.__main__ import generate_parser, run_makespec

    options = vars(generate_parser().parse_args(command[3:]))
    scripts = options.pop("filenames")
    spec = Path(run_makespec(scripts, **options))
    source = spec.read_text(encoding="utf-8")
    boundary = "\npyz = PYZ("
    if source.count(boundary) != 1:
        raise RuntimeError("Unexpected PyInstaller spec layout; isolated runtime payload was not added")
    # Analysis reclassifies --add-data DLLs as BINARY and scans their imports.
    # That copies worker dependencies into the desktop's root DLL directory,
    # where independent OCR/PDF versions can override Qt/Python dependencies.
    insertion = (
        "\nfrom PyInstaller.building.datastruct import Tree\n"
        f"a.datas += Tree({str((runtime_bundle / 'runtime').resolve())!r}, "
        "prefix='runtimes', typecode='DATA')\n"
    )
    spec.write_text(source.replace(boundary, insertion + boundary), encoding="utf-8")
    return [*command[:3], "--noconfirm", "--clean", "--distpath", options["distpath"],
            "--workpath", options["workpath"], str(spec)]


def package_release(
    root: Path, artifact: Path, payload: Path, *, validation: dict | None = None,
    runtime_bundle: Path | None = None, runtime_candidate: bool = False,
    separate_runtime_sources: bool = False,
) -> Path:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(artifact))
    members = sorted(name.replace("\\", "/") for name in archive.toc)
    forbidden = ("qt6charts", "qt6graphs", "qt6datavisualization", "qt6quick3d",
                 "qt6virtualkeyboard", "qt6quicktimeline", "qt6location", "qt6webengine")
    bad = [name for name in members if any(value in name.lower() for value in forbidden)]
    if bad:
        raise RuntimeError("Unexpected optional Qt libraries: " + ", ".join(bad))
    version = release_version(root)
    target = artifact.stem.removeprefix("Forma-")
    if separate_runtime_sources and runtime_bundle is None:
        raise ValueError("Separate runtime sources require an embedded runtime bundle")
    metadata = {
        "name": "FormaTranslate", "version": version, "platform": target,
        "python": platform.python_version(), "system": platform.platform(),
        "openssl": ssl.OPENSSL_VERSION,
        "built_at_utc": datetime.now(UTC).isoformat(),
        "pyside6": importlib.metadata.version("PySide6"),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "executable": artifact.name, "bytes": artifact.stat().st_size,
        "sha256": digest(artifact),
        "runtime_members": members,
        "native_windows_validation": "Required separately; a build does not certify GUI behavior",
        "validation": validation or {"runtime": "Not tested by the packaging script"},
    }
    if runtime_bundle is not None:
        runtime = validate_bundle(runtime_bundle, require_validation=not runtime_candidate)
        expected = {
            "runtimes/" + path.removeprefix("runtime/")
            for path in runtime["files"] if path.startswith("runtime/")
        }
        if not expected <= set(members):
            raise RuntimeError("The executable is missing prepared runtime payload files")
        metadata["runtime_mode"] = "complete-embedded-candidate" if runtime_candidate else "complete-embedded"
        metadata["publication_ready"] = not runtime_candidate
        metadata["runtime_sha256"] = runtime["runtime_sha256"]
        validation_path = runtime_bundle / "VALIDATION.json"
        metadata["runtime_validation"] = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.is_file() else None
        if separate_runtime_sources:
            sources_archive = root / "dist" / f"FormaTranslate-{version}-{target}-runtime-sources.zip"
            # Upstream source archives are already compressed. Storing them
            # avoids a second expensive compression pass and keeps each
            # downloadable artifact below hosting size limits.
            with zipfile.ZipFile(sources_archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                for path in sorted((payload.parent / "runtime-sources").rglob("*")):
                    if path.is_file():
                        bundle.write(path, "runtime-sources/" + path.relative_to(payload.parent / "runtime-sources").as_posix())
            metadata["runtime_sources"] = {
                "delivery": "separate-archive", "archive": sources_archive.name,
                "bytes": sources_archive.stat().st_size, "sha256": digest(sources_archive),
                "instructions": "Publish this corresponding-source archive alongside the application archive. Extract both into the same directory to restore the runtime-sources paths in runtimes/SOURCES.json.",
            }
        else:
            metadata["runtime_sources"] = {"delivery": "included", "directory": "runtime-sources"}
    else:
        metadata["runtime_mode"] = "desktop-only-development" if "desktop-only" in target else "external-tools"
    if target.startswith("linux-"):
        glibc_versions = set()
        for name in archive.toc:
            if ".so" in name:
                glibc_versions.update(tuple(map(int, match)) for match in
                                      re.findall(rb"GLIBC_(\d+)\.(\d+)", archive.extract(name)))
        metadata["minimum_glibc"] = ".".join(map(str, max(glibc_versions))) if glibc_versions else "unknown"
    metadata_path = payload.parent / "RELEASE.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    source_files = [path for path in (root / "src/forma").rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts]
    source_files += [root / "scripts/build.py", root / "pyproject.toml", root / "uv.lock"]
    source_files += list((root / "scripts/pyinstaller_hooks").glob("*.py"))
    source_files += [root / "scripts" / name for name in ("windows_runtime.py", "ocr_entry.py", "babeldoc_entry.py")
                       if (root / "scripts" / name).is_file()]
    if runtime_bundle is not None:
        source_files += list((root / "tools/ocr/src").rglob("*.py"))
        source_files += [root / "tools/ocr" / name for name in ("pyproject.toml", "uv.lock", "LICENSE")]
    file_hashes = {path.relative_to(root).as_posix(): digest(path)
                   for path in sorted(source_files)}
    source_manifest = {
        "version": version, "algorithm": "sha256",
        "digest_input": "UTF-8 JSON of files, sorted keys, separators comma/colon",
        "sha256": hashlib.sha256(json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "files": file_hashes,
    }
    source_path = payload.parent / "SOURCE.json"
    source_path.write_text(json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8")
    destination = root / "dist" / f"FormaTranslate-{version}-{target}.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.write(artifact, artifact.name)
        bundle.write(metadata_path, "RELEASE.json")
        bundle.write(source_path, "SOURCE.json")
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(payload).as_posix())
        if runtime_bundle is not None and not separate_runtime_sources:
            for path in sorted((payload.parent / "runtime-sources").rglob("*")):
                if path.is_file():
                    bundle.write(path, "runtime-sources/" + path.relative_to(payload.parent / "runtime-sources").as_posix())
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-bundle", type=Path, help="Prepared complete Windows runtime bundle")
    parser.add_argument("--desktop-only", action="store_true", help="Development Windows build without embedded OCR/PDF tools")
    parser.add_argument("--runtime-candidate", action="store_true", help="Complete Windows candidate pending native offline validation; never publication-ready")
    parser.add_argument("--separate-runtime-sources", action="store_true", help="Deliver matching worker source archives in a companion ZIP, which must accompany the release")
    args = parser.parse_args()
    runtime_bundle = None
    if platform.system().lower() == "windows" and not args.desktop_only:
        runtime_bundle = args.runtime_bundle or ROOT / "build/windows-runtime"
    if args.separate_runtime_sources and runtime_bundle is None:
        parser.error("--separate-runtime-sources requires a complete Windows runtime bundle")
    command, artifact = build_command(runtime_bundle=runtime_bundle, desktop_only=args.desktop_only,
                                      runtime_candidate=args.runtime_candidate)
    (ROOT / "build" / "specs").mkdir(parents=True, exist_ok=True)
    payload = prepare_release_materials(ROOT, artifact, runtime_bundle=runtime_bundle,
                                        runtime_candidate=args.runtime_candidate)
    if runtime_bundle is not None:
        command = runtime_build_command(command, runtime_bundle)
    subprocess.run(command, cwd=ROOT, check=True)
    if not artifact.is_file():
        raise SystemExit(f"Build did not produce the expected executable: {artifact}")
    print(f"Built standalone executable: {artifact}")
    package = package_release(ROOT, artifact, payload, runtime_bundle=runtime_bundle,
                              runtime_candidate=args.runtime_candidate,
                              separate_runtime_sources=args.separate_runtime_sources)
    print(f"Built release package: {package}")


if __name__ == "__main__":
    main()
