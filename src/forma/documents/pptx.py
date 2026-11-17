"""Profile-four presentation extraction; unsupported surfaces stay byte-for-byte intact."""

from __future__ import annotations

import json
import math
import posixpath
from urllib.parse import unquote

from forma.core.models import FormaError, Issue, plain_text
from forma.documents.ooxml import A, R

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
PACKAGE_R = "http://schemas.openxmlformats.org/package/2006/relationships"
_SHAPE_TAGS = {f"{{{P}}}sp", f"{{{P}}}graphicFrame"}


def _excerpt(value, limit=700):
    if len(value) <= limit:
        return value
    side = (limit - 3) // 2
    return value[:side] + " … " + value[-side:]


def slide_parts(document):
    """Use the declared slide order, never ZIP storage order or slide filenames."""
    main, relationships = "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"
    if main not in document.parts or relationships not in document.parts:
        raise FormaError("У PPTX відсутня структура презентації або зв’язки слайдів.")
    tree = document._tree(main)
    if tree.getroot().tag != f"{{{P}}}presentation":
        raise FormaError("Strict OOXML поки не підтримується. Збережіть копію як звичайний PPTX.")
    relations = document._tree(relationships).getroot()
    if relations.tag != f"{{{PACKAGE_R}}}Relationships":
        raise FormaError("У PPTX некоректні зв’язки слайдів.")
    targets = {}
    for node in relations:
        key = node.get("Id", "")
        if not key or key in targets:
            raise FormaError("У PPTX некоректні зв’язки слайдів.")
        targets[key] = node
    result = []
    for node in tree.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId"):
        relation = targets.get(node.get(f"{{{R}}}id", ""))
        if relation is None or relation.get("Type") != R + "/slide" or relation.get("TargetMode", "Internal") != "Internal":
            raise FormaError("У PPTX некоректні зв’язки слайдів.")
        target = unquote(relation.get("Target", ""))
        if not target or any(char in target for char in "\\:#?\x00") or target.startswith("//"):
            raise FormaError("У PPTX некоректні зв’язки слайдів.")
        part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
        if not part.startswith("ppt/") or part not in document.parts or part in result:
            raise FormaError("У PPTX некоректні зв’язки слайдів.")
        if document._tree(part).getroot().tag != f"{{{P}}}sld":
            raise FormaError("У PPTX некоректна структура слайда.")
        result.append(part)
    return tuple(result)


def _shapes(tree):
    for node in tree:
        if node.tag == f"{{{P}}}grpSp":
            yield from _shapes(node)
        elif node.tag in _SHAPE_TAGS:
            yield node


def _shape_role(shape):
    placeholder = shape.find(f".//{{{P}}}ph")
    return placeholder.get("type", "obj") if placeholder is not None else "shape"


def _hidden(shape):
    for node in (shape, *shape.iterancestors(f"{{{P}}}grpSp")):
        identity = node.find(f".//{{{P}}}cNvPr")
        if identity is not None and identity.get("hidden", "0") in {"1", "true"}:
            return True
    return False


def _paragraphs(shape):
    if shape.tag == f"{{{P}}}graphicFrame":
        return [paragraph
                for table in shape.findall(f"{{{A}}}graphic/{{{A}}}graphicData/{{{A}}}tbl")
                for row in table.findall(f"{{{A}}}tr")
                for cell in row.findall(f"{{{A}}}tc")
                for paragraph in cell.findall(f"{{{A}}}txBody/{{{A}}}p")]
    if shape.tag == f"{{{P}}}sp":
        return shape.findall(f"{{{P}}}txBody/{{{A}}}p")
    if shape.tag == f"{{{A}}}tc":
        return shape.findall(f"{{{A}}}txBody/{{{A}}}p")
    return []


def _shape_text(shape):
    # Context has exactly the supported text surface. Unsupported graphic data
    # must not enter paid prompts merely because it contains DrawingML nodes.
    return "\n".join("".join(node.text or "" for node in paragraph.iter(f"{{{A}}}t"))
                     for paragraph in _paragraphs(shape))


def _table_context(shape, cell):
    table = next(cell.iterancestors(f"{{{A}}}tbl"), None)
    if table is None:
        return {}
    row = cell.getparent()
    rows = table.findall(f"{{{A}}}tr")
    cells = row.findall(f"{{{A}}}tc")
    column = cells.index(cell)
    return {
        "row": rows.index(row) + 1, "column": column + 1,
        "current_row": [_shape_text(item) for item in cells],
        "first_row": [_shape_text(item) for item in rows[0].findall(f"{{{A}}}tc")],
        "merges": [(item.get("gridSpan", "1"), item.get("rowSpan", "1"),
                    item.get("hMerge", "0"), item.get("vMerge", "0")) for item in cells],
    }


