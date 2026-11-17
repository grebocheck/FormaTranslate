"""Strict native SubRip blocks; clocks and counters are never regenerated."""

from __future__ import annotations

import re

from forma.core.models import FormaError, Issue
from forma.documents.subtitles import MAX_CUES, Cue, SubtitleDocument, source_lines

_TIME = r"([0-9]{2,6}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})"
_TIMING = re.compile(r"[ \t]*" + _TIME + r"[ \t]*-->[ \t]*" + _TIME + r"((?:[ \t]+[XY][12]:[0-9]{1,6}){4})?[ \t]*")
_ERROR = "Некоректна структура або таймінг SRT. Перевірте номери реплік, часові коди та порожні рядки між репліками."


class SrtDocument(SubtitleDocument):
    extraction_profile = 11

    def _parse_cues(self, text):
        lines, cues, index, previous_number, previous_start = source_lines(text, self.check_cancel), [], 0, -1, -1
        while index < len(lines):
            self.check_cancel()
            if not lines[index][2].strip(" \t"):
                index += 1
                continue
            start, _, number, _ = lines[index]
            if not re.fullmatch(r"[ \t]*[0-9]{1,9}[ \t]*", number):
                raise FormaError(_ERROR)
            identifier = number.strip(" \t")
            counter = int(identifier)
            if index + 2 >= len(lines):
                raise FormaError(_ERROR)
            if counter != (1 if previous_number == -1 else previous_number + 1) and not any(issue.code == "srt_counter_preserved" for issue in self.issues):
                self.issues.append(Issue("srt_counter_preserved", "Нумерацію реплік SRT збережено без змін. Нульові, повторені або непослідовні номери не виправлялися.", severity="info"))
            match = _TIMING.fullmatch(lines[index + 1][2])
            if match is None:
                raise FormaError(_ERROR)
            values = [int(value) for value in match.groups()[:8]]
            begin = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
            end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
            if end <= begin or begin < previous_start:
                raise FormaError(_ERROR)
            if match.group(9):
                keys = re.findall(r"([XY][12]):", match.group(9))
                if set(keys) != {"X1", "X2", "Y1", "Y2"}:
                    raise FormaError(_ERROR)
            payload_line = index + 2
            index = payload_line
            while index < len(lines) and lines[index][2].strip(" \t"):
                if "-->" in lines[index][2]:
                    raise FormaError(_ERROR)
                index += 1
            if index == payload_line:
                raise FormaError(_ERROR)
            last = lines[index - 1]
            cues.append(Cue(len(cues) + 1, start, last[1], begin, end,
                            lines[payload_line][0], last[1] - len(last[3]), identifier))
            if len(cues) > MAX_CUES:
                raise FormaError("Файл містить забагато реплік субтитрів. Розділіть його на менші файли.")
            previous_number, previous_start = counter, begin
        if not cues and text.strip():
            raise FormaError(_ERROR)
        return cues
