"""Bounded local page previews with disposable caches and isolated renderers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from forma.core.models import Cancelled, FormaError
from forma.documents.bundled_runtime import start_worker
from forma.documents.ocr import _stop_process
from forma.documents.preview_office import find_libreoffice, prepare_docx, prepare_profile

MAX_INPUT_BYTES = 200 * 1024**2
MAX_CACHE_BYTES = 256 * 1024**2
MAX_CONVERTED_BYTES = 64 * 1024**2
MAX_RETAINED_PAGES = 12


def _disk_bytes(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # LibreOffice atomically replaces files in its private profile.
    return total


def _resident_bytes(pid: int) -> int:
    if sys.platform.startswith("linux"):
        total, pending, visited = 0, [pid], set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if len(visited) > 64:
                return 2 * 1024**3
            try:
                for line in Path(f"/proc/{current}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) * 1024
                pending.extend(int(child) for child in Path(f"/proc/{current}/task/{current}/children").read_text().split())
            except (OSError, ValueError):
                pass
        return total
    return 0


@dataclass(frozen=True)
class PreviewPage:
    image_path: Path | None
    page_count: int
    width: int
    height: int
    page_index: int


@dataclass(frozen=True)
class PreviewDocument:
    page_count: int
    page_sizes: tuple[tuple[float, float], ...]


def file_signature(path: Path) -> tuple:
    path = Path(path).expanduser().resolve()
    try:
        stat = path.stat()
        if not path.is_file() or not 0 < stat.st_size <= MAX_INPUT_BYTES:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise FormaError("Файл для перегляду недоступний або перевищує 200 МБ. Перевірте його розташування.") from exc
    return str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class PreviewSession:
    def __init__(self, *, converter: str | None = None, cache_parent: Path | None = None,
                 render_timeout: float = 35, conversion_timeout: float = 90):
        self._temporary = tempfile.TemporaryDirectory(prefix="forma-preview-", dir=cache_parent)
        self._root = Path(self._temporary.name)
        self._converter = converter
        self._render_timeout = render_timeout
        self._conversion_timeout = conversion_timeout
        self._documents = OrderedDict()
        self._pages = OrderedDict()
        self._layouts = OrderedDict()
        self._display_pages = OrderedDict()
        self._retained_pages = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def _check(self, cancel):
        if self._closed.is_set() or cancel.is_set():
            raise Cancelled("Перегляд документа скасовано.")

    def _run(self, arguments, directory, timeout, cancel, *, external=False):
        self._check(cancel)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("OPENAI_", "GEMINI_", "GOOGLE_API_", "ANTHROPIC_", "FORMA_"))}
        environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", PYTHONIOENCODING="utf-8")
        if getattr(sys, "frozen", False):
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            if external:
                for name in ("LD_LIBRARY_PATH", "LIBPATH"):
                    original = environment.get(name + "_ORIG")
                    if original is None:
                        environment.pop(name, None)
                    else:
                        environment[name] = original
        if external:
            environment.update(SAL_USE_VCLPLUGIN="svp", http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9", no_proxy="")
        try:
            process = start_worker(arguments, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            raise FormaError("Не вдалося запустити локальний перегляд документа.") from exc
        start = time.monotonic()
        try:
            while process.poll() is None:
                self._check(cancel)
                if time.monotonic() - start > timeout:
                    raise FormaError("Підготовка перегляду триває надто довго. Спробуйте відкрити файл у зовнішній програмі.")
                if _disk_bytes(directory) > MAX_CACHE_BYTES or _resident_bytes(process.pid) > 1024**3:
                    raise FormaError("Документ створює завеликий перегляд. Відкрийте його у зовнішній програмі.")
                cancel.wait(0.05)
            self._check(cancel)
            if process.returncode:
                raise FormaError("Не вдалося побудувати перегляд документа. Перевірте файл у зовнішній програмі.")
        finally:
            _stop_process(process)

    def _pdf(self, source, signature, cancel):
        if signature in self._documents:
            self._documents.move_to_end(signature)
            return self._documents[signature]
        folder = Path(tempfile.mkdtemp(prefix="document-", dir=self._root))
        try:
            pdf = source
            if source.suffix.lower() == ".docx":
                converter = find_libreoffice() if self._converter is None else self._converter
                if not converter:
                    raise FormaError("Для перегляду сторінок DOCX установіть LibreOffice. Текст документа можна переглянути без нього.")
                copied = folder / "preview.docx"
                prepare_docx(source, copied, lambda: self._check(cancel))
                profile = folder / "profile"
                prepare_profile(profile)
                self._run([converter, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo", "--nodefault", "--norestore", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(folder), str(copied)], folder, self._conversion_timeout, cancel, external=True)
                pdf = folder / "preview.pdf"
                if not pdf.is_file() or not 0 < pdf.stat().st_size <= MAX_CONVERTED_BYTES:
                    raise FormaError("LibreOffice не створив PDF для перегляду. Перевірте, чи відкривається DOCX у LibreOffice.")
                copied.unlink()
                shutil.rmtree(profile, ignore_errors=True)
            if file_signature(source) != signature:
                raise Cancelled("Документ змінився під час підготовки перегляду.")
            self._documents[signature] = (folder, pdf)
            return folder, pdf
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    @staticmethod
    def _worker_arguments(pdf, metadata):
        arguments = ([sys.executable, "preview-worker"] if getattr(sys, "frozen", False)
                     else [sys.executable, "-m", "forma.documents.preview_worker"])
        return arguments + ["--source", str(pdf), "--metadata", str(metadata)]

    @staticmethod
    def _read_metadata(metadata, limit=4096):
        if not metadata.is_file() or metadata.stat().st_size > limit:
            raise ValueError("metadata")
        result = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("metadata")
        if result.get("error") == "password":
            raise FormaError("PDF захищено паролем. Для перегляду відкрийте незахищену копію документа.")
        if "error" in result:
            raise FormaError("Не вдалося прочитати сторінку PDF. Перевірте, чи відкривається файл у зовнішній програмі.")
        return result

    def describe(self, path: Path, cancel_event: threading.Event) -> PreviewDocument:
        """Read every page's point size once, without rasterizing the document."""
        while not self._lock.acquire(timeout=0.05):
            self._check(cancel_event)
        try:
            self._check(cancel_event)
            source = Path(path).expanduser().resolve()
            if source.suffix.lower() not in {".pdf", ".docx"}:
                raise FormaError("Перегляд сторінок доступний для PDF і DOCX.")
            signature = file_signature(source)
            if signature in self._layouts:
                self._layouts.move_to_end(signature)
                return self._layouts[signature]
            folder, pdf = self._pdf(source, signature, cancel_event)
            metadata = folder / "description.json"
            metadata.unlink(missing_ok=True)
            try:
                self._run(self._worker_arguments(pdf, metadata) + ["--describe"], folder, self._render_timeout, cancel_event)
                result = self._read_metadata(metadata, 1024**2)
                count, sizes = result.get("page_count"), result.get("page_sizes")
                if type(count) is not int or not 1 <= count <= 10000 or not isinstance(sizes, list) or len(sizes) != count:
                    raise ValueError("metadata")
                for size in sizes:
                    if (not isinstance(size, list) or len(size) != 2
                            or any(type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 10_000_000 for value in size)):
                        raise ValueError("page size")
                self._check(cancel_event)
                if file_signature(source) != signature:
                    raise Cancelled("Документ змінився під час підготовки перегляду.")
                description = PreviewDocument(count, tuple(tuple(float(value) for value in size) for size in sizes))
                self._layouts[signature] = description
                while len(self._layouts) > 8:
                    self._layouts.popitem(last=False)
                return description
            except (OSError, ValueError, TypeError) as exc:
                raise FormaError("Локальний рушій повернув некоректний перегляд документа.") from exc
            finally:
                metadata.unlink(missing_ok=True)
                self._trim()
        finally:
            self._lock.release()

    def retain_pages(self, requests) -> None:
        """Pin one viewport and its nearby pages before a serial rendering batch.

        Entries are (path, zero-based page index, requested width). Only these
        pages remain pinned until the next call, with at most twelve images.
        """
        requests = list(requests)
        if len(requests) > MAX_RETAINED_PAGES:
            raise ValueError("Preview viewport exceeds twelve images")
        with self._lock:
            if self._closed.is_set():
                raise Cancelled("Перегляд документа скасовано.")
            retained, signatures = set(), {}
            for path, page_index, width in requests:
                if type(page_index) is not int or page_index < 0 or type(width) is not int:
                    raise ValueError("Invalid retained preview page or width")
                source = Path(path).expanduser().resolve()
                try:
                    if source not in signatures:
                        signatures[source] = file_signature(source)
                    retained.add((signatures[source], page_index, max(128, min(2400, width))))
                except FormaError:
                    continue  # Individual describe/render results explain missing sides.
            self._retained_pages = retained
            self._display_pages.clear()
            self._trim()

    def render(self, path: Path, page_index: int, width_px: int, cancel_event: threading.Event) -> PreviewPage:
        while not self._lock.acquire(timeout=0.05):
            self._check(cancel_event)
        try:
            self._check(cancel_event)
            source = Path(path).expanduser().resolve()
            if source.suffix.lower() not in {".pdf", ".docx"}:
                raise FormaError("Перегляд сторінок доступний для PDF і DOCX.")
            if type(page_index) is not int or page_index < 0 or type(width_px) is not int:
                raise ValueError("Invalid preview page or width")
            width_px = max(128, min(2400, width_px))
            signature = file_signature(source)
            key = signature, page_index, width_px
            if key in self._pages:
                self._pages.move_to_end(key)
                if signature in self._documents:
                    self._documents.move_to_end(signature)
                self._protect(key)
                return self._pages[key]
            folder, pdf = self._pdf(source, signature, cancel_event)
            pages = self._root / "pages"
            pages.mkdir(exist_ok=True, mode=0o700)
            image = pages / (hashlib.sha256(repr(key).encode()).hexdigest() + ".png")
            metadata = folder / "result.json"
            metadata.unlink(missing_ok=True)
            arguments = self._worker_arguments(pdf, metadata) + ["--output", str(image), "--page", str(page_index), "--width", str(width_px)]
            try:
                self._run(arguments, folder, self._render_timeout, cancel_event)
                result = self._read_metadata(metadata)
                count, width, height = (result.get(name) for name in ("page_count", "width", "height"))
                if any(type(value) is not int for value in (count, width, height)) or not 1 <= count <= 10000 or result.get("page_index") != page_index:
                    raise ValueError("metadata")
                available = page_index < count
                if available and (not 1 <= width <= 2400 or not 1 <= height <= 4000 or width * height > 4_000_000 or not image.is_file() or image.stat().st_size > 20 * 1024**2):
                    raise ValueError("image")
                if not available and (width != 0 or height != 0):
                    raise ValueError("unavailable")
                self._check(cancel_event)
                if file_signature(source) != signature:
                    raise Cancelled("Документ змінився під час підготовки перегляду.")
                page = PreviewPage(image if available else None, count, width, height, page_index)
                self._pages[key] = page
                self._protect(key)
                return page
            except (OSError, ValueError, TypeError) as exc:
                raise FormaError("Локальний рушій повернув некоректний перегляд документа.") from exc
            finally:
                metadata.unlink(missing_ok=True)
                if key not in self._pages:
                    image.unlink(missing_ok=True)
                self._trim()
        finally:
            self._lock.release()

    def _protect(self, key):
        if self._retained_pages is not None:
            return
        self._display_pages.pop(key, None)
        self._display_pages[key] = None
        while len(self._display_pages) > 2:
            self._display_pages.popitem(last=False)

    def _trim(self):
        # Pin the current viewport, independently of reusable converted PDFs.
        protected = self._display_pages if self._retained_pages is None else self._retained_pages
        while len(self._pages) > 24 or _disk_bytes(self._root) > MAX_CACHE_BYTES:
            key = next((key for key in self._pages if key not in protected), None)
            if key is None:
                break
            page = self._pages.pop(key)
            if page.image_path:
                page.image_path.unlink(missing_ok=True)
        while self._documents and (len(self._documents) > 8 or _disk_bytes(self._root) > MAX_CACHE_BYTES):
            _, (folder, _) = self._documents.popitem(last=False)
            shutil.rmtree(folder, ignore_errors=True)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            self._pages.clear()
            self._layouts.clear()
            self._display_pages.clear()
            self._retained_pages = None
            self._documents.clear()
            self._temporary.cleanup()
