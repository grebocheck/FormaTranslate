from __future__ import annotations

import hashlib
import html
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from forma.models import FormaError
from forma.ooxml import A, S, W, parse_xml
from forma.quality import numbers_match
from forma.reports import document_html
from forma.storage import atomic_bytes, atomic_json

TEXT_TAGS = {f"{{{W}}}t", f"{{{A}}}t", f"{{{S}}}t"}
SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def office_signature(path: Path) -> dict:
    structure, text, formulas = {}, [], []
    with ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 512 * 1024 * 1024:
            raise FormaError("Файл завеликий для порівняння.")
        for name in archive.namelist():
            data = archive.read(name)
            if name.endswith(".xml"):
                tree = parse_xml(data)
                for node in tree.iter():
                    if node.tag in TEXT_TAGS:
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
        from forma.pdf import inspect_pdf

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
