from __future__ import annotations

import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import forma.review_store as module
from forma.models import FormaError
from forma.review_store import ReviewStore

ENTRY_FINGERPRINT = hashlib.sha256(b"source, translation, current findings").hexdigest()


@pytest.fixture
def report(tmp_path):
    path = tmp_path / "translated.docx.forma.json"
    path.write_text('{"engine":"forma-ooxml","segments":[]}', encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / "reviews")


def snapshot(store):
    paths = list(store.path.glob("*.json"))
    assert len(paths) == 1
    return paths[0]


def seed(store, report):
    state = store.load(report)
    store.save(
        report,
        {"segment:1": ENTRY_FINGERPRINT},
        {"segment:1": "<r0>Перевірений переклад</r0>"},
        expected_fingerprint=state["reportFingerprint"],
    )
    return state["reportFingerprint"]


def test_default_storage_is_private_review_directory(tmp_path):
    store = ReviewStore()
    assert store.path == tmp_path / "appdata" / "reviews"
    assert store.path.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o700


def test_restart_preserves_decisions_and_unicode_markup_drafts_without_touching_report(store, report):
    original = report.read_bytes()
    baseline = store.load(report)
    assert baseline == {
        "reportFingerprint": hashlib.sha256(original).hexdigest(),
        "accepted": {},
        "edits": {},
    }
    assert list(store.path.iterdir()) == []
    accepted = {"document:images": ENTRY_FINGERPRINT}
    edits = {"segment:1": '<r0>Насос &amp; фільтр</r0><k1/><r2>Manual draft ✨</r2>', "empty": ""}
    store.save(report, accepted, edits, expected_fingerprint=baseline["reportFingerprint"])
    assert report.read_bytes() == original
    restored = ReviewStore(store.path).load(report)
    assert restored == {**baseline, "accepted": accepted, "edits": edits}
    restored["edits"]["segment:1"] = "Uncommitted edit"
    assert store.load(report)["edits"] == edits


def test_snapshot_has_only_document_decisions_and_no_source_path_or_settings(store, report):
    seed(store, report)
    path = snapshot(store)
    payload = json.loads(path.read_text())
    assert set(payload) == {"version", "reportFingerprint", "accepted", "edits"}
    assert str(report) not in path.read_text()
    assert report.name not in path.name
    assert len(path.stem) == 64
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_changed_report_invalidates_state_without_rewriting_it_and_rejects_stale_save(store, report):
    original_fingerprint = seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()
    report.write_text('{"engine":"forma-ooxml","segments":[],"changed":true}')
    current_report = report.read_bytes()
    loaded = store.load(report)
    assert loaded["reportFingerprint"] != original_fingerprint
    assert loaded["accepted"] == {} and loaded["edits"] == {}
    assert path.read_bytes() == previous

    with pytest.raises(FormaError, match="Звіт змінився"):
        store.save(report, {}, {"segment:1": "Stale edit"}, expected_fingerprint=original_fingerprint)
    assert path.read_bytes() == previous
    assert report.read_bytes() == current_report
    store.save(
        report, {}, {"segment:2": "Current edit"}, expected_fingerprint=loaded["reportFingerprint"]
    )
    assert store.load(report) == {**loaded, "edits": {"segment:2": "Current edit"}}


def test_report_changed_while_snapshot_is_written_prevents_commit(store, report, monkeypatch):
    fingerprint = seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()
    real_fsync = module.os.fsync

    def change_during_write(fd):
        real_fsync(fd)
        report.write_text("The report was replaced during this save")

    monkeypatch.setattr(module.os, "fsync", change_during_write)
    with pytest.raises(FormaError, match="Звіт змінився"):
        store.save(report, {}, {"segment:1": "New draft"}, expected_fingerprint=fingerprint)
    assert path.read_bytes() == previous
    assert list(store.path.glob("*.tmp")) == []
    assert store.load(report)["edits"] == {}


