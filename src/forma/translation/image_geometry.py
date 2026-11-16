"""Optional local OCR anchors for photo overlays; recognition never replaces source text.

Vision provides the translation and full-image context. A local Tesseract process
can supply word geometry, which is used only after matching the recognized words.
Unavailable language data, failures and timeouts retain the vision-only route.
"""

from __future__ import annotations

import csv
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPainter

from forma.documents.bundled_runtime import runtime_root, start_worker

_LANGUAGES = {"en": "eng", "uk": "ukr", "pl": "pol", "de": "deu", "fr": "fra", "es": "spa"}
_MAX_TSV_BYTES = 2 * 1024 * 1024
_MAX_WORDS = 4000
_TIMEOUT = 12.0


@dataclass(frozen=True)
class TextWord:
    text: str
    rectangle: QRect


@dataclass(frozen=True)
class TextLine:
    words: tuple[TextWord, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def rectangle(self) -> QRect:
        result = QRect()
        for word in self.words:
            result = result.united(word.rectangle)
        return result


@dataclass(frozen=True)
class TextAnchor:
    rectangle: QRect
    font_size: int


def _normalized(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum())


def _configuration() -> tuple[str, dict[str, str]]:
    # OCR has no reason to inherit provider secrets. Bundled native libraries
    # use start_worker(), which restores PyInstaller's Windows DLL directory.
    environment = {key: value for key, value in os.environ.items() if key not in {
        "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
    }}
    if os.name != "nt" and getattr(sys, "frozen", False):
        # A system Tesseract must load its system libraries, not desktop-bundle
        # copies inherited from PyInstaller's launcher environment.
        original = environment.pop("LD_LIBRARY_PATH_ORIG", "")
        if original:
            environment["LD_LIBRARY_PATH"] = original
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    root = runtime_root()
    if root is not None:
        executable = root / "tesseract/tesseract.exe"
        if executable.is_file():
            environment["TESSDATA_PREFIX"] = str(root / "tesseract/tessdata")
            return str(executable), environment
    return shutil.which("tesseract") or "", environment


def _run(arguments: list[str], environment: dict[str, str], check: Callable[[], None],
         *, data: bytes | None = None, timeout: float = _TIMEOUT) -> bytes | None:
    # Redirect to a temporary file so a worker cannot fill a PIPE and deadlock,
    # or accumulate arbitrarily large stdout in Python's communicate buffer.
    with tempfile.TemporaryFile() as output:
        try:
            process = start_worker(
                arguments, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                stdout=output, stderr=subprocess.DEVNULL, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            return None
        deadline = time.monotonic() + timeout
        try:
            pending = data
            while True:
                check()
                if time.monotonic() > deadline or os.fstat(output.fileno()).st_size > _MAX_TSV_BYTES:
                    return None
                try:
                    process.communicate(pending, timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            if process.returncode or os.fstat(output.fileno()).st_size > _MAX_TSV_BYTES:
                return None
            output.seek(0)
            return output.read(_MAX_TSV_BYTES + 1)
        except OSError:
            return None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _parse_tsv(data: bytes, width: int, height: int, scale: float) -> list[TextLine]:
    if len(data) > _MAX_TSV_BYTES:
        return []
    groups: dict[tuple[int, ...], list[TextWord]] = {}
    try:
        rows = csv.DictReader(io.StringIO(data.decode("utf-8")), delimiter="\t")
        count = 0
        characters = 0
        for row in rows:
            text = row.get("text")
            if row.get("level") != "5" or not isinstance(text, str) or not (text := text.strip()):
                continue
            if len(text) > 4000 or any(ord(char) < 32 for char in text):
                return []
            confidence = float(row["conf"])
            if not math.isfinite(confidence) or confidence < 35:
                continue
            left, top, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
            if min(left, top) < 0 or min(w, h) <= 0 or left + w > width or top + h > height:
                return []
            key = tuple(int(row[name]) for name in ("page_num", "block_num", "par_num", "line_num"))
            x, y = math.floor(left / scale), math.floor(top / scale)
            right, bottom = math.ceil((left + w) / scale), math.ceil((top + h) / scale)
            groups.setdefault(key, []).append(TextWord(text, QRect(x, y, right - x, bottom - y)))
            count += 1
            characters += len(text)
            if count > _MAX_WORDS or characters > 40_000:
                return []
    except (ValueError, KeyError, TypeError, UnicodeError, csv.Error):
        return []
    return [TextLine(tuple(words)) for words in groups.values()]


def detect_text(image: QImage, source_language: str, check: Callable[[], None]) -> list[TextLine]:
    executable, environment = _configuration()
    if not executable:
        return []
    check()
    available = _run([executable, "--list-langs"], environment, check, timeout=2)
    if available is None:
        return []
    installed = set(available.decode("utf-8", errors="replace").splitlines())
    requested = list(_LANGUAGES.values()) if source_language == "auto" else [_LANGUAGES.get(source_language, "")]
    languages = [language for language in requested if language in installed]
    if not languages:
        return []
    scale = min(1.0, 3200 / max(image.width(), image.height()),
                math.sqrt(4_000_000 / (image.width() * image.height())))
    width, height = max(1, int(image.width() * scale)), max(1, int(image.height() * scale))
    # Strip metadata and composite alpha exactly as for the vision upload.
    prepared = QImage(width, height, QImage.Format_RGB888)
    prepared.fill(Qt.white)
    scaled = image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(1)
    painter = QPainter(prepared)
    painter.drawImage(0, 0, scaled)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not prepared.save(buffer, "PNG"):
        return []
    data = bytes(buffer.data())
    buffer.close()
    result = _run([executable, "stdin", "stdout", "-l", "+".join(languages),
                   "--psm", "11", "tsv"], environment, check, data=data)
    return _parse_tsv(result, width, height, scale) if result is not None else []


def anchor_regions(regions: list[tuple[str, QRect]], lines: list[TextLine],
                   image_size: tuple[int, int], *,
                   check: Callable[[], None] = lambda: None) -> dict[int, TextAnchor]:
    """Match complete source spans; a nearby unrelated OCR line is never an anchor."""
    anchors = {}
    occupied: list[QRect] = []
    diagonal = math.hypot(*image_size)
    reference_font = QFont("Sans Serif")
    reference_font.setPixelSize(100)
    metrics = QFontMetrics(reference_font)
    for index, (source, estimated) in enumerate(regions):
        check()
        target = _normalized(source)
        if len(target) < 3:
            continue
        candidates: dict[tuple[int, ...], tuple[float, TextAnchor]] = {}
        for start, line in enumerate(lines):
            if start % 32 == 0:
                check()
            words: list[TextWord] = []
            previous = line.rectangle
            for end in range(start, min(start + 6, len(lines))):
                following = lines[end]
                rect = following.rectangle
                if end != start and (rect.top() < previous.top() or
                        rect.top() - previous.bottom() > 2 * max(previous.height(), rect.height()) or
                        abs(rect.left() - line.rectangle.left()) > max(40, line.rectangle.width() * 0.3)):
                    break
                words.extend(following.words)
                previous = rect
                tokens = [_normalized(word.text) for word in words]
                joined = "".join(tokens)
                spans = []
                # Exact contiguous spans allow a vision label to match part of
                # an OCR line (for example a sentence and its separate link).
                position = 0
                for first, token in enumerate(tokens):
                    if joined.startswith(target, position):
                        remaining = len(target)
                        for last in range(first, len(tokens)):
                            remaining -= len(tokens[last])
                            if remaining == 0:
                                spans.append((1.0, words[first:last + 1]))
                            if remaining <= 0:
                                break
                    position += len(token)
                if not spans and len(target) >= 10 and abs(len(target) - len(joined)) <= len(target) * 0.15:
                    ratio = SequenceMatcher(None, target, joined, autojunk=False).ratio()
                    if ratio >= 0.92:
                        spans.append((ratio, words))
                for similarity, matched in spans:
                    matched_rect = QRect()
                    for word in matched:
                        matched_rect = matched_rect.united(word.rectangle)
                    if any(matched_rect.intersects(used) for used in occupied):
                        continue
                    distance = math.hypot(matched_rect.center().x() - estimated.center().x(),
                                          matched_rect.center().y() - estimated.center().y()) / max(1, diagonal)
                    if distance > (0.12 if len(target) < 6 else 0.4):
                        continue
                    # Glyph height alone varies strongly with capitals/descenders.
                    # Match actual recognized words to a reference font in both
                    # dimensions, then use the median to resist one odd glyph.
                    sizes = []
                    for word in matched:
                        reference = metrics.tightBoundingRect(word.text)
                        if reference.width() > 0 and reference.height() > 0:
                            sizes.extend((100 * word.rectangle.width() / reference.width(),
                                          100 * word.rectangle.height() / reference.height()))
                    font_size = max(8, min(256, round(median(sizes)))) if sizes else 12
                    key = (matched_rect.x(), matched_rect.y(), matched_rect.width(), matched_rect.height())
                    candidate = (similarity - distance * 0.2, TextAnchor(matched_rect, font_size))
                    if key not in candidates or candidates[key][0] < candidate[0]:
                        candidates[key] = candidate
        ranked = sorted(candidates.values(), key=lambda item: item[0], reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] > 0.002):
            anchors[index] = ranked[0][1]
            occupied.append(ranked[0][1].rectangle)
    return anchors
