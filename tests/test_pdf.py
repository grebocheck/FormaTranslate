from __future__ import annotations

import hashlib
import os
import tomllib
from contextlib import contextmanager
from pathlib import Path

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen.canvas import Canvas

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.pdf import _check_pdf_language, inspect_pdf, translate_pdf
from forma.providers.client import JobControl
from forma.settings.config import Settings


def test_pdf_adapter_keeps_key_out_of_arguments(pdf_file, tmp_path, monkeypatch):
    calls = []
    config_paths = []
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Process:
        returncode = 0

        def __init__(self, arguments, **kwargs):
            calls.append(arguments)
            assert "secret-test-key" not in " ".join(arguments)
            config_path = Path(arguments[arguments.index("--config") + 1])
            config_paths.append(config_path)
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))["babeldoc"]
            assert config["openai-api-key"] != "secret-test-key"
            assert len(config["openai-api-key"]) >= 32
            assert config["openai-base-url"].startswith("http://127.0.0.1:")
            assert config["openai-model"].startswith("test-forma-")
            assert config["lang-out"] == "en"
            assert config["no-send-temperature"]
            assert (
                '"translation_preferences": "Use formal technical language."'
                in config["custom-system-prompt"]
            )
            assert "Use formal" not in " ".join(arguments)
            if os.name != "nt":
                assert config_path.stat().st_mode & 0o777 == 0o600
            (Path(config["output"]) / "sample.mono.pdf").write_bytes(pdf_file.read_bytes())

        def poll(self):
            return 0

    monkeypatch.setattr("forma.documents.pdf.subprocess.Popen", Process)
    result = translate_pdf(
        pdf_file,
        tmp_path / "out",
        Options(
            source_language="en",
            target_language="en",
            instructions="Use formal technical language.",
        ),
        Settings(model="test", api_key="secret-test-key"),
        JobControl(),
        lambda *_: None,
    )
    assert result.output.exists()
    assert result.report.exists()
    assert result.engine == "babeldoc"
    assert result.source_sha256 == hashlib.sha256(pdf_file.read_bytes()).hexdigest()
    assert result.output_sha256 == hashlib.sha256(result.output.read_bytes()).hexdigest()
    assert not config_paths[0].exists()
    assert len(calls) == 1
    assert "secret-test-key" not in result.report.read_text()


def test_changed_pdf_source_is_rejected_before_any_output_is_published(pdf_file, tmp_path, monkeypatch):
    original = pdf_file.read_bytes()
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    def run(arguments, *_):
        config_path = Path(arguments[arguments.index("--config") + 1])
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))["babeldoc"]
        (Path(config["output"]) / "translated.mono.pdf").write_bytes(original)
        pdf_file.write_bytes(original + b"\nchanged during translation\n")

    monkeypatch.setattr("forma.documents.pdf._run_babeldoc", run)
    folder = tmp_path / "out"
    with pytest.raises(FormaError, match="Оригінал змінився"):
        translate_pdf(
            pdf_file, folder, Options(source_language="en", target_language="en"),
            Settings(model="test"), JobControl(), lambda *_: None,
        )
    assert not folder.exists() or not list(folder.iterdir())


def test_pdf_scans_fail_explicitly(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(path)
    assert inspect_pdf(path)["scanned"]
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")
    with pytest.raises(FormaError, match="OCR"):
        translate_pdf(
            path,
            tmp_path / "out",
            Options(source_language="en"),
            Settings(model="test"),
            JobControl(),
            lambda *_: None,
        )


def test_pdf_requires_source_language(pdf_file, tmp_path, monkeypatch):
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")
    with pytest.raises(FormaError, match="мову оригіналу"):
        translate_pdf(
            pdf_file,
            tmp_path / "out",
            Options(),
            Settings(model="test"),
            JobControl(),
            lambda *_: None,
        )


def _fake_engine_output(monkeypatch, candidate):
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Process:
        returncode = 0

        def __init__(self, arguments, **kwargs):
            config_path = Path(arguments[arguments.index("--config") + 1])
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))["babeldoc"]
            (Path(config["output"]) / "translated.mono.pdf").write_bytes(candidate.read_bytes())

        def poll(self):
            return 0

    monkeypatch.setattr("forma.documents.pdf.subprocess.Popen", Process)


