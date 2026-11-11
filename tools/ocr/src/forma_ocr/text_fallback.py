"""Optional local second OCR for uncertain prose; native table OCR stays intact."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field

LANGUAGES = {"en": "eng", "uk": "ukr", "pl": "pol", "de": "deu"}
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
MIN_CONFIDENCE = 85
PROSE_LABELS = {"text", "list_item", "header", "footer", "footnote"}
NUMBERS = re.compile(r"(?<!\w)[+\-−]?\d+(?:[.,]\d+)*(?:[%‰])?(?!\w)")


@dataclass
class FallbackResult:
    attempted: int = 0
    corrected: int = 0
    unavailable: bool = False
    rejected: int = 0
    alternatives: list[dict] = field(default_factory=list)


def run_local(arguments: list[str], payload: bytes | None = None, *, timeout=TIMEOUT_SECONDS) -> bytes:
    """Keep subprocess output out of logs and bound decoded output memory."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            process.communicate(input=payload, timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
        if process.returncode:
            raise ValueError("Local secondary OCR failed")
        if output.tell() > MAX_OUTPUT_BYTES:
            raise ValueError("Local secondary OCR response exceeds its limit")
        output.seek(0)
        return output.read(MAX_OUTPUT_BYTES + 1)


def parse_tsv(payload: bytes) -> tuple[str, float]:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ValueError("Local secondary OCR response exceeds its limit")
    rows = csv.DictReader(io.StringIO(payload.decode("utf-8")), delimiter="\t")
    if not rows.fieldnames or not {"level", "conf", "text", "page_num", "block_num", "par_num", "line_num"}.issubset(rows.fieldnames):
        raise ValueError("Invalid local secondary OCR response")
    lines: list[list[str]] = []
    last_line = None
    weighted_confidence = weight = 0.0
    for row in rows:
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        text = row["text"].strip()
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise ValueError("Invalid local secondary OCR confidence")
        key = tuple(row[name] for name in ("page_num", "block_num", "par_num", "line_num"))
        if key != last_line:
            lines.append([])
            last_line = key
        lines[-1].append(text)
        weighted_confidence += confidence * len(text)
        weight += len(text)
    return "\n".join(" ".join(line) for line in lines), weighted_confidence / weight if weight else 0.0


def _center_inside(polygon, bbox) -> bool:
    points = list(polygon)
    cx = (min(point[0] for point in points) + max(point[0] for point in points)) / 2
    cy = (min(point[1] for point in points) + max(point[1] for point in points)) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def repair_uncertain_prose(page, language: str, *, executable: str | None = None) -> FallbackResult:
    """Replace only uncertain prose after a strong local second recognition.

    Replace the corresponding raw-reference lines too, so retention checks do
    not reinsert the rejected primary OCR. Alternatives are returned for the
    explicitly requested private diagnostics; none enter the public protocol.
    """
    result = FallbackResult()
    eligible = [block for block in page.blocks if block["label"] in PROSE_LABELS and block.get("uncertain") and len(block.get("text", "")) >= 35]
    if not eligible:
        return result
    executable = executable or shutil.which("tesseract")
    if not executable or language not in LANGUAGES:
        result.unavailable = True
        return result
    try:
        supported = run_local([executable, "--list-langs"], timeout=10).decode("utf-8").splitlines()
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        result.unavailable = True
        return result
    profile = LANGUAGES[language]
    if profile not in {value.strip() for value in supported}:
        result.unavailable = True
        return result
    corrected_boxes = []
    for block in eligible:
        result.attempted += 1
        bbox = [float(value) for value in block["bbox"]]
        crop = page.image.crop((
            max(0, int(bbox[0]) - 8), max(0, int(bbox[1]) - 8),
            min(page.image.width, int(bbox[2]) + 8), min(page.image.height, int(bbox[3]) + 8),
        )).convert("RGB")
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
        try:
            candidate, confidence = parse_tsv(run_local(
                [executable, "stdin", "stdout", "-l", profile, "--psm", "6", "tsv"], buffer.getvalue(),
            ))
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            result.rejected += 1
            continue
        accepted = confidence >= MIN_CONFIDENCE and len(candidate.strip()) >= len(block["text"].strip()) * 0.5
        reliable_numbers = Counter(value for text, score, polygon in zip(
            page.recognized_texts, page.scores, page.polygons, strict=True
        ) if score >= 0.9 and _center_inside(polygon, bbox) for value in NUMBERS.findall(text))
        if reliable_numbers - Counter(NUMBERS.findall(candidate)):
            accepted = False
        result.alternatives.append({"bbox": bbox, "primary_text": block["text"], "secondary_text": candidate, "secondary_confidence": confidence, "accepted": accepted})
        if not accepted:
            result.rejected += 1
            continue
        retained = [index for index, polygon in enumerate(page.polygons) if not _center_inside(polygon, bbox)]
        page.recognized_texts = [page.recognized_texts[index] for index in retained]
        page.scores = [page.scores[index] for index in retained]
        page.polygons = [page.polygons[index] for index in retained]
        left, top, right, bottom = bbox
        page.recognized_texts.append(candidate)
        page.scores.append(confidence / 100)
        page.polygons.append([[left, top], [right, top], [right, bottom], [left, bottom]])
        block["text"] = candidate
        block["uncertain"] = True
        block["secondary_ocr"] = "tesseract"
        result.corrected += 1
        corrected_boxes.append(bbox)
    # Cached diagnostics can already contain recovery lines from an earlier
    # export. Discard those inside replaced prose; live inference has none yet.
    if corrected_boxes:
        page.blocks[:] = [block for block in page.blocks if not (
            block["label"] == "recovered_text" and any(_center_inside(
                [[block["bbox"][0], block["bbox"][1]], [block["bbox"][2], block["bbox"][3]]], bbox
            ) for bbox in corrected_boxes)
        )]
    return result
