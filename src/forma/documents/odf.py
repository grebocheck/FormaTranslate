"""Native OpenDocument packages with surgical XML text-slot replacement."""

from __future__ import annotations

import hashlib
import os
import posixpath
import stat
import struct
import tempfile
import zlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from lxml import etree

from forma.core.models import FormaError, Issue, Segment, plain_text
from forma.documents.ooxml import MAX_UNPACKED, MAX_XML, _ZipParts, parse_xml
from forma.translation.quality import parse_translation

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
SVG = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
XLINK = "http://www.w3.org/1999/xlink"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
_KINDS = {".odt": ((8, 15), "text"), ".ods": ((9,), "spreadsheet"), ".odp": ((10, 16), "presentation")}
_PARAGRAPHS = {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}
_INLINE = {f"{{{TEXT}}}span", f"{{{TEXT}}}a", f"{{{TEXT}}}meta"}
_CONTROLS = {f"{{{TEXT}}}s", f"{{{TEXT}}}tab", f"{{{TEXT}}}line-break"}
_OMITTED = {f"{{{OFFICE}}}annotation", f"{{{OFFICE}}}annotation-end", f"{{{TEXT}}}tracked-changes",
            f"{{{OFFICE}}}scripts",
            f"{{{TEXT}}}deletion", f"{{{TEXT}}}hidden-text", f"{{{TEXT}}}hidden-paragraph",
            f"{{{PRESENTATION}}}notes", f"{{{DRAW}}}object", f"{{{DRAW}}}object-ole", f"{{{DRAW}}}image",
            f"{{{TABLE}}}covered-table-cell"}
_ARCHIVE_ERROR = "Не вдалося прочитати пакет OpenDocument. Перевірте, чи файл не пошкоджений і не зашифрований."
MAX_EXPANDED_TEXT = 64 * 1024 * 1024


def _safe_name(name, *, root=False):
    if root and name == "/":
        return True
    return bool(name and "\x00" not in name and "\\" not in name and ":" not in name
                and not name.startswith("/") and posixpath.normpath(name.rstrip("/")) == name.rstrip("/")
                and all(part not in {".", "..", ""} for part in name.rstrip("/").split("/")))


@dataclass
class ParagraphTarget:
    part: str
    paragraph: etree._Element
    slots: list[tuple[etree._Element, str]]
    markers: list[etree._Element]
    role: str