def _pdf_with_pages(path, texts):
    canvas = Canvas(str(path), pagesize=(595, 842))
    for text in texts:
        if text:
            canvas.drawString(40, 750, text)
        canvas.showPage()
    canvas.save()
    return path


def _translate(source, folder):
    return translate_pdf(
        source,
        folder,
        Options(source_language="en", target_language="uk"),
        Settings(model="test"),
        JobControl(),
        lambda *_: None,
    )


def test_blank_cover_is_preserved_and_unchanged_text_is_flagged(tmp_path, monkeypatch):
    source = _pdf_with_pages(
        tmp_path / "blank-cover.pdf",
        ["", "The technical inspection identifies maintenance requirements for the installation."],
    )
    assert not inspect_pdf(source)["scanned"]
    _fake_engine_output(monkeypatch, source)
    result = _translate(source, tmp_path / "out")
    assert len(inspect_pdf(result.output)["pages"]) == 2
    warnings = [issue for issue in result.issues if issue.code == "pdf_unchanged_pages"]
    assert len(warnings) == 1
    assert "2" in warnings[0].message


def test_mixed_image_page_is_reported_without_rejecting_illustrated_cover(tmp_path, monkeypatch):
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    text_pdf = _pdf_with_pages(
        tmp_path / "text.pdf", ["This page contains translatable body text."]
    )
    writer = PdfWriter()
    cover = writer.add_blank_page(width=595, height=842)
    image = DecodedStreamObject()
    image.set_data(bytes([255, 255, 255]))
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    cover[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Cover"): writer._add_object(image)}
            )
        }
    )
    writer.append(text_pdf)
    source = tmp_path / "illustrated.pdf"
    writer.write(source)
    inspected = inspect_pdf(source)
    assert not inspected["scanned"]
    assert inspected["pages"][0]["image_without_text"]
    _fake_engine_output(monkeypatch, source)
    result = _translate(source, tmp_path / "out")
    warnings = [issue for issue in result.issues if issue.code == "pdf_image_text"]
    assert len(warnings) == 1
    assert "1" in warnings[0].message and "OCR" in warnings[0].message


