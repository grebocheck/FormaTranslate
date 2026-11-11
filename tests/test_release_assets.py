from __future__ import annotations

import hashlib
import json
import runpy
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def release_assets():
    return runpy.run_path(str(Path(__file__).parents[1] / "scripts/release_assets.py"))


def inputs(tmp_path):
    first = tmp_path / "component-1.0.tar.gz"
    first.write_bytes(b"exact original compressed source\x00\xff")
    second = tmp_path / "application-source.zip"
    with zipfile.ZipFile(second, "w") as archive:
        archive.writestr("src/example.py", "print('public example')")
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)}
    return [first, second], expected


def test_source_delivery_keeps_original_archives_and_complete_hash_inventory(tmp_path, release_assets):
    archives, expected = inputs(tmp_path)
    destination = tmp_path / "Sources-7.8.9.zip"
    manifest = release_assets["create_source_bundle"](archives, destination, "7.8.9", expected=expected)
    assert {row["name"]: row["sha256"] for row in manifest["archives"]} == expected
    assert release_assets["verify_source_bundle"](destination, expected) == manifest
    with zipfile.ZipFile(destination) as bundle:
        assert set(bundle.namelist()) == {path.name for path in archives} | {"README.txt", "MANIFEST.json"}
        for source in archives:
            assert bundle.read(source.name) == source.read_bytes()
            assert bundle.getinfo(source.name).compress_type == zipfile.ZIP_STORED
        assert "not required to run" in bundle.read("README.txt").decode()
    assert all(source.is_file() for source in archives)


def test_bad_original_hash_does_not_publish_or_remove_originals(tmp_path, release_assets):
    archives, expected = inputs(tmp_path)
    archives[0].write_bytes(b"changed original archive")
    with pytest.raises(ValueError, match="hash changed"):
        release_assets["create_source_bundle"](archives, tmp_path / "Sources.zip", "7.8.9", expected=expected)
    assert not (tmp_path / "Sources.zip").exists()
    assert not list(tmp_path.glob(".sources-*"))
    assert all(path.is_file() for path in archives)


def test_existing_bundle_is_never_replaced(tmp_path, release_assets):
    archives, expected = inputs(tmp_path)
    destination = tmp_path / "Sources.zip"
    destination.write_bytes(b"published bundle")
    with pytest.raises(FileExistsError):
        release_assets["create_source_bundle"](archives, destination, "7.8.9", expected=expected)
    assert destination.read_bytes() == b"published bundle"


def test_missing_or_modified_inner_source_is_detected_even_if_inventory_looks_valid(tmp_path, release_assets):
    archives, expected = inputs(tmp_path)
    good = tmp_path / "Sources.zip"
    release_assets["create_source_bundle"](archives, good, "7.8.9", expected=expected)
    with zipfile.ZipFile(good) as bundle:
        members = {name: bundle.read(name) for name in bundle.namelist()}
    modified = tmp_path / "changed.zip"
    with zipfile.ZipFile(modified, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, b"substituted source" if name == archives[0].name else data)
    with pytest.raises(ValueError, match="does not match inventory"):
        release_assets["verify_source_bundle"](modified, expected)
    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as bundle:
        for name, data in members.items():
            if name != archives[0].name:
                bundle.writestr(name, data)
    with pytest.raises(ValueError, match="Missing"):
        release_assets["verify_source_bundle"](missing, expected)


def test_external_inventory_rejects_omitted_sources_and_rewritten_manifest(tmp_path, release_assets):
    archives, expected = inputs(tmp_path)
    good = tmp_path / "Sources.zip"
    release_assets["create_source_bundle"](archives, good, "7.8.9", expected=expected)
    with pytest.raises(ValueError, match="every expected archive"):
        release_assets["verify_source_bundle"](good, {**expected, "omitted.tar.xz": "0" * 64})
    with zipfile.ZipFile(good) as bundle:
        members = {name: bundle.read(name) for name in bundle.namelist()}
    name = archives[0].name
    members[name] = b"substituted source"
    manifest = json.loads(members["MANIFEST.json"])
    row = next(row for row in manifest["archives"] if row["name"] == name)
    row.update(bytes=len(members[name]), sha256=hashlib.sha256(members[name]).hexdigest())
    members["MANIFEST.json"] = json.dumps(manifest).encode()
    changed = tmp_path / "rewritten.zip"
    with zipfile.ZipFile(changed, "w") as bundle:
        for member, data in members.items():
            bundle.writestr(member, data)
    with pytest.raises(ValueError, match="Original source archive hash changed"):
        release_assets["verify_source_bundle"](changed, expected)
