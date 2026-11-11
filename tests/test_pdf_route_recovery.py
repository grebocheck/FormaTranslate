"""Route hints survive completed scan recovery without starting paid work."""
from test_backend_ocr import settle
from test_ocr import scan as scan

from forma.presentation.backend import Backend
from forma.settings.config import Settings


def test_completed_scan_keeps_ocr_route_hint_after_restart(qapp, monkeypatch, scan):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.addFiles([str(scan)])
    settle(qapp, backend)
    assert backend.selectedDocument["pdfRoute"] == "ocr"
    assert "Скан PDF" in backend.selectedDocument["workflowText"]
    backend._documents[0].update(status="done", progress=100)
    backend._persist_queue()

    restarted = Backend()
    settle(qapp, restarted)
    assert restarted.selectedDocument["status"] == "done"
    assert restarted.selectedDocument["pdfRoute"] == "ocr"
    assert "Скан PDF" in restarted.selectedDocument["workflowText"]
    assert not restarted.busy


def test_old_completed_pdf_without_route_does_not_claim_native_text(qapp, monkeypatch, scan):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.addFiles([str(scan)])
    settle(qapp, backend)
    backend._documents[0].update(status="done", progress=100)
    backend._documents[0].pop("pdfRoute")
    backend._persist_queue()

    restarted = Backend()
    settle(qapp, restarted)
    assert restarted.selectedDocument["status"] == "done"
    assert not restarted.selectedDocument.get("workflowText")
    assert not restarted.busy