def test_identical_report_copies_have_independent_review_state(store, report, tmp_path):
    seed(store, report)
    duplicate = tmp_path / "copied.docx.forma.json"
    duplicate.write_bytes(report.read_bytes())
    copied = store.load(duplicate)
    assert copied["accepted"] == {} and copied["edits"] == {}
    store.save(duplicate, {}, {"segment:1": "Copy-specific edit"})
    assert store.load(report)["edits"]["segment:1"] != "Copy-specific edit"
    assert store.load(duplicate)["edits"] == {"segment:1": "Copy-specific edit"}
    assert len(list(store.path.glob("*.json"))) == 2


def test_canonical_relative_and_absolute_paths_share_snapshot(store, report, monkeypatch):
    seed(store, report)
    monkeypatch.chdir(report.parent)
    relative = Path(".") / report.name
    assert store.load(relative) == store.load(report)
    store.save(relative, {}, {"segment:2": "Edit using relative path"})
    assert store.load(report)["edits"] == {"segment:2": "Edit using relative path"}
    assert len(list(store.path.glob("*.json"))) == 1


def test_canonical_symlink_alias_shares_snapshot(store, report, tmp_path):
    alias = tmp_path / "alias.forma.json"
    try:
        alias.symlink_to(report)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this system")
    seed(store, report)
    assert store.load(alias) == store.load(report)
    store.save(alias, {}, {"alias-edit": "Draft"})
    assert store.load(report)["edits"] == {"alias-edit": "Draft"}
    assert len(list(store.path.glob("*.json"))) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        b"invalid-private-content",
        b"\xff\xfe",
        b"null",
        b"[]",
        b"[" * 2000,
        b'{"version":1,"version":1}',
        {"version": True},
        {"version": 2},
        {"api_key": "private-key-must-not-appear-in-errors"},
        {"reportFingerprint": "invalid"},
        {"accepted": []},
        {"edits": {"segment:1": False}},
    ],
)
def test_corrupt_state_fails_safely_and_is_never_overwritten(store, report, corruption):
    seed(store, report)
    path = snapshot(store)
    if isinstance(corruption, dict):
        data = json.loads(path.read_text())
        data.update(corruption)
        raw = json.dumps(data).encode("utf-8")
    else:
        raw = corruption
    path.write_bytes(raw)
    for operation in (lambda: store.load(report), lambda: store.save(report, {}, {})):
        with pytest.raises(FormaError) as caught:
            operation()
        assert "private" not in str(caught.value)
        assert str(path) not in str(caught.value)
        assert path.read_bytes() == raw
    assert list(store.path.glob("*.tmp")) == []


def test_duplicate_ids_in_snapshot_are_rejected_instead_of_silently_overwritten(store, report):
    seed(store, report)
    path = snapshot(store)
    state = store.load(report)
    raw = (
        '{"version":1,"reportFingerprint":"' + state["reportFingerprint"]
        + '","accepted":{},"edits":{"same":"first","same":"second"}}'
    ).encode("utf-8")
    path.write_bytes(raw)
    with pytest.raises(FormaError):
        store.load(report)
    with pytest.raises(FormaError):
        store.save(report, {}, {})
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    ("accepted", "edits"),
    [
        ([], {}),
        ({}, []),
        ({"": ENTRY_FINGERPRINT}, {}),
        ({"\n": ENTRY_FINGERPRINT}, {}),
        ({"segment": "not-a-fingerprint"}, {}),
        ({"segment": True}, {}),
        ({3: ENTRY_FINGERPRINT}, {}),
        ({}, {"segment": None}),
        ({}, {"segment": {"api_key": "private-marker"}}),
        ({}, {"bad\x00id": "text"}),
        ({}, {"x" * 1025: "text"}),
        ({}, {"segment": "\ud800"}),
    ],
)
def test_invalid_maps_reject_save_without_altering_previous_snapshot(store, report, accepted, edits):
    seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()
    with pytest.raises(FormaError) as caught:
        store.save(report, accepted, edits)
    assert "private" not in str(caught.value)
    assert path.read_bytes() == previous


def test_entry_count_is_bounded_for_loading_and_saving(store, report, monkeypatch):
    monkeypatch.setattr(module, "MAX_ENTRIES", 2)
    with pytest.raises(FormaError):
        store.save(report, {str(index): ENTRY_FINGERPRINT for index in range(3)}, {})
    with pytest.raises(FormaError):
        store.save(report, {}, {str(index): "draft" for index in range(3)})
    seed(store, report)
    path = snapshot(store)
    data = json.loads(path.read_text())
    data["edits"] = {str(index): "draft" for index in range(3)}
    path.write_text(json.dumps(data))
    with pytest.raises(FormaError):
        store.load(report)


