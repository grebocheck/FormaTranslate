"""Offline review edits: preserve run identities and patch a separate Office copy."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from forma.core.models import FormaError, Segment, TranslationResult, plain_text
from forma.documents.ooxml import A, OfficeDocument, S, Target, W
from forma.documents.review import SEGMENT_CHECK_CODES
from forma.persistence.files import atomic_bytes, atomic_json
from forma.translation.engine import translation_issues
from forma.translation.quality import immutable_spans, parse_translation
from forma.translation.reports import write_translation_report


def _edit_zone(bindings: list[tuple[str, str]], edited: str) -> dict[str, str]:
    """Assign a diff to its original runs; replacements share their former styles."""
    original = "".join(text for _, text in bindings)
    owners = [tag for tag, text in bindings for _ in text]
    result = {tag: [] for tag, _ in bindings}
    if edited and not bindings:
        raise FormaError("Між цими службовими позначками немає текстового фрагмента для вставки.")
    for operation, start, end, new_start, new_end in SequenceMatcher(
        None, original, edited, autojunk=True
    ).get_opcodes():
        text = edited[new_start:new_end]
        if operation == "equal":
            for offset, char in enumerate(text, start):
                result[owners[offset]].append(char)
        elif operation == "replace":
            for offset, char in enumerate(text):
                result[owners[start + offset * (end - start) // len(text)]].append(char)
        elif operation == "insert":
            owner = owners[max(0, start - 1)] if owners else bindings[0][0]
            result[owner].append(text)
    return {tag: "".join(chars) for tag, chars in result.items()}


def edit_translation_markup(original_markup: str, edited_plain: str) -> str:
    """Map plain edits to existing runs without exposing XML or moving control anchors.

    Every visible newline represents an existing control anchor or text newline.
    Its position between text regions may change, but its identity/order cannot.
    """
    parse_translation(original_markup, original_markup)
    if not isinstance(edited_plain, str) or len(edited_plain) > max(
        20000, len(original_markup) * 12
    ):
        raise FormaError("Текст редактури завеликий або має некоректний формат.")
    if edited_plain == plain_text(original_markup):
        return original_markup
    edited_plain = edited_plain.replace("\r\n", "\n").replace("\r", "\n")
    root = etree.fromstring(
        f"<root>{original_markup}</root>".encode(),
        etree.XMLParser(resolve_entities=False, no_network=True),
    )
    zones: list[list[tuple[str, str]]] = [[]]
    boundaries: list[str | None] = []
    values = {node.tag: "" for node in root if node.tag.startswith("r")}
    for node in root:
        if node.tag.startswith("k"):
            boundaries.append(None)
            zones.append([])
            continue
        pieces = (node.text or "").split("\n")
        for index, piece in enumerate(pieces):
            if index:
                boundaries.append(node.tag)
                zones.append([])
            zones[-1].append((node.tag, piece))
    lines = edited_plain.split("\n")
    if len(lines) != len(zones):
        raise FormaError(
            "Збережіть кількість переносів рядка: вони позначають наявні табуляції, "
            "переноси або інші службові елементи документа."
        )
    for index, (bindings, edited) in enumerate(zip(zones, lines, strict=True)):
        for tag, text in _edit_zone(bindings, edited).items():
            values[tag] += text
        if index < len(boundaries) and boundaries[index] is not None:
            values[boundaries[index]] += "\n"
    translated = "".join(
        f"<{node.tag}>{escape(values[node.tag])}</{node.tag}>"
        if node.tag.startswith("r")
        else f"<{node.tag}/>"
        for node in root
    )
    parse_translation(original_markup, translated)
    if plain_text(translated) != edited_plain:
        raise FormaError("Редактуру не вдалося безпечно розподілити між стилями фрагмента.")
    return translated


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _shape(document: OfficeDocument, part: str) -> list[tuple]:
    """Text/control topology, ignoring run styles and current translated text."""
    content_tags = {
        **dict.fromkeys(
            (
                f"{{{W}}}{tag}"
                for tag in (
                    "p",
                    "r",
                    "t",
                    "tab",
                    "br",
                    "cr",
                    "sym",
                    "fldChar",
                    "instrText",
                    "footnoteReference",
                    "endnoteReference",
                )
            ),
            "word",
        ),
        **dict.fromkeys((f"{{{A}}}{tag}" for tag in ("p", "r", "t", "br")), "drawing"),
        **dict.fromkeys(
            (
                f"{{{S}}}{tag}"
                for tag in (
                    "row",
                    "c",
                    "si",
                    "is",
                    "r",
                    "t",
                    "comment",
                )
            ),
            "sheet",
        ),
    }
    tree = document._tree(part)
    result = []
    for node in tree.iter():
        if node.tag not in content_tags:
            continue
        attributes = ()
        reference = ""
        if node.tag == f"{{{S}}}c":
            attributes = tuple((key, node.get(key, "")) for key in ("r", "t"))
            if node.get("t") == "s":
                reference = node.findtext(f"{{{S}}}v", "")
        elif node.tag == f"{{{S}}}row":
            attributes = (("r", node.get("r", "")),)
        elif node.tag == f"{{{S}}}comment":
            attributes = (("ref", node.get("ref", "")),)
        elif node.tag in {
            f"{{{W}}}{tag}"
            for tag in (
                "tab",
                "br",
                "cr",
                "sym",
                "fldChar",
                "footnoteReference",
                "endnoteReference",
            )
        }:
            attributes = tuple(sorted(node.attrib.items()))
        result.append((tree.getpath(node), node.tag, attributes, reference))
    return result


def _locate_target(original: OfficeDocument, current: OfficeDocument, target: Target) -> Target:
    tree, current_tree = original._tree(target.part), current._tree(target.part)
    namespaces = {key: value for key, value in tree.getroot().nsmap.items() if key}

    def locate(node):
        matches = current_tree.xpath(tree.getpath(node), namespaces=namespaces)
        if (
            len(matches) != 1
            or not isinstance(matches[0], etree._Element)
            or matches[0].tag != node.tag
        ):
            raise FormaError(
                "Структура перекладеного документа змінилася; зіставлення редактури небезпечне."
            )
        return matches[0]

    return Target(
        target.part, [locate(n) for n in target.nodes], [locate(n) for n in target.markers]
    )


def _target_markup(document: OfficeDocument, target: Target) -> str:
    nodes = {node: index for index, node in enumerate(target.nodes)}
    markers = {node: index for index, node in enumerate(target.markers)}
    if target.part not in document._orders:
        document._orders[target.part] = {
            node: index for index, node in enumerate(document._tree(target.part).iter())
        }
    events = sorted([*nodes, *markers], key=document._orders[target.part].__getitem__)
    return "".join(
        f"<r{nodes[node]}>{escape(node.text or '')}</r{nodes[node]}>"
        if node in nodes
        else f"<k{markers[node]}/>"
        for node in events
    )


def _unique_destination(output: Path, folder: Path) -> Path:
    for number in range(1, 10000):
        suffix = "" if number == 1 else f"-{number}"
        candidate = folder / f"{output.stem}.reviewed{suffix}{output.suffix}"
        paths = [
            candidate,
            candidate.with_name(candidate.name + ".forma.json"),
            candidate.with_name(candidate.name + ".forma.html"),
        ]
        if not any(path.exists() for path in paths):
            return candidate
    raise FormaError("Не вдалося вибрати вільне ім’я для відредагованої копії.")


def export_reviewed_copy(
    source: Path,
    output: Path,
    report: Path,
    edits: dict[str, str],
    destination: Path | None = None,
    *,
    expected_report_fingerprint: str | None = None,
) -> TranslationResult:
    """Validate a report-bound edit and publish a fresh DOCX/XLSX plus its reports.

    Source IDs are never interpreted as node indexes. The full reported source is
    matched by location + exact markup; output nodes follow verified XML paths.
    Unedited external text/style changes are preserved from the current output.
    """
    started = time.monotonic()
    source, output, report = Path(source), Path(output), Path(report)
    if (
        source.suffix.lower() not in {".docx", ".xlsx"}
        or output.suffix.lower() != source.suffix.lower()
    ):
        raise FormaError("Ручний експорт підтримує DOCX і XLSX одного формату.")
    if source.resolve() == output.resolve():
        raise FormaError("Оригінал і переклад мають бути різними файлами.")
    if (
        not isinstance(edits, dict)
        or not edits
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in edits.items())
    ):
        raise FormaError("Немає коректних змін для збереження у копії.")
    try:
        return _export(
            source,
            output,
            report,
            edits,
            Path(destination) if destination else output.parent,
            expected_report_fingerprint,
            started,
        )
    except (OSError, etree.LxmlError) as exc:
        raise FormaError(
            "Не вдалося створити відредаговану копію. Перевірте доступ до файлів."
        ) from exc


def _export(source, output, report, edits, folder, expected_report_fingerprint, started):
    from forma.documents.review import parse_report_data

    with report.open("rb") as stream:
        report_bytes = stream.read(64 * 1024 * 1024 + 1)
    if len(report_bytes) > 64 * 1024 * 1024:
        raise FormaError("Звіт завеликий для безпечного читання.")
    report_fingerprint = hashlib.sha256(report_bytes).hexdigest()
    if (
        expected_report_fingerprint is not None
        and expected_report_fingerprint != report_fingerprint
    ):
        raise FormaError("Звіт змінився після відкриття. Відкрийте його знову перед експортом.")
    try:
        metadata = json.loads(report_bytes)
    except (ValueError, UnicodeError, RecursionError):
        raise FormaError("Не вдалося прочитати звіт Forma.") from None
    previous, options = parse_report_data(metadata, report)
    saved_options = metadata["options"]
    for key in ("domain", "review", "auto_glossary", "glossary", "instructions", "memory_project_id"):
        value = saved_options.get(key)
        if type(value) is type(getattr(options, key)) and (
            key != "glossary"
            or all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
        ):
            setattr(options, key, value)
    options.validate()
    if previous.engine not in {"forma-ooxml", "forma-corpus"}:
        raise FormaError("Цей звіт не підтримує ручний експорт Office.")
    if not set(edits) <= set(previous.translations):
        raise FormaError("Редактура містить фрагменти, яких немає в цьому звіті.")
    source_hash, output_hash = _fingerprint(source), _fingerprint(output)
    if metadata.get("source_sha256", source_hash) != source_hash:
        raise FormaError("Оригінал змінився після створення звіту.")
    original, current = OfficeDocument(source), OfficeDocument(output)
    by_source = defaultdict(list)
    for segment in original.segments:
        by_source[(segment.location, segment.source)].append(segment)
    reported = [(segment.location, segment.source) for segment in previous.segments]
    # Before 0.5.0, text separated by tabs/breaks could be concatenated into a
    # false identifier and omitted. Accept only that precisely reproducible gap
    # in old reports; all reported segments still require exact unique matches.
    legacy_gaps = set(by_source) - set(reported)

    def legacy_omission(markup):
        if not re.search(r"<k\d+\s*/>", markup):
            return False
        collapsed = plain_text(re.sub(r"<k\d+\s*/>", "", markup)).strip()
        return any(
            start == 0 and end == len(collapsed) for start, end, _ in immutable_spans(collapsed)
        )

    if (
        len(set(reported)) != len(reported)
        or not set(reported) <= set(by_source)
        or any(not legacy_omission(markup) for _, markup in legacy_gaps)
        or any(len(matches) != 1 for matches in by_source.values())
    ):
        raise FormaError(
            "Оригінал не відповідає повному набору фрагментів звіту; редактуру не застосовано."
        )
    parts = {original.targets[segment.id].part for segment in original.segments}
    if source.suffix.lower() == ".xlsx":
        original_sheets = original._sheet_names(original._tree("xl/workbook.xml"))
        current_sheets = current._sheet_names(current._tree("xl/workbook.xml"))
        if original_sheets != current_sheets:
            raise FormaError("Змінилися аркуші книги; зіставлення редактури небезпечне.")
        # Shared-string nodes live outside worksheets. Verify their cell references
        # even when a worksheet has no inline-text targets of its own.
        parts.update(original_sheets)
        for part in original_sheets:
            if original._related_parts(part, "comments") != current._related_parts(
                part, "comments"
            ):
                raise FormaError("Змінилися посилання на примітки книги; редактуру не застосовано.")
    for part in parts:
        if part not in current.parts or _shape(original, part) != _shape(current, part):
            raise FormaError(
                "Структура перекладеного документа змінилася; збережіть редактуру в редакторі Office."
            )
    segments, targets, translations = [], {}, {}
    for old in previous.segments:
        segment = by_source[(old.location, old.source)][0]
        target = _locate_target(original, current, original.targets[segment.id])
        present = _target_markup(current, target)
        if old.id in edits and parse_translation(old.source, present) != parse_translation(
            old.source, previous.translations[old.id]
        ):
            raise FormaError(
                "Редагований фрагмент у файлі вже змінився після звіту. Зміни не перезаписано."
            )
        segments.append(Segment(old.id, old.location, old.source, segment.context))
        targets[old.id] = target
        translations[old.id] = edits.get(old.id, present)
    segment_ids = set(previous.translations)
    issues = [
        issue
        for issue in previous.issues
        if issue.code not in SEGMENT_CHECK_CODES or issue.segment_id not in segment_ids
    ]
    for segment in segments:
        if segment.plain.strip() and not plain_text(translations[segment.id]).strip():
            raise FormaError("Переклад фрагмента не може бути порожнім.")
        issues.extend(translation_issues(segment, translations[segment.id], options))
    if any(issue.severity == "error" for issue in issues):
        raise FormaError(
            "Експорт зупинено: перевірте числа, коди й форматування відредагованих фрагментів."
        )
    current.segments, current.targets = segments, targets
    folder.mkdir(parents=True, exist_ok=True)
    final = _unique_destination(output, folder)
    created = []
    with tempfile.TemporaryDirectory(prefix=".forma-edit-", dir=folder) as temporary:
        stage = Path(temporary) / final.name
        current.write(translations, stage)
        result = TranslationResult(
            source.resolve() if previous.preparation else source,
            stage,
            segments,
            translations,
            issues,
            engine=previous.engine,
            elapsed_seconds=time.monotonic() - started,
            usage_note="Ручна редактура без нових запитів до API.",
            memory_project_id=options.memory_project_id,
            source_sha256=source_hash,
            output_sha256=_fingerprint(stage),
            preparation=(
                {**previous.preparation, "prepared_source": str(source.resolve())}
                if previous.preparation else {}
            ),
        )
        # Unknown legacy glossary provenance stays unknown. Manual edits never
        # become preferred memory entries without the separate explicit action.
        glossary = metadata.get("effective_glossary")
        if isinstance(glossary, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in glossary.items()
        ):
            result.effective_glossary = dict(glossary)
        revision = metadata.get("memory_revision")
        if type(revision) is int and revision >= 0:
            result.memory_revision = revision
        references = metadata.get("memory_references", {})
        if options.memory_project_id and isinstance(references, dict):
            for sid, entry in references.items():
                if (
                    sid not in edits and sid in translations
                    and translations[sid] == previous.translations[sid]
                    and isinstance(entry, dict)
                    and type(entry.get("entry_id")) is int and entry["entry_id"] > 0
                    and type(entry.get("revision")) is int and entry["revision"] >= 0
                    and entry.get("status") in {"machine_final", "human_approved"}
                ):
                    result.memory_references[sid] = {
                        "entry_id": entry["entry_id"], "revision": entry["revision"],
                        "status": entry["status"], "kind": "manual_copy",
                    }
        model = metadata.get("model", "Невідома модель")
        write_translation_report(
            result, options, model if isinstance(model, str) else "Невідома модель"
        )
        stage_report = result.report
        payload = json.loads(stage_report.read_text(encoding="utf-8"))
        payload.update(
            output=str(final),
            parent_report=str(report),
            manual_edits=list(edits),
            source_sha256=source_hash,
            output_sha256=_fingerprint(stage),
        )
        atomic_json(stage_report, payload)
        if (
            _fingerprint(source) != source_hash
            or _fingerprint(output) != output_hash
            or _fingerprint(report) != report_fingerprint
        ):
            raise FormaError("Вхідні файли змінилися під час експорту. Повторіть збереження копії.")
        stage_paths = [stage, stage_report, stage_report.with_suffix(".html")]
        try:
            for path in stage_paths:
                target = folder / path.name
                atomic_bytes(target, path.read_bytes(), overwrite=False)
                created.append(target)
        except OSError:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        result.output = final
        result.report = final.with_name(final.name + ".forma.json")
        return result
