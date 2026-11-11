from __future__ import annotations

from forma.documents.pdf_progress import PDFProgress


def test_rich_frames_report_real_counters_without_reverting_to_completed_stages():
    updates = []
    progress = PDFProgress(lambda *value: updates.append(value))
    progress.feed(
        b"\x1b[2Ktranslate ---------------- 40/100 0:00:12\r\n"
        b"Parse Page Layout (1/1) ---------------- 4/4 0:00:03\r\n"
        b"Translate Paragraphs (1/1) ---------------- 12/50 0:00:09\r\n"
    )
    assert updates[-1][0] == 42
    assert "Перекладаємо абзаци" in updates[-1][1] and "12/50" in updates[-1][1]
    size = len(updates)
    progress.feed(b"Parse Page Layout (1/1) ---------------- 4/4 0:00:03\n")
    assert len(updates) == size
    # A frame may span reads; completion is kept below Forma's validation stage.
    progress.feed(b"translate ---------------- 100/100\nSave PDF (1/1) ---- ")
    progress.feed(b"2/2\n", final=True)
    assert updates[-1][0] == 94
    assert "Зберігаємо PDF" in updates[-1][1]


def test_tqdm_frames_and_elapsed_updates_never_invent_percentage():
    updates, clock = [], [10.0]
    progress = PDFProgress(lambda *value: updates.append(value), clock=lambda: clock[0])
    progress.feed(b"Translate Paragraphs (3/20):  52%|xxxxx| 52/100 [00:12]\r")
    assert updates[-1][0] == 52
    assert "3/20" in updates[-1][1]
    clock[0] += 35
    progress.tick()
    assert updates[-1][0] == 52
    assert "35 с" in updates[-1][1]
    assert "очікуємо оновлення" in updates[-1][1]


def test_resource_and_retry_notices_do_not_expose_raw_logs_or_credentials():
    updates = []
    progress = PDFProgress(lambda *value: updates.append(value))
    progress.feed(b"INFO doclayout onnx model not found or corrupted, downloading... secret-key\n")
    assert "Завантажуємо ресурси" in updates[-1][1]
    progress.feed(b"WARNING Retrying OpenAITranslator after RateLimitError 429 secret-key\n")
    assert "API обмежив" in updates[-1][1]
    progress.feed(b"API request Authorization: Bearer secret-key\n")
    assert all("secret-key" not in message for _, message in updates)
    assert all(percent == 8 for percent, _ in updates)


def test_unknown_renderer_keeps_startup_status_and_bounds_partial_line_memory():
    updates = []
    progress = PDFProgress(lambda *value: updates.append(value))
    progress.feed(b"x" * 200000)
    assert len(progress.buffer) <= 65536
    assert len(updates) == 1
    assert updates[0][0] == 8