def test_textual_page_cannot_become_blank(tmp_path, monkeypatch):
    source = _pdf_with_pages(tmp_path / "source.pdf", ["Source text must survive translation."])
    candidate = _pdf_with_pages(tmp_path / "candidate.pdf", [""])
    _fake_engine_output(monkeypatch, candidate)
    with pytest.raises(FormaError, match="сторінці 1 зник"):
        _translate(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_dominantly_scanned_mixed_pdf_stops_before_engine_or_api(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "mostly-scanned.pdf"
    canvas = Canvas(str(source), pagesize=(595, 842))
    for index in range(3):
        if index == 1:
            canvas.drawString(40, 750, "Small existing text layer on just one page.")
        else:
            canvas.drawInlineImage(Image.new("RGB", (100, 100), "black"), 0, 0, 595, 842)
        canvas.showPage()
    canvas.save()
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    def forbidden(*_args, **_kwargs):
        pytest.fail("A mostly scanned PDF must not reach the engine or provider")

    monkeypatch.setattr("forma.documents.pdf.PDFAPI", forbidden)
    with pytest.raises(FormaError, match="Скан PDF → DOCX"):
        _translate(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_nearly_empty_page_cannot_hide_lost_body_text(tmp_path, monkeypatch):
    source = _pdf_with_pages(
        tmp_path / "source.pdf", ["Documented maintenance requirements. " * 20]
    )
    candidate = _pdf_with_pages(tmp_path / "candidate.pdf", ["1"])
    _fake_engine_output(monkeypatch, candidate)
    with pytest.raises(FormaError, match="сторінці 1 залишилося менше 10%"):
        _translate(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_pdf_streams_live_progress_and_reserves_its_worker_allowance(
    pdf_file, tmp_path, monkeypatch
):
    reservations, updates = [], []
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Control(JobControl):
        @contextmanager
        def request_slots(self, count=1):
            with super().request_slots(count):
                reservations.append(("acquired", count))
                try:
                    yield
                finally:
                    reservations.append(("released", count))

    class Process:
        returncode = 0

        def __init__(self, arguments, **kwargs):
            assert reservations == [("acquired", 3)]
            self.log = kwargs["stdout"]
            assert kwargs["env"]["TTY_INTERACTIVE"] == "1"
            config_path = Path(arguments[arguments.index("--config") + 1])
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))["babeldoc"]
            assert (
                config["qps"] == config["pool-max-workers"] == config["term-pool-max-workers"] == 3
            )
            assert config["report-interval"] == 0.5
            (Path(config["output"]) / "translated.mono.pdf").write_bytes(pdf_file.read_bytes())
            self.chunks = iter(
                [
                    b"translate ------- 40/100\nParse Page Layout (1/1) ----- 1/2\n",
                    b"translate ------- 65/100\nTranslate Paragraphs (1/1) ----- 8/12\n",
                ]
            )

        def poll(self):
            chunk = next(self.chunks, None)
            if chunk:
                self.log.write(chunk)
                return None
            return 0

    monkeypatch.setattr("forma.documents.pdf.subprocess.Popen", Process)
    result = translate_pdf(
        pdf_file,
        tmp_path / "out",
        Options(source_language="en", target_language="en"),
        Settings(model="test", max_parallel_requests=6, parallel_documents=2),
        Control(max_parallel_requests=6),
        lambda *value: updates.append(value),
    )
    assert result.output.is_file()
    assert reservations == [("acquired", 3), ("released", 3)]
    assert any("Перекладаємо абзаци" in message and "8/12" in message for _, message in updates)
    assert any(8 < percent < 95 for percent, _ in updates)


def test_pdf_cancellation_terminates_process_and_releases_slots(pdf_file, tmp_path, monkeypatch):
    control = JobControl(max_parallel_requests=2)
    terminated = []
    monkeypatch.setattr("forma.documents.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Process:
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            control.cancelled.set()
            return self.returncode

        def terminate(self):
            self.returncode = -15
            terminated.append(True)

        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setattr("forma.documents.pdf.subprocess.Popen", Process)
    with pytest.raises(Cancelled):
        translate_pdf(
            pdf_file,
            tmp_path / "out",
            Options(source_language="en"),
            Settings(model="test", max_parallel_requests=2, parallel_documents=1),
            control,
            lambda *_: None,
        )
    assert terminated
    control.cancelled.clear()
    with control.request_slots(2):
        pass
    assert not (tmp_path / "out").exists()


def test_entire_english_document_cannot_be_exported_as_ukrainian(tmp_path, monkeypatch):
    source = _pdf_with_pages(
        tmp_path / "untranslated.pdf",
        ["The building must be inspected before the installation of thermal insulation. " * 8],
    )
    _fake_engine_output(monkeypatch, source)
    with pytest.raises(FormaError, match="мовою оригіналу"):
        _translate(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_pdf_language_gate_allows_mixed_references_and_already_target_text():
    english = "The building must be inspected before insulation work begins. " * 20
    ukrainian = "Будівлю необхідно оглянути перед початком робіт із теплоізоляції. " * 20
    before = {"pages": [{"text": english}]}
    after = {"pages": [{"text": ukrainian}, {"text": english}]}
    options = Options(source_language="en", target_language="uk")
    _check_pdf_language(before, after, options)
    _check_pdf_language(after, after, options)
    with pytest.raises(FormaError, match="мовою оригіналу"):
        _check_pdf_language(
            before, {"pages": [{"text": english.replace("must", "should")}]}, options
        )


def test_pdf_cancellation_at_final_validation_does_not_export(pdf_file, tmp_path, monkeypatch):
    _fake_engine_output(monkeypatch, pdf_file)
    control = JobControl()

    def progress(percent, _):
        if percent == 95:
            control.cancelled.set()

    with pytest.raises(Cancelled):
        translate_pdf(
            pdf_file,
            tmp_path / "out",
            Options(source_language="en", target_language="en"),
            Settings(model="test"),
            control,
            progress,
        )
    assert not (tmp_path / "out").exists()
