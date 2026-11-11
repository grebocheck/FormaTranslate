"""Consolidate exact corresponding-source archives into one release download."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

MAX_ASSET_BYTES = 2 * 1024**3
_CHUNK = 1024 * 1024
_MANIFEST = "MANIFEST.json"
_README = "README.txt"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _archive_name(name: str) -> bool:
    return (
        Path(name).name == name and "/" not in name and "\\" not in name and ":" not in name and "\0" not in name
        and not name.startswith(".")
        and name.endswith((".zip", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz"))
    )


def verify_source_bundle(path: Path, expected: dict[str, str] | None = None) -> dict:
    """Check every original archive against both inventory and external hashes."""
    with zipfile.ZipFile(path) as bundle:
        if bundle.getinfo(_MANIFEST).file_size > _CHUNK:
            raise ValueError("Source inventory is too large")
        manifest = json.loads(bundle.read(_MANIFEST))
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest.get("schema_version") != 1 or manifest.get("algorithm") != "sha256"):
            raise ValueError("Unsupported source inventory")
        if bundle.getinfo(_README).file_size > _CHUNK:
            raise ValueError("Source readme is too large")
        bundle.read(_README)  # Verify its CRC as well as the archive entries below.
        records = manifest.get("archives")
        if not isinstance(records, list) or not records:
            raise ValueError("Source inventory is empty")
        names = [row.get("name") for row in records if isinstance(row, dict)]
        if (len(names) != len(records) or any(not isinstance(name, str) or not _archive_name(name) for name in names)
                or len(names) != len(set(names))):
            raise ValueError("Invalid or duplicate source archive name")
        required = set(names) | {_MANIFEST, _README}
        if set(bundle.namelist()) != required or len(bundle.namelist()) != len(required):
            raise ValueError("Missing, duplicate or unexpected source bundle members")
        if expected is not None and set(names) != set(expected):
            raise ValueError("Source bundle does not contain every expected archive")
        for row in records:
            size = 0
            checksum = hashlib.sha256()
            with bundle.open(row["name"]) as stream:
                while chunk := stream.read(_CHUNK):
                    size += len(chunk)
                    checksum.update(chunk)
            if type(row.get("bytes")) is not int or size != row["bytes"] or checksum.hexdigest() != row.get("sha256"):
                raise ValueError("Source archive content does not match inventory: " + row["name"])
            if expected is not None and checksum.hexdigest() != expected[row["name"]]:
                raise ValueError("Original source archive hash changed: " + row["name"])
        return manifest


def create_source_bundle(
    archives: list[Path], destination: Path, version: str, *, expected: dict[str, str],
) -> dict:
    """Store upstream archives unchanged; atomically publish only a verified ZIP.

    Original files stay available. The caller may remove old remote assets only
    after the new bundle has been uploaded and its public access/hash verified.
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Invalid release version")
    inputs = sorted((Path(path) for path in archives), key=lambda path: path.name)
    if not inputs or len({path.name for path in inputs}) != len(inputs):
        raise ValueError("Source archive list is empty or contains duplicate names")
    if set(expected) != {path.name for path in inputs}:
        raise ValueError("Expected hashes must cover every selected source archive")
    if any(not _archive_name(path.name) or not path.is_file() or path.is_symlink() for path in inputs):
        raise ValueError("Expected regular source archives with plain filenames")
    if sum(path.stat().st_size for path in inputs) >= MAX_ASSET_BYTES:
        raise ValueError("Source bundle exceeds the release asset size limit")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".sources-", suffix=".zip", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        records = []
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for source in inputs:
                checksum = hashlib.sha256()
                size = 0
                with source.open("rb") as stream, bundle.open(source.name, "w", force_zip64=True) as target:
                    while chunk := stream.read(_CHUNK):
                        checksum.update(chunk)
                        size += len(chunk)
                        target.write(chunk)
                value = checksum.hexdigest()
                if value != expected[source.name]:
                    raise ValueError("Original source archive hash changed: " + source.name)
                records.append({"name": source.name, "bytes": size, "sha256": value})
            manifest = {
                "schema_version": 1, "release": version, "algorithm": "sha256",
                "delivery": "Original source archives stored without modification",
                "archives": records,
            }
            bundle.writestr(_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            bundle.writestr(_README, (
                f"FormaTranslate {version}: corresponding source archives\n\n"
                "This developer/source download is not required to run the application.\n"
                "The Windows and Linux application ZIPs are the end-user downloads.\n\n"
                "Every original archive is stored under its original filename and its\n"
                "bytes are unchanged. MANIFEST.json records size and SHA256 for each.\n"
                "The application/worker source ZIPs contain the release's build\n"
                "instructions. Each component retains its original license.\n\n"
                "For Windows runtime corresponding sources, extract the contained\n"
                "*-runtime-sources.zip into the same directory as the application\n"
                "package when rebuilding or inspecting dependencies. This restores\n"
                "the runtime-sources paths referenced by its included inventories.\n"
                "Linux dependency sources and Qt/PySide archives are included too.\n"
            ))
        if temporary.stat().st_size >= MAX_ASSET_BYTES:
            raise ValueError("Source bundle exceeds the release asset size limit")
        verify_source_bundle(temporary, expected)
        os.link(temporary, destination)  # Do not overwrite a concurrent publication.
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Consolidate selected archives, checking their existing hashes")
    create.add_argument("archives", type=Path, nargs="+")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--inventory", type=Path, required=True, help="Existing JSON with assets [{name, sha256}]")
    verify = commands.add_parser("verify", help="Verify every original archive and the complete inventory")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
            known = {row["name"]: row["sha256"] for row in inventory["assets"]}
            expected = {path.name: known[path.name] for path in args.archives}
            manifest = create_source_bundle(args.archives, args.output, args.version, expected=expected)
            result = {"bundle": str(args.output), "bytes": args.output.stat().st_size,
                      "sha256": digest(args.output), "archives": len(manifest["archives"])}
        else:
            manifest = verify_source_bundle(args.bundle)
            result = {"bundle": str(args.bundle), "release": manifest["release"],
                      "archives": len(manifest["archives"]), "verified": True}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
