from __future__ import annotations

import hashlib
import html
import posixpath
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from forma.core.models import FormaError
from forma.documents.ooxml import A, R, S, W, parse_xml
from forma.persistence.files import atomic_bytes, atomic_json
from forma.translation.quality import numbers_match
from forma.translation.reports import document_html

TEXT_TAGS = {f"{{{W}}}t", f"{{{A}}}t", f"{{{S}}}t"}
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_SHARED_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"


def _xlsx_logical_parts(parts: dict[str, bytes]) -> dict[str, bytes]:
    """Compare displayed cell payloads, rather than shared-string storage indexes.

    Rich-text runs/properties and all cell attributes remain in the structure.
    Unused shared strings have no displayed use and contribute neither text nor numbers.
    """
    shared_part = "xl/sharedStrings.xml"
    shared = []
    metadata = None
    if shared_part in parts:
        metadata = parse_xml(parts[shared_part]).getroot()
        if metadata.tag != f"{{{S}}}sst":
            raise FormaError("Некоректна таблиця спільних рядків XLSX.")
        shared = [node for node in metadata if node.tag == f"{{{S}}}si"]
    worksheets = {name for name in parts if name.startswith("xl/worksheets/") and name.endswith(".xml")}
    relations = None
    if "xl/_rels/workbook.xml.rels" in parts:
        relations = parse_xml(parts["xl/_rels/workbook.xml.rels"])
        for node in relations.getroot():
            if node.get("Type") == R + "/worksheet" and node.get("TargetMode", "Internal") == "Internal":
                target = node.get("Target", "")
                resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                if resolved in parts:
                    worksheets.add(resolved)
    normalized = {}
    for name, data in parts.items():
        if name == shared_part:
            continue
        if name in worksheets:
            tree = parse_xml(data)
            for cell in tree.iter(f"{{{S}}}c"):
                if cell.get("t") != "s":
                    continue
                value = cell.find(f"{{{S}}}v")
                try:
                    index = int(value.text if value is not None else "")
                    if not 0 <= index < len(shared):
                        raise ValueError
                    payload = deepcopy(shared[index])
                except (ValueError, TypeError):
                    raise FormaError("Некоректне посилання на спільний рядок XLSX.") from None
                payload.tag = f"{{{S}}}is"
                position = cell.index(value)
                cell.remove(value)
                cell.insert(position, payload)
                cell.set("t", "inlineStr")
            data = etree.tostring(tree)
        elif name == "[Content_Types].xml":
            tree = parse_xml(data)
            for node in list(tree.getroot()):
                if (
                    node.tag == f"{{{_CONTENT_TYPES}}}Override"
                    and dict(node.attrib) == {"PartName": "/" + shared_part, "ContentType": _SHARED_TYPE}
                    and len(node) == 0 and not (node.text or "").strip()
                ):
                    tree.getroot().remove(node)
            data = etree.tostring(tree)
        elif name == "xl/_rels/workbook.xml.rels":
            tree = relations
            for node in list(tree.getroot()):
                target = node.get("Target", "")
                resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                if (
                    node.get("Type") == R + "/sharedStrings" and resolved == shared_part
                    and set(node.attrib) <= {"Id", "Type", "Target", "TargetMode"}
                    and node.get("TargetMode", "Internal") == "Internal"
                    and len(node) == 0 and not (node.text or "").strip()
                ):
                    tree.getroot().remove(node)
            data = etree.tostring(tree, method="c14n")
        normalized[name] = data
    if metadata is not None:
        for node in shared:
            metadata.remove(node)
        metadata.attrib.pop("count", None)
        metadata.attrib.pop("uniqueCount", None)
        if metadata.attrib or len(metadata) or (metadata.text or "").strip():
            normalized[shared_part + "#metadata"] = etree.tostring(metadata, method="c14n")
    return normalized


