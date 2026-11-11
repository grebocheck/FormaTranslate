from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from support import pump as pump_events

from forma.presentation.backend import Backend
from forma.settings.config import Settings


def pump(app, predicate, seconds=10):
    pump_events(app, predicate, timeout=seconds)


class LocalAPI(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payload = json.loads(body["messages"][1]["content"])
        segments = [
            {
                "id": item["id"],
                "text": item["text"]
                .replace("Heat meter", "Лічильник тепла")
                .replace("sensor", "датчик")
                .replace("The ", "Значення ")
                .replace("maximum operating pressure", "максимального робочого тиску")
                .replace(
                    " must not exceed 16 bar. Maintain a flow rate of 2.5 m³/h.",
                    " не має перевищувати 16 бар. Підтримуйте витрату 2.5 м³/год.",
                )
                .replace(
                    "Check the flow direction before installation.",
                    "Перевірте напрямок потоку перед установленням.",
                )
                .replace(
                    "Record the serial number and initial reading.",
                    "Запишіть серійний номер і початкові покази.",
                ),
            }
            for item in payload["segments"]
        ]
        data = json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": json.dumps({"segments": segments})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_desktop_import_translate_review_all_pages(qapp, docx_file, xlsx_file, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    backend = Backend()
    backend._settings = Settings(
        base_url=f"http://127.0.0.1:{server.server_port}/v1", model="local-test"
    )
    backend.setOutputFolder(str(tmp_path / "results"))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    try:
        assert not root.property("translationOptionsOpen")
        assert not root.property("previewOpen")
        backend.addFiles([QUrl.fromLocalFile(str(docx_file)), QUrl.fromLocalFile(str(xlsx_file))])
        pump(qapp, lambda: all(d["status"] == "ready" for d in backend.documents))
        assert backend.segments
        backend.start("en", "uk", "Технічна документація", True, False)
        pump(qapp, lambda: not backend.busy)
        assert all(d["status"] == "done" for d in backend.documents), backend.message
        assert any(segment["translation"] for segment in backend.segments)
        for document in backend.documents:
            assert Path(document["output"]).is_file()
            assert Path(document["report"]).is_file()
        assert len(backend.historyEntries) == 2
        pump(qapp, lambda: all(entry["outputExists"] for entry in backend.historyEntries))
        assert all(
            entry["outputExists"] and entry["model"] == "local-test"
            for entry in backend.historyEntries
        )
        backend.selectReviewDocument(backend.documents[0]["id"])
        pump(qapp, lambda: not backend._threads)
        assert backend.reviewSummary["hasResult"]
        assert backend.reviewSummary["segments"] > 0
        for page in range(4):
            root.setProperty("page", page)
            qapp.processEvents()
            assert not root.grabWindow().isNull()
        for setting in (
            "translationOptionsOpen",
            "previewOpen",
            "connectionOptionsOpen",
            "pdfOptionsOpen",
        ):
            root.setProperty(setting, True)
        for page in (0, 2):
            root.setProperty("page", page)
            qapp.processEvents()
            assert not root.grabWindow().isNull()
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancel()
        pump(qapp, lambda: not backend._threads)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        root.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