class OpenDocument:
    engine = "forma-odf"

    def __init__(self, path: Path, *, extraction_profile=None, check_cancel=lambda: None):
        self.path, self.check_cancel = Path(path), check_cancel
        if self.path.suffix.lower() not in _KINDS:
            raise FormaError("Цей рушій підтримує ODT, ODS і ODP.")
        profiles, self.kind = _KINDS[self.path.suffix.lower()]
        if extraction_profile is not None and (type(extraction_profile) is not int or extraction_profile not in profiles):
            raise ValueError("Unsupported OpenDocument extraction profile")
        self.extraction_profile = profiles[-1] if extraction_profile is None else extraction_profile
        self.trees, self.segments, self.targets, self.issues = {}, [], {}, []
        self._paragraphs, self._string_values, self._style_cache = [], [], {}
        self._hidden_layers = set()
        self._source_roots, self._counted_paragraphs, self._expanded_size = set(), set(), 0
        self.check_cancel()
        try:
            with ZipFile(self.path) as archive:
                self.infos, self.zip_comment = archive.infolist(), archive.comment
                if len(self.infos) > 20000 or sum(info.file_size for info in self.infos) > MAX_UNPACKED:
                    raise FormaError("Документ завеликий для безпечного розпакування (ліміт 512 МБ).")
                if len({info.filename for info in self.infos}) != len(self.infos):
                    raise FormaError("Документ містить дублікати частин ZIP.")
                if any(not _safe_name(info.orig_filename) or stat.S_ISLNK(info.external_attr >> 16)
                       or info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED} for info in self.infos):
                    raise FormaError(_ARCHIVE_ERROR)
                if not self.infos or self.infos[0].filename != "mimetype" or self.infos[0].compress_type != ZIP_STORED or self.infos[0].extra:
                    raise FormaError("Пакет OpenDocument має містити перший нестиснений файл mimetype без додаткових полів ZIP.")
                with self.path.open("rb") as stream:
                    header = stream.read(30)
                if len(header) != 30 or header[:4] != b"PK\x03\x04" or self.infos[0].header_offset != 0 or struct.unpack_from("<HH", header, 26) != (8, 0):
                    raise FormaError("Пакет OpenDocument має містити перший нестиснений файл mimetype без додаткових полів ZIP.")
                self.parts = _ZipParts(self.path, self.infos, self.check_cancel)
            with self.parts.reading():
                self._validate_manifest()
                self.styles = {}
                for part in ("content.xml", "styles.xml"):
                    if part in self.parts:
                        for style in self.tree(part).iter(f"{{{STYLE}}}style"):
                            self.styles.setdefault(style.get(f"{{{STYLE}}}name", ""), []).append(style)
                        for style in self.tree(part).iter(f"{{{STYLE}}}default-style"):
                            self.styles.setdefault("__default__:" + style.get(f"{{{STYLE}}}family", ""), []).append(style)
                        # Legacy profiles remain available for reconstructing
                        # completed reports. New extraction excludes objects on
                        # layers hidden both on screen and in printed output.
                        if self.extraction_profile in {15, 16}:
                            self._hidden_layers.update(
                                name for layer in self.tree(part).iter(f"{{{DRAW}}}layer")
                                if (name := layer.get(f"{{{DRAW}}}name"))
                                and layer.get(f"{{{DRAW}}}display", "always") == "none"
                            )
                if self.kind == "spreadsheet":
                    from forma.documents.odf_spreadsheet import extract_spreadsheet

                    extract_spreadsheet(self)
                elif self.kind == "presentation":
                    self._presentation()
                else:
                    self._text_document()
            self.source_sha256 = self._digest()
        except (BadZipFile, RuntimeError, EOFError, zlib.error, etree.XMLSyntaxError) as exc:
            raise FormaError(_ARCHIVE_ERROR) from exc

    def tree(self, part):
        self.check_cancel()
        if part not in self.trees:
            if self.parts.infos[part].file_size > MAX_XML:
                raise FormaError(f"XML-частина документа завелика: {part}")
            self.trees[part] = parse_xml(self.parts[part])
            self._source_roots.add(self.trees[part].getroot())
        return self.trees[part]

    def _validate_manifest(self):
        if not {"mimetype", "content.xml", "META-INF/manifest.xml"} <= set(self.parts):
            raise FormaError("У пакеті OpenDocument відсутній mimetype, content.xml або маніфест.")
        if self.parts.infos["mimetype"].file_size > 256:
            raise FormaError(_ARCHIVE_ERROR)
        expected = "application/vnd.oasis.opendocument." + self.kind
        if self.parts["mimetype"] != expected.encode("ascii"):
            raise FormaError("Розширення файла, mimetype та маніфест OpenDocument не узгоджені.")
        if any(name.lower().startswith("meta-inf/") and "signature" in name.lower() for name in self.parts):
            raise FormaError("Документ має цифровий підпис. Для перекладу створіть непідписану копію.")
        manifest = self.tree("META-INF/manifest.xml").getroot()
        if manifest.tag != f"{{{MANIFEST}}}manifest":
            raise FormaError("У пакеті OpenDocument некоректний маніфест.")
        entries = {}
        for node in manifest.iter():
            self.check_cancel()
            if not isinstance(node.tag, str):
                continue
            if etree.QName(node).localname in {"encryption-data", "encrypted-key", "EncryptedKey"}:
                raise FormaError("Зашифровані пакети OpenDocument не підтримуються. Створіть незашифровану копію.")
            if node.tag == f"{{{MANIFEST}}}file-entry":
                name = node.get(f"{{{MANIFEST}}}full-path", "")
                if not _safe_name(name, root=True) or name in entries:
                    raise FormaError("У пакеті OpenDocument некоректний маніфест.")
                entries[name] = node.get(f"{{{MANIFEST}}}media-type", "")
                if name != "/" and not name.endswith("/") and name not in self.parts:
                    raise FormaError("Маніфест OpenDocument посилається на відсутню частину пакета.")
        if entries.get("/") != expected or entries.get("content.xml") != "text/xml":
            raise FormaError("Розширення файла, mimetype та маніфест OpenDocument не узгоджені.")
        if any(not name.endswith("/") and name != "mimetype" and not name.startswith("META-INF/") and name not in entries for name in self.parts):
            raise FormaError("Маніфест OpenDocument не описує всі частини документа.")
        content = self.tree("content.xml").getroot()
        if content.tag != f"{{{OFFICE}}}document-content" or content.find(f"{{{OFFICE}}}body/{{{OFFICE}}}{self.kind}") is None:
            raise FormaError("Тип вмісту OpenDocument не відповідає розширенню файла.")

    def _digest(self):
        self.parts.check()
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                self.check_cancel()
                digest.update(chunk)
        self.parts.check()
        return digest.hexdigest()

    def validate_package(self):
        try:
            with self.parts.reading() as archive:
                for info in self.infos:
                    self.parts.check()
                    with archive.open(info.filename) as stream:
                        while stream.read(1024 * 1024):
                            self.check_cancel()
                self.parts.check()
        except (BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
            raise FormaError(_ARCHIVE_ERROR) from exc
        if self._digest() != self.source_sha256:
            raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")

    def add_issue(self, code, message, severity="info"):
        if not any(issue.code == code for issue in self.issues):
            self.issues.append(Issue(code, message, severity=severity))

    def _style_protected(self, name):
        if name in self._style_cache:
            return self._style_cache[name]
        initial = name
        pending, seen, protected = [name], set(), False
        while pending and len(seen) < 128:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            if name.replace("_20_", " ").casefold() in {"source text", "preformatted text", "code", "html preformatted"}:
                protected = True
            for style in self.styles.get(name, ()):
                display = style.get(f"{{{STYLE}}}display-name", "").casefold()
                if display in {"source text", "preformatted text", "code", "html preformatted"}:
                    protected = True
                if any(etree.QName(key).localname in {"display", "visibility"} and value in {"none", "hidden"}
                       for child in style.iter() for key, value in child.attrib.items()):
                    protected = True
                parent = style.get(f"{{{STYLE}}}parent-style-name")
                if parent:
                    pending.append(parent)
                elif not name.startswith("__default__:"):
                    pending.append("__default__:" + style.get(f"{{{STYLE}}}family", ""))
        self._style_cache[initial] = protected
        return protected

    def eligible(self, element):
        if element.tag in _PARAGRAPHS and element.find(f".//{{{TEXT}}}hidden-paragraph") is not None:
            return False
        for node in (element, *element.iterancestors()):
            if node.tag in _OMITTED:
                return False
            if node.get(f"{{{DRAW}}}layer") in self._hidden_layers:
                return False
            if any(value in {"none", "hidden"} and etree.QName(key).localname in {"display", "visibility"}
                   or value in {"true", "1"} and etree.QName(key).localname == "is-hidden"
                   or key == f"{{{STYLE}}}display" and value in {"false", "0"} for key, value in node.attrib.items()):
                return False
            if node.tag == f"{{{TEXT}}}section" and node.get(f"{{{TEXT}}}display", "true") != "true":
                return False
            for key in (f"{{{TEXT}}}style-name", f"{{{DRAW}}}style-name", f"{{{PRESENTATION}}}style-name"):
                if self._style_protected(node.get(key, "")):
                    return False
            if node.tag in _PARAGRAPHS and self._style_protected("__default__:paragraph"):
                return False
            if node.tag in _INLINE and self._style_protected("__default__:text"):
                return False
            if isinstance(node.tag, str) and etree.QName(node).namespace == DRAW:
                family = "drawing-page" if node.tag == f"{{{DRAW}}}page" else "graphic"
                if self._style_protected("__default__:" + family):
                    return False
        return True

    def _events(self, paragraph):
        events = []

        def emit(kind, node, attr, value):
            nonlocal visible_size
            visible_size += len(value)
            if visible_size > 26000 or len(events) >= 20000:
                raise FormaError("Документ містить абзац довший за 26 000 символів. Розділіть його перед перекладом.")
            events.append((kind, node, attr, value))

        visible_size = 0

        def visit(node):
            self.check_cancel()
            # Empty slots remain explicit: moving translated words between rich
            # runs must not change their IDs or the text-neutral XML topology.
            emit("r", node, "text", node.text or "")
            for child in node:
                if child.tag in _INLINE and self.eligible(child):
                    visit(child)
                else:
                    visible = ""
                    if child.tag == f"{{{TEXT}}}s":
                        try:
                            count = int(child.get(f"{{{TEXT}}}c", "1"))
                            if not 1 <= count <= 26000:
                                raise ValueError
                        except ValueError as exc:
                            raise FormaError("У OpenDocument некоректна кількість повторених пробілів.") from exc
                        visible = " " * count
                    elif child.tag == f"{{{TEXT}}}tab":
                        visible = "\t"
                    elif child.tag == f"{{{TEXT}}}line-break":
                        visible = "\n"
                    elif isinstance(child.tag, str) and child.tag not in _CONTROLS:
                        self.add_issue("odf_protected_inline", "Поля, примітки та непідтримувані вбудовані елементи OpenDocument збережено без змін.")
                    emit("k", child, "", visible)
                emit("r", child, "tail", child.tail or "")
        visit(paragraph)
        return events

    def paragraph_text(self, paragraph):
        if not self.eligible(paragraph):
            return ""
        events = self._events(paragraph)
        self._account_paragraph(paragraph, events)
        return "".join(event[3] for event in events)

    def _account_paragraph(self, paragraph, events):
        if paragraph not in self._counted_paragraphs and paragraph.getroottree().getroot() in self._source_roots:
            self._expanded_size += sum(len(event[3]) for event in events)
            self._counted_paragraphs.add(paragraph)
            if self._expanded_size > MAX_EXPANDED_TEXT:
                raise FormaError("Забагато розгорнутого тексту OpenDocument. Розділіть документ на менші файли.")

    def add_paragraph(self, part, paragraph, location, context="", role="body"):
        self.check_cancel()
        if not self.eligible(paragraph):
            self.add_issue("odf_hidden_preserved", "Прихований текст, видалення та відомі стилі коду OpenDocument залишено без перекладу.")
            return None
        events = self._events(paragraph)
        self._account_paragraph(paragraph, events)
        if len(self._paragraphs) >= 100000:
            raise FormaError("Документ містить забагато абзаців. Розділіть його на менші файли.")
        self._paragraphs.append(paragraph)
        if not any(char.isalpha() for kind, _, _, value in events if kind == "r" for char in value):
            return None
        slots, markers, markup = [], [], []
        for kind, node, attr, value in events:
            if kind == "r":
                markup.append(f"<r{len(slots)}>{escape(value)}</r{len(slots)}>")
                slots.append((node, attr))
            else:
                markup.append(f"<k{len(markers)}/>")
                markers.append(node)
        if sum(len(value) for _, _, _, value in events) > 26000:
            raise FormaError("Документ містить абзац довший за 26 000 символів. Розділіть його перед перекладом.")
        sid = f"odf-v{self.extraction_profile}:{part}#{len(self.segments)}"
        self.segments.append(Segment(sid, location, "".join(markup), context[:4000]))
        self.targets[sid] = ParagraphTarget(part, paragraph, slots, markers, role)
        return sid

    def register_string_value(self, cell, paragraphs):
        value = cell.get(f"{{{OFFICE}}}string-value")
        if value is None:
            return True
        if value != "\n".join(self.paragraph_text(paragraph) for paragraph in paragraphs):
            return False
        self._string_values.append((cell, tuple(paragraphs)))
        return True

    def _text_document(self):
        body = self.tree("content.xml").find(f"{{{OFFICE}}}body/{{{OFFICE}}}text")
        self._extract_container("content.xml", body, "body")
        if "styles.xml" in self.parts:
            roles = {}
            for node in self.tree("styles.xml").iter():
                if node.tag in {f"{{{STYLE}}}{name}" for name in ("header", "header-left", "header-first", "footer", "footer-left", "footer-first")}:
                    role = etree.QName(node).localname
                    roles[role] = roles.get(role, 0) + 1
                    self._extract_container("styles.xml", node, f"{role} {roles[role]}")
        self._scope_notices()

    def _extract_container(self, part, container, role):
        paragraphs = []
        for node in container.iter():
            self.check_cancel()
            if node.tag in _PARAGRAPHS:
                paragraphs.append(node)
                if len(paragraphs) > 100000:
                    raise FormaError("Документ містить забагато абзаців. Розділіть його на менші файли.")
        texts = {node: self.paragraph_text(node)[:700] for node in paragraphs}
        for index, paragraph in enumerate(paragraphs, 1):
            # Context uses only the same safe text surface as extraction.
            context = "\n".join(texts[node][:700] for node in paragraphs[max(0, index - 2):index + 1] if texts[node])
            self.add_paragraph(part, paragraph, f"{role} · paragraph {index}", context, role)

    def _presentation(self):
        body = self.tree("content.xml").find(f"{{{OFFICE}}}body/{{{OFFICE}}}presentation")
        for index, page in enumerate(body.findall(f"{{{DRAW}}}page"), 1):
            self._extract_container("content.xml", page, f"Slide {index}")
        self.add_issue("odf_presentation_layout", "ODP зберігає розміри фігур і шрифти. Перевірте слайди візуально: переклад може не вміститися.")
        self._scope_notices()

    def _scope_notices(self):
        root = self.tree("content.xml")
        if any(node.tag in {f"{{{DRAW}}}object", f"{{{DRAW}}}object-ole", f"{{{DRAW}}}image"} for node in root.iter()):
            self.add_issue("odf_objects_preserved", "Текст зображень, діаграм і вкладених об’єктів OpenDocument залишено без перекладу.")
        if root.find(f".//{{{PRESENTATION}}}notes") is not None:
            self.add_issue("odf_notes_preserved", "Нотатки доповідача ODP залишено без перекладу.")
        if self.kind == "presentation" and "styles.xml" in self.parts:
            self.add_issue("odf_masters_preserved", "Текст зразків і макетів ODP залишено без перекладу.")

    def layout_issues(self, translations):
        if self.kind != "presentation":
            return []
        return [Issue("odf_text_fit", "Текст перекладу може не вміститися у фігуру ODP. Це приблизна оцінка; перевірте слайд візуально.", segment.id)
                for segment in self.segments if len(plain_text(translations.get(segment.id, segment.source))) > max(len(segment.plain) * 1.5, len(segment.plain) + 30)]

    def comparison_text(self):
        return "\n".join([self.paragraph_text(paragraph) for paragraph in self._paragraphs]
                         + getattr(self, "spreadsheet_comparison_values", []))

    def structure_signature(self):
        editable = {}
        node_positions = {part: {node: index for index, node in enumerate(self.tree(part).iter())}
                          for part in {target.part for target in self.targets.values()} | ({"content.xml"} if self._string_values else set())}
        for target in self.targets.values():
            slots = editable.setdefault(target.part, set())
            slots.update((node_positions[target.part][node], attr) for node, attr in target.slots)
        result = []
        with self.parts.reading() as archive:
            for info in self.infos:
                self.check_cancel()
                if info.filename.endswith(".xml"):
                    original = self.tree(info.filename)
                    tree = deepcopy(original)
                    for index, node in enumerate(tree.iter()):
                        for attr in ("text", "tail"):
                            if (index, attr) in editable.get(info.filename, ()):
                                setattr(node, attr, "__TEXT__")
                    if info.filename == "content.xml":
                        registered = {node_positions[info.filename][cell] for cell, _ in self._string_values}
                        for index, node in enumerate(tree.iter()):
                            if index in registered:
                                node.set(f"{{{OFFICE}}}string-value", "__TEXT__")
                    digest = hashlib.sha256(etree.tostring(tree, method="c14n")).hexdigest()
                else:
                    value = hashlib.sha256()
                    with archive.open(info.filename) as stream:
                        while chunk := stream.read(1024 * 1024):
                            self.check_cancel()
                            value.update(chunk)
                    digest = value.hexdigest()
                result.append((info.filename, digest))
        return self.extraction_profile, tuple(result)

    def write(self, translations, output: Path, *, decimal_comma_to_point=False):
        output = Path(output)
        if output.resolve() == self.path.resolve():
            raise FormaError("Результат має зберігатися в окремий файл.")
        if set(translations) != set(self.targets):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        try:
            parsed = {segment.id: parse_translation(segment.source, translations[segment.id]) for segment in self.segments}
        except UnicodeError as exc:
            raise FormaError("Переклад містить некоректні символи Unicode.") from exc
        staged, indices = {}, {}

        def editable(part, node):
            if part not in staged:
                original = self.tree(part)
                staged[part] = deepcopy(original)
                indices[part] = dict(zip(original.iter(), staged[part].iter(), strict=True))
            return indices[part][node]

        for segment in self.segments:
            self.check_cancel()
            target = self.targets[segment.id]
            for index, (node, attr) in enumerate(target.slots):
                value = parsed[segment.id][f"r{index}"]
                if value != (getattr(node, attr) or ""):
                    setattr(editable(target.part, node), attr, value)
        if "content.xml" in staged:
            for cell, paragraphs in self._string_values:
                clone = indices["content.xml"][cell]
                value = "\n".join(self.paragraph_text(indices["content.xml"][paragraph]) for paragraph in paragraphs)
                clone.set(f"{{{OFFICE}}}string-value", value)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".forma-", dir=output.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w+b") as stream, self.parts.reading() as original, ZipFile(stream, "w") as archive:
                archive.comment = self.zip_comment
                for info in self.infos:
                    self.parts.check()
                    if info.filename in staged:
                        raw = self.parts[info.filename]
                        data = etree.tostring(staged[info.filename], encoding="UTF-8", xml_declaration=raw.lstrip().startswith(b"<?xml"), standalone=self.tree(info.filename).docinfo.standalone)
                        archive.writestr(deepcopy(info), data)
                    else:
                        with original.open(info.filename) as source, archive.open(deepcopy(info), "w") as destination:
                            while chunk := source.read(1024 * 1024):
                                self.check_cancel()
                                destination.write(chunk)
                archive.close()
                stream.flush()
                os.fsync(stream.fileno())
            self.parts.check()
            if self._digest() != self.source_sha256:
                raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