def office_signature(path: Path) -> dict:
    structure, text, formulas = {}, [], []
    with ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 512 * 1024 * 1024:
            raise FormaError("Файл завеликий для порівняння.")
        parts = {name: archive.read(name) for name in archive.namelist()}
        if path.suffix.lower() == ".xlsx":
            parts = _xlsx_logical_parts(parts)
        for name, data in parts.items():
            if name.endswith(".xml"):
                tree = parse_xml(data)
                for node in tree.iter():
                    if node.tag in TEXT_TAGS:
                        cell = next(node.iterancestors(f"{{{S}}}c"), None)
                        if cell is not None and cell.find(f"{{{S}}}f") is not None:
                            # A formula's resolved string is a cached result, not
                            # translatable content: preserve its full value strictly.
                            continue
                        text.append(node.text or "")
                        node.text = "__TEXT__"
                        node.attrib.pop(SPACE, None)
                    elif node.tag == f"{{{S}}}f":
                        formulas.append(node.text or "")
                data = etree.tostring(tree, method="c14n")
            structure[name] = hashlib.sha256(data).hexdigest()
    return {"parts": structure, "text": "\n".join(text), "formulas": formulas}


def compare_files(source: Path, candidates: list[Path]) -> dict:
    if not candidates:
        raise FormaError("Додайте хоча б один переклад для порівняння.")
    result = {
        "source": str(source),
        "candidates": [],
        "note": "Структурні метрики не оцінюють зміст і природність перекладу. Мовну якість оцінюйте окремо.",
    }
    if source.suffix.lower() == ".pdf":
        from forma.documents.pdf import inspect_pdf

        original = inspect_pdf(source)
        for path in candidates:
            other = inspect_pdf(path)
            same_pages = len(original["pages"]) == len(other["pages"])
            same_dimensions = same_pages and all(
                abs(a["width"] - b["width"]) < 1 and abs(a["height"] - b["height"]) < 1
                for a, b in zip(original["pages"], other["pages"], strict=True)
            )
            result["candidates"].append(
                {
                    "file": str(path),
                    "name": path.name,
                    "format": "PDF",
                    "structure_ok": same_pages and same_dimensions,
                    "pages": len(other["pages"]),
                    "same_dimensions": same_dimensions,
                    "visual_review_required": True,
                }
            )
    elif source.suffix.lower() in (".docx", ".xlsx"):
        original = office_signature(source)
        for path in candidates:
            if path.suffix.lower() != source.suffix.lower():
                raise FormaError(
                    "Для порівняння Office-файлів потрібен однаковий формат оригіналу й перекладу."
                )
            other = office_signature(path)
            missing = sorted(original["parts"].keys() - other["parts"].keys())
            added = sorted(other["parts"].keys() - original["parts"].keys())
            changed = sorted(
                k
                for k in original["parts"].keys() & other["parts"].keys()
                if original["parts"][k] != other["parts"][k]
            )
            result["candidates"].append(
                {
                    "file": str(path),
                    "name": path.name,
                    "format": source.suffix[1:].upper(),
                    "structure_ok": not (missing or added or changed),
                    "removed_parts": missing,
                    "added_parts": added,
                    "changed_nontext_parts": changed,
                    "formulas_preserved": original["formulas"] == other["formulas"],
                    "numbers_preserved": numbers_match(original["text"], other["text"]),
                    "text_changed": original["text"] != other["text"],
                    "source_characters": len(original["text"]),
                    "translated_characters": len(other["text"]),
                }
            )
    else:
        raise FormaError("Підтримуються DOCX, XLSX і PDF.")
    return result


def write_comparison(result: dict, path: Path) -> None:
    atomic_json(path.with_suffix(".json"), result)
    rows = []
    for candidate in result["candidates"]:
        rows.append(
            "<tr><td>"
            + html.escape(candidate["name"])
            + "</td><td>"
            + ("Без структурних змін" if candidate["structure_ok"] else "Є структурні відмінності")
            + "</td><td><pre>"
            + html.escape(
                "\n".join(
                    f"{k}: {v}"
                    for k, v in candidate.items()
                    if k not in ("file", "name", "structure_ok")
                )
            )
            + "</pre></td></tr>"
        )
    body = (
        f"<h1>forma. / Порівняння перекладів</h1><p>{html.escape(result['source'])}</p>"
        f"<p>{html.escape(result['note'])}</p><table><tr><th>Результат</th><th>Структура</th><th>Деталі</th></tr>{''.join(rows)}</table>"
        "<h2>Ручна оцінка, 1–5</h2><p>Для кожного результату окремо оцініть точність змісту, термінологію, природність мови та верстку. "
        "Використовуйте однакову вибірку фрагментів і приховайте назви систем від оцінювача.</p>"
    )
    atomic_bytes(path.with_suffix(".html"), document_html("Forma — порівняння", body))
