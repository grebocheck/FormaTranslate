"""Read BabelDOC 0.7.x CLI progress without exposing its diagnostic text in the UI.

The CLI renders its progress events with Rich (or tqdm); it has no JSON output
switch. Only known stage names and numeric counters are accepted here. A silent
or newer renderer still receives elapsed-time updates, never invented progress.
"""

from __future__ import annotations

import codecs
import re
import time

STAGES = {
    "Parse PDF and Create Intermediate Representation": "Читаємо структуру PDF",
    "DetectScannedFile": "Перевіряємо текстовий шар",
    "Parse Page Layout": "Аналізуємо розміщення тексту",
    "Parse Table": "Розпізнаємо таблиці",
    "Parse Paragraphs": "Визначаємо абзаци",
    "Parse Formulas and Styles": "Аналізуємо формули та стилі",
    "Automatic Term Extraction": "Узгоджуємо термінологію",
    "Translate Paragraphs": "Перекладаємо абзаци",
    "Typesetting": "Верстаємо переклад",
    "Add Fonts": "Додаємо шрифти",
    "Generate drawing instructions": "Відновлюємо графіку",
    "Subset font": "Готуємо шрифти PDF",
    "Save PDF": "Зберігаємо PDF",
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
COUNTER = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)(?!\d)")


class PDFProgress:
    def __init__(self, callback, *, clock=time.monotonic):
        self.callback, self.clock = callback, clock
        self.started = self.updated = self.published = clock()
        self.percent = 8
        self.message = "Готуємо PDF-рушій; перший запуск може потребувати завантаження ресурсів"
        self.stage_position = (0, -1)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""
        self.last = None
        self.tick(force=True)

    def feed(self, data: bytes, *, final=False):
        self.buffer += self.decoder.decode(data, final=final)
        lines = re.split(r"[\r\n]", self.buffer)
        self.buffer = "" if final else lines.pop()
        # A malformed diagnostic must not grow the GUI's memory indefinitely.
        if len(self.buffer) > 65536:
            self.buffer = self.buffer[-65536:]
        before = self.percent, self.message
        for raw in lines:
            self._line(ANSI.sub("", raw).strip())
        if before != (self.percent, self.message):
            self.updated = self.clock()
            self.tick(force=True)

    def _line(self, line):
        if line.startswith("translate "):
            match = COUNTER.search(line)
            if match and float(match[2]) == 100:
                self._percent(float(match[1]))
            else:
                match = re.search(r":\s*(\d{1,3})%", line)
                if match:
                    self._percent(float(match[1]))
            return
        for rank, (stage, label) in enumerate(STAGES.items()):
            if not line.startswith(stage + " ("):
                continue
            suffix = line[len(stage) :].lstrip()
            if re.search(r":\s*\d{1,3}%", suffix):
                # tqdm embeds the stage counter in its description.
                counter = COUNTER.search(suffix)
                percent = re.search(r":\s*(\d{1,3})%", suffix)
                self._percent(float(percent[1]))
                position = (1, rank)
            else:
                # Rich first shows (part/parts), followed by the stage counter.
                part = re.match(r"\((\d+)/(\d+)\)\s*(.*)", suffix)
                if not part:
                    return
                counter = COUNTER.search(part[3])
                position = (int(part[1]), rank)
            # Rich redraws all completed stages in each frame. Do not briefly
            # report an earlier stage when a frame straddles two file reads.
            if position < self.stage_position:
                return
            self.stage_position = position
            self.message = label
            if counter:
                self.message += f" · {counter[1]}/{counter[2]}"
            return
        lowered = line.lower()
        if "downloading" in lowered and any(
            word in lowered for word in ("model", "font", "asset", "cmap")
        ):
            self.message = "Завантажуємо ресурси PDF-рушія"
        elif "retrying" in lowered and (
            "ratelimiterror" in lowered or "429" in lowered
        ):
            self.message = "API обмежив частоту запитів; рушій очікує та повторює запит"
        elif "retrying" in lowered or "network error occurred" in lowered:
            self.message = "Рушій повторює мережевий запит після помилки"

    def _percent(self, value):
        if 0 <= value <= 100:
            # Leave room for Forma's independent validation and final export.
            self.percent = max(self.percent, min(94, 8 + int(value * 0.86)))

    def tick(self, *, force=False):
        now = self.clock()
        if not force and now - self.published < 5:
            return
        elapsed = max(0, int(now - self.started))
        message = f"PDF · {self.message} · {elapsed} с"
        if now - self.updated >= 30:
            message += " · очікуємо оновлення від рушія"
        value = self.percent, message
        if value != self.last:
            self.callback(*value)
            self.last = value
        self.published = now