def test_report_read_is_bounded_and_allows_exact_limit(store, report, monkeypatch):
    monkeypatch.setattr(module, "MAX_REPORT_BYTES", 64)
    report.write_bytes(b"x" * 64)
    assert store.load(report)["reportFingerprint"] == hashlib.sha256(b"x" * 64).hexdigest()
    report.write_bytes(b"x" * 65)
    with pytest.raises(FormaError):
        store.load(report)
    with pytest.raises(FormaError):
        store.save(report, {}, {})
    assert list(store.path.iterdir()) == []


def test_snapshot_size_is_bounded_on_load_and_save(store, report, monkeypatch):
    seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()
    monkeypatch.setattr(module, "MAX_STATE_BYTES", len(previous))
    assert store.load(report)["edits"]
    with pytest.raises(FormaError):
        store.save(report, {}, {"segment:1": "x" * len(previous)})
    assert path.read_bytes() == previous
    path.write_bytes(previous + b" ")
    with pytest.raises(FormaError):
        store.load(report)
    with pytest.raises(FormaError):
        store.save(report, {}, {})
    assert path.read_bytes() == previous + b" "


def test_failed_atomic_replace_preserves_previous_snapshot_and_cleans_temp(store, report, monkeypatch):
    seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()

    def failure(*args, **kwargs):
        raise OSError("private-key-or-filesystem-details")

    monkeypatch.setattr(module.os, "replace", failure)
    with pytest.raises(FormaError) as caught:
        store.save(report, {}, {"segment:1": "Replacement draft"})
    assert "private" not in str(caught.value)
    assert path.read_bytes() == previous
    assert list(store.path.glob("*.tmp")) == []


def test_missing_reports_and_directory_paths_fail_with_safe_errors(store, report, tmp_path):
    missing = tmp_path / "private-missing-report.forma.json"
    for path in (missing, tmp_path):
        for operation in (
            lambda path=path: store.load(path),
            lambda path=path: store.save(path, {}, {}),
        ):
            with pytest.raises(FormaError) as caught:
                operation()
            assert "private" not in str(caught.value)
            assert str(tmp_path) not in str(caught.value)


def test_default_data_directory_errors_are_safe(monkeypatch):
    def failure():
        raise OSError("private-directory-details")

    monkeypatch.setattr(module, "data_directory", failure)
    with pytest.raises(FormaError) as caught:
        ReviewStore()
    assert "private" not in str(caught.value)


def test_invalid_expected_fingerprint_does_not_change_snapshot(store, report):
    seed(store, report)
    path = snapshot(store)
    previous = path.read_bytes()
    with pytest.raises(FormaError):
        store.save(report, {}, {}, expected_fingerprint="private-invalid-fingerprint")
    assert path.read_bytes() == previous


def test_concurrent_distinct_reports_do_not_lose_snapshots(store, tmp_path):
    reports = []
    for index in range(24):
        path = tmp_path / f"report-{index}.forma.json"
        path.write_text(json.dumps({"index": index}))
        reports.append(path)

    def save_one(item):
        index, path = item
        baseline = store.load(path)
        store.save(
            path,
            {"same-id": ENTRY_FINGERPRINT},
            {"same-id": f"Draft for report {index}"},
            expected_fingerprint=baseline["reportFingerprint"],
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save_one, enumerate(reports)))
    restarted = ReviewStore(store.path)
    for index, report in enumerate(reports):
        assert restarted.load(report)["edits"] == {"same-id": f"Draft for report {index}"}
    assert len(list(store.path.glob("*.json"))) == 24
    assert list(store.path.glob("*.tmp")) == []


def test_empty_snapshot_explicitly_removes_all_decisions_and_drafts(store, report):
    fingerprint = seed(store, report)
    original = report.read_bytes()
    store.save(report, {}, {}, expected_fingerprint=fingerprint)
    assert store.load(report) == {"reportFingerprint": fingerprint, "accepted": {}, "edits": {}}
    assert report.read_bytes() == original
