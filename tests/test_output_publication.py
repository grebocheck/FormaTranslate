from __future__ import annotations

import json
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from support import FakeProvider, local_settings, make_pptx

from forma.core.models import Cancelled, FormaError, Options, TranslationResult
from forma.documents.ooxml import OfficeDocument
from forma.memory.store import TranslationMemory
from forma.persistence.files import available_output, output_artifacts, publish_artifacts
from forma.providers.client import JobControl
from forma.translation.reports import write_translation_report


@pytest.mark.parametrize("suffix", [".docx", ".xlsx", ".pptx", ".pdf"])
def test_output_selection_preserves_leftover_artifacts(tmp_path, suffix):
    source = tmp_path / ("source" + suffix)
    candidate = tmp_path / ("source.en" + suffix)
    for artifact in output_artifacts(candidate):
        artifact.write_bytes(b"existing user data")
        assert available_output(source, "en", tmp_path).name == "source.en.2" + suffix
        assert artifact.read_bytes() == b"existing user data"
        artifact.unlink()


def test_output_selection_does_not_reuse_a_dangling_symlink(tmp_path):
    source = tmp_path / "source.docx"
    sidecar = tmp_path / "source.en.docx.forma.json"
    try:
        sidecar.symlink_to(tmp_path / "missing-report")
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    assert available_output(source, "en", tmp_path).name == "source.en.2.docx"
    assert sidecar.is_symlink()


def test_report_collision_rolls_back_only_new_artifacts(tmp_path):
    output = tmp_path / "translated.pdf"
    result = TranslationResult(tmp_path / "source.pdf", output, [], {}, [])
    report = output.with_name(output.name + ".forma.json")
    page = report.with_suffix(".html")
    page.write_bytes(b"existing report with review notes")
    usage = output.with_name(output.name + ".api.json")
    staged_document = tmp_path / "worker-result.pdf"
    staged_document.write_bytes(b"translated PDF")
    with pytest.raises(FileExistsError):
        write_translation_report(
            result, Options(), "offline-model",
            additional_artifacts={output: staged_document, usage: b"{}"},
        )
    assert page.read_bytes() == b"existing report with review notes"
    assert not output.exists() and not report.exists() and not usage.exists()
    assert result.report is None
    assert staged_document.read_bytes() == b"translated PDF"
    assert not list(tmp_path.glob(".forma-*"))


def test_complete_bundle_references_final_paths_and_preserves_existing_report(tmp_path):
    output = tmp_path / "translated.docx"
    result = TranslationResult(tmp_path / "source.docx", output, [], {}, [])
    stage = tmp_path / "staged.docx"
    stage.write_bytes(b"translated document")
    write_translation_report(
        result, Options(), "offline-model", additional_artifacts={output: stage},
    )
    assert output.read_bytes() == stage.read_bytes()
    assert json.loads(result.report.read_bytes())["output"] == str(output)
    previous = {path: path.read_bytes() for path in output_artifacts(output)}
    fresh_result = TranslationResult(result.source, output, [], {}, [])
    with pytest.raises(FileExistsError):
        write_translation_report(fresh_result, Options(), "different-model")
    assert all(path.read_bytes() == data for path, data in previous.items())


def test_staging_failure_publishes_nothing(tmp_path, monkeypatch):
    def disk_full(_):
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError, match="No space"):
        publish_artifacts({tmp_path / "document.docx": b"document", tmp_path / "report": b"report"})
    assert list(tmp_path.iterdir()) == []


def test_parallel_publishers_never_mix_document_and_reports(tmp_path):
    barrier = Barrier(2)
    output, report = tmp_path / "document.docx", tmp_path / "document.docx.forma.json"

    def publish(value):
        barrier.wait(timeout=5)
        try:
            publish_artifacts({output: value, report: value})
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, [b"first", b"second"]))
    assert outcomes.count(True) == 1
    assert output.read_bytes() == report.read_bytes()
    assert not list(tmp_path.glob(".forma-*"))


def test_failed_publication_does_not_remove_an_external_replacement(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    real_link = os.link

    def fail_after_replacement(source, target):
        if Path(target) == second:
            first.unlink()
            first.write_bytes(b"external replacement")
            raise OSError("publication interrupted")
        real_link(source, target)

    monkeypatch.setattr(os, "link", fail_after_replacement)
    with pytest.raises(OSError, match="interrupted"):
        publish_artifacts({first: b"our file", second: b"our second file"})
    assert first.read_bytes() == b"external replacement"
    assert not second.exists() and not list(tmp_path.glob(".forma-*"))


@pytest.mark.parametrize("use_memory", [False, True])
@pytest.mark.parametrize("damage", ["crc", "deflate"])
def test_corrupt_untranslated_media_fails_before_any_provider_request(tmp_path, use_memory, damage):
    from forma.translation.engine import TranslationEngine

    source = make_pptx(tmp_path / "damaged.pptx")
    compression = ZIP_DEFLATED if damage == "deflate" else ZIP_STORED
    with ZipFile(source, "a", compression=compression) as archive:
        archive.writestr("ppt/media/corrupt.bin", b"synthetic media bytes")
        info = archive.getinfo("ppt/media/corrupt.bin")
    raw = bytearray(source.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", raw, info.header_offset + 26)
    payload_offset = info.header_offset + 30 + name_length + extra_length
    if damage == "deflate":
        raw[payload_offset] |= 0b111  # Reserved DEFLATE block type.
    else:
        raw[payload_offset] ^= 1
    source.write_bytes(raw)

    class Provider:
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Corrupt documents must not incur provider requests")

    provider = Provider()
    options = Options("en", "uk", auto_glossary=False)
    if use_memory:
        project = TranslationMemory().create_project("Corrupt deck", "en", "uk")
        options.memory_project_id = project.id
    folder = tmp_path / "output"
    with pytest.raises(FormaError, match="пошкоджений"):
        TranslationEngine(local_settings(), provider=provider).translate(source, folder, options)
    assert provider.calls == 0
    assert not folder.exists() or not list(folder.iterdir())


@pytest.mark.parametrize("use_memory", [False, True])
def test_cancellation_after_document_staging_does_not_publish_a_bundle(
    docx_file, tmp_path, monkeypatch, use_memory,
):
    from forma.translation.engine import TranslationEngine

    control = JobControl()
    original_write = OfficeDocument.write

    def cancel_after_staging(document, *args, **kwargs):
        original_write(document, *args, **kwargs)
        control.cancelled.set()

    monkeypatch.setattr(OfficeDocument, "write", cancel_after_staging)
    options = Options("en", "uk", auto_glossary=False, review=False)
    if use_memory:
        options.memory_project_id = TranslationMemory().create_project("Cancelled bundle", "en", "uk").id
    folder = tmp_path / "output"
    with pytest.raises(Cancelled):
        TranslationEngine(local_settings(), control, provider=FakeProvider()).translate(
            docx_file, folder, options,
        )
    assert list(folder.iterdir()) == []