def extract_presentation(document):
    document.presentation_slides = slide_parts(document)
    document.presentation_contexts = {}
    document.presentation_roles = {}
    document.presentation_layout = {}
    skipped = set()
    for slide_number, part in enumerate(document.presentation_slides, 1):
        document.check_cancel()
        root = document._tree(part).getroot()
        tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
        if tree is None:
            raise FormaError("У PPTX відсутня структура фігур слайда.")
        shapes = list(_shapes(tree))
        shape_texts = {shape: _shape_text(shape) for shape in shapes if not _hidden(shape)}
        slide_text = "\n".join(shape_texts.values())
        titles = [text for shape, text in shape_texts.items() if _shape_role(shape) in {"title", "ctrTitle"}]
        title = "\n".join(value for value in titles if value.strip())
        if root.get("show", "1") in {"0", "false"}:
            skipped.add("hidden_slides")
        for shape_number, shape in enumerate(shapes, 1):
            document.check_cancel()
            role = _shape_role(shape)
            if _hidden(shape):
                skipped.add("hidden_shapes")
                continue
            shape_text = shape_texts[shape]
            paragraphs = _paragraphs(shape)
            for paragraph_number, paragraph in enumerate(paragraphs, 1):
                nodes, markers = [], []
                for child in paragraph:
                    if child.tag == f"{{{A}}}r":
                        nodes.extend(child.findall(f"{{{A}}}t"))
                    elif child.tag in {f"{{{A}}}br", f"{{{A}}}fld"}:
                        markers.append(child)
                        if child.tag == f"{{{A}}}fld":
                            skipped.add("fields")
                cell = next(paragraph.iterancestors(f"{{{A}}}tc"), None)
                full = {"slide": slide_number, "title": title, "slide_text": slide_text,
                        "shape_index": shape_number, "shape_role": role,
                        "shape_text": shape_text, "table": _table_context(shape, cell) if cell is not None else {}}
                readable = {**full, "title": _excerpt(title), "slide_text": _excerpt(slide_text, 1800),
                            "shape_text": _excerpt(full["shape_text"])}
                if full["table"]:
                    readable["table"] = {**full["table"],
                                         "current_row": [_excerpt(text, 200) for text in full["table"]["current_row"]],
                                         "first_row": [_excerpt(text, 200) for text in full["table"]["first_row"]]}
                before = len(document.segments)
                document._add(part, f"Slide {slide_number} · shape {shape_number} · paragraph {paragraph_number}",
                              nodes, markers, context=json.dumps(readable, ensure_ascii=False, sort_keys=True))
                if len(document.segments) == before:
                    continue
                segment = document.segments[-1]
                document.presentation_contexts[segment.id] = full
                document.presentation_roles[segment.id] = {"placeholder": role, "table": cell is not None,
                                                          "column": full["table"].get("column")}
                document.presentation_layout[segment.id] = shape, paragraph
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            local = node.tag.rsplit("}", 1)[-1]
            if local in {"chart", "relIds", "oleObj", "pic"}:
                skipped.add({"chart": "charts", "relIds": "smartart", "oleObj": "objects", "pic": "images"}[local])
    if any(name.startswith("ppt/notesSlides/") and name.endswith(".xml") for name in document.parts):
        skipped.add("notes")
    for name in document.parts:
        if name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")) and name.endswith(".xml"):
            if any((node.text or "").strip() for node in document._tree(name).iter(f"{{{A}}}t")):
                skipped.add("masters")
    document.issues.append(Issue("pptx_visual_review", "Переклад PPTX зберігає розміри фігур і шрифти. Перевірте слайди візуально: переклад може не вміститися в текстові блоки.", severity="info"))
    for code in sorted(skipped):
        messages = {
            "charts": "Текст діаграм PPTX і їхні вбудовані дані залишено без перекладу.",
            "smartart": "Текст SmartArt у PPTX залишено без перекладу.",
            "objects": "Текст вкладених об’єктів PPTX залишено без перекладу.",
            "images": "Текст усередині зображень PPTX не перекладається автоматично.",
            "notes": "Нотатки доповідача PPTX залишено без перекладу.",
            "masters": "Текст зразків і макетів слайдів PPTX залишено без перекладу.",
            "fields": "Автоматичні поля PPTX збережено без змін.",
            "hidden_shapes": "Приховані фігури PPTX залишено без перекладу.",
            "hidden_slides": "Приховані слайди PPTX також включено до перекладу.",
        }
        document.issues.append(Issue("pptx_" + code + "_preserved", messages[code], severity="info"))


def layout_issues(document, translations):
    """Flag substantial expansion and conservative explicit-size fit estimates.

    This is not a renderer: inherited fonts, wrapping, autofit and group scaling
    can alter the actual result. Never change font sizes or shorten translations.
    """
    issues = []
    for segment in document.segments:
        translated = plain_text(translations.get(segment.id, segment.source))
        source = segment.plain
        shape, paragraph = document.presentation_layout[segment.id]
        expansion = len(translated) > max(len(source) * 1.5, len(source) + 30)
        extent = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}ext")
        if extent is None:
            extent = shape.find(f"{{{P}}}xfrm/{{{A}}}ext")
        sizes = [node.get("sz", "") for node in paragraph.iter() if isinstance(node.tag, str) and node.get("sz")]
        explicit_fit = False
        try:
            width, height = int(extent.get("cx")), int(extent.get("cy"))
            point_size = max(int(value) for value in sizes) / 100
            if width > 0 and height > 0 and 0 < point_size < 1000:
                # EMUs -> points; a deliberately rough average glyph width.
                columns = max(1, width / 12700 / (point_size * 0.55))
                needed = sum(max(1, math.ceil(len(line) / columns)) for line in translated.splitlines() or [""])
                explicit_fit = needed * point_size * 1.2 > height / 12700
        except (AttributeError, ValueError, TypeError):
            pass
        if expansion or explicit_fit:
            issues.append(Issue("pptx_text_fit", "Текст перекладу може не вміститися у фігуру слайда. Це приблизна оцінка; перевірте слайд візуально.", segment.id))
    return issues
