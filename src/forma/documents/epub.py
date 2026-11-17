"""Native EPUB containers; publication metadata and binary resources stay intact."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
import struct
import tempfile
import zlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from lxml import etree

from forma.core.models import FormaError, Issue
from forma.documents.ooxml import MAX_UNPACKED, MAX_XML, _ZipParts, parse_xml

OCF = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
ENC = "http://www.w3.org/2001/04/xmlenc#"
MIMETYPE = b"application/epub+zip"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_PARTS = 20000
MAX_CONTENT_BYTES = 64 * 1024 * 1024
MAX_CONTENT_SEGMENTS = 100000
_XHTML = "application/xhtml+xml"
_NCX = "application/x-dtbncx+xml"
_FONTS = {"font/otf", "font/ttf", "font/woff", "font/woff2", "application/vnd.ms-opentype",
          "application/font-sfnt", "application/font-woff", "application/x-font-ttf",
          "application/x-font-truetype", "application/x-font-opentype"}
_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
_ERROR = "Не вдалося прочитати пакет EPUB. Перевірте структуру та цілісність файла."
_URI_ERROR = "EPUB містить небезпечний або неоднозначний шлях до ресурсу."
_ENCRYPTION = "EPUB містить зашифрований вміст або непідтримуваний захист. Потрібна незашифрована копія."


def _safe_zip_name(name):
    return bool(name and "\\" not in name and ":" not in name and not name.startswith("/")
                and not any(ord(char) < 32 or ord(char) == 127 for char in name)
                and posixpath.normpath(name.rstrip("/")) == name.rstrip("/")
                and all(part not in {"", ".", ".."} for part in name.rstrip("/").split("/")))


def resolve_package_uri(basepart, href, *, allow_fragment=False):
    """Decode a local resource URI once, retaining container-relative semantics."""
    if not isinstance(href, str) or not href or re.search(r"%(?![0-9A-Fa-f]{2})", href):
        raise FormaError(_URI_ERROR)
    try:
        uri = urlsplit(href)
        if uri.scheme or uri.netloc or uri.query or uri.fragment and not allow_fragment or not uri.path and not (allow_fragment and uri.fragment) or uri.path.startswith("/"):
            raise ValueError
        path = unquote_to_bytes(uri.path).decode("utf-8", errors="strict")
        if "\\" in path or ":" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
            raise ValueError
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(basepart), path)) if path else basepart
        if not _safe_zip_name(resolved) or resolved.endswith("/"):
            raise ValueError
        return resolved
    except (ValueError, UnicodeError) as exc:
        raise FormaError(_URI_ERROR) from exc


@dataclass(frozen=True)
class ManifestItem:
    identifier: str
    href: str
    media_type: str
    path: str | None
    properties: frozenset[str]


class EpubDocument:
    extraction_profile = 13
    engine = "forma-epub"

    def __init__(self, path: Path, *, extraction_profile=None, check_cancel=lambda: None):
        if extraction_profile is not None and (type(extraction_profile) is not int or extraction_profile != 13):
            raise ValueError("Unsupported EPUB extraction profile")
        self.path, self.check_cancel = Path(path), check_cancel
        self.trees, self.segments, self.targets, self.issues = {}, [], {}, []
        self.content_documents = {}
        self.check_cancel()
        try:
            with self.path.open("rb") as source:
                self._archive_preflight(source)
                original = os.fstat(source.fileno())
                with ZipFile(source) as archive:
                    self.infos, self.zip_comment = archive.infolist(), archive.comment
                if len(self.infos) > MAX_PARTS or sum(info.file_size for info in self.infos) > MAX_UNPACKED:
                    raise FormaError("Документ завеликий для безпечного розпакування (ліміт 512 МБ).")
                if len({info.filename for info in self.infos}) != len(self.infos):
                    raise FormaError("Документ містить дублікати частин ZIP.")
                if any(not _safe_zip_name(info.orig_filename) or stat.S_ISLNK(info.external_attr >> 16)
                       or info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED} for info in self.infos):
                    raise FormaError(_ERROR)
                if not self.infos or self.infos[0].filename != "mimetype" or self.infos[0].compress_type != ZIP_STORED or self.infos[0].extra or self.infos[0].header_offset != 0:
                    raise FormaError("EPUB має починатися з нестисненого файла mimetype без додаткових полів ZIP.")
                source.seek(0)
                header = source.read(30)
                if len(header) != 30 or header[:4] != b"PK\x03\x04" or struct.unpack_from("<HH", header, 26) != (8, 0):
                    raise FormaError("EPUB має починатися з нестисненого файла mimetype без додаткових полів ZIP.")
                self.parts = _ZipParts(self.path, self.infos, self.check_cancel)
                if self.parts.signature != (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns):
                    raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
            with self.parts.reading():
                self._package()
                self._encryption()
                self._extract()
            self.source_sha256 = self._digest()
        except (BadZipFile, RuntimeError, EOFError, zlib.error, etree.XMLSyntaxError) as exc:
            raise FormaError(_ERROR) from exc

    def _archive_preflight(self, source):
        size = os.fstat(source.fileno()).st_size
        if size > MAX_ARCHIVE_BYTES:
            raise FormaError("Файл EPUB завеликий. Ліміт архіву — 512 МБ.")
        source.seek(max(0, size - 65557))
        tail = source.read(65557)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise FormaError(_ERROR)
        fields = struct.unpack_from("<4s4H2IH", tail, offset)
        _, disk, directory_disk, disk_count, count, directory_size, directory_offset, comment = fields
        if disk or directory_disk or disk_count != count or offset + 22 + comment != len(tail):
            raise FormaError(_ERROR)
        if count > MAX_PARTS or directory_size > MAX_DIRECTORY_BYTES or directory_offset == 0xFFFFFFFF:
            raise FormaError("Пакет EPUB має завеликі ZIP-метадані або забагато частин.")
        if directory_offset + directory_size > size - len(tail) + offset:
            raise FormaError(_ERROR)
        source.seek(0)

    def add_issue(self, code, message, severity="info"):
        if not any(issue.code == code for issue in self.issues):
            self.issues.append(Issue(code, message, severity=severity))

    def tree(self, part):
        self.check_cancel()
        if part not in self.trees:
            if self.parts.infos[part].file_size > MAX_XML:
                raise FormaError(f"XML-частина документа завелика: {part}")
            tree = parse_xml(self.parts[part])
            if any(node.tag == "{http://www.w3.org/2000/09/xmldsig#}Signature" for node in tree.iter()):
                raise FormaError("Документ має цифровий підпис. Для перекладу створіть непідписану копію.")
            if any("{http://www.w3.org/XML/1998/namespace}base" in node.attrib for node in tree.iter()):
                raise FormaError("EPUB використовує непідтримуване перенаправлення базових URI у файлах пакета.")
            self.trees[part] = tree
        return self.trees[part]

    def resolve_uri(self, basepart, href, *, allow_fragment=False):
        return resolve_package_uri(basepart, href, allow_fragment=allow_fragment)

    def _package(self):
        if not {"mimetype", "META-INF/container.xml"} <= set(self.parts) or self.parts.infos["mimetype"].file_size != len(MIMETYPE) or self.parts["mimetype"] != MIMETYPE:
            raise FormaError("У EPUB відсутній правильний mimetype або META-INF/container.xml.")
        if any(name.lower().startswith("meta-inf/") and "signature" in name.lower() for name in self.parts):
            raise FormaError("Документ має цифровий підпис. Для перекладу створіть непідписану копію.")
        container = self.tree("META-INF/container.xml").getroot()
        rootfiles = container.findall(f"{{{OCF}}}rootfiles")
        roots = container.findall(f"{{{OCF}}}rootfiles/{{{OCF}}}rootfile")
        if container.tag != f"{{{OCF}}}container" or len(rootfiles) != 1 or len(roots) != 1 or roots[0].get("media-type") != "application/oebps-package+xml":
            raise FormaError("Підтримується EPUB з одним однозначно визначеним документом пакета OPF.")
        self.package_path = self.resolve_uri("", roots[0].get("full-path", ""))
        if self.package_path not in self.parts:
            raise FormaError("Пакет EPUB посилається на відсутній ресурс.")
        package = self.tree(self.package_path).getroot()
        if package.tag != f"{{{OPF}}}package" or package.get("version") not in {"2.0", "3.0"}:
            raise FormaError("Підтримуються звичайні пакети EPUB 2 та EPUB 3 з документом OPF.")
        self.version = package.get("version")
        package_ids = [node.get("id") for node in package.iter() if isinstance(node.tag, str) and node.get("id")]
        if len(package_ids) != len(set(package_ids)):
            raise FormaError("У EPUB неунікальні ідентифікатори документа пакета.")
        if any(len(package.findall(f"{{{OPF}}}{name}")) != 1 for name in ("metadata", "manifest", "spine")):
            raise FormaError("У EPUB відсутні метадані, маніфест або порядок читання spine.")
        metadata, manifest, spine = (package.find(f"{{{OPF}}}{name}") for name in ("metadata", "manifest", "spine"))
        unique = package.get("unique-identifier", "")
        identifiers = metadata.findall(f"{{{DC}}}identifier")
        selected = [node for node in identifiers if node.get("id") == unique]
        if not unique or len(selected) != 1 or not (selected[0].text or "").strip():
            raise FormaError("У EPUB відсутній однозначний ідентифікатор публікації.")
        self.publication_identifier = selected[0].text
        self.manifest, paths = {}, set()
        for node in manifest:
            self.check_cancel()
            if not isinstance(node.tag, str):
                continue
            if node.tag != f"{{{OPF}}}item":
                raise FormaError("У EPUB некоректний маніфест ресурсів.")
            identifier, href, media_type = node.get("id", ""), node.get("href", ""), node.get("media-type", "")
            if not identifier or identifier in self.manifest or not href or not media_type:
                raise FormaError("У EPUB некоректний маніфест ресурсів.")
            try:
                uri = urlsplit(href)
            except ValueError as exc:
                raise FormaError(_URI_ERROR) from exc
            if uri.scheme in {"http", "https"} and uri.netloc:
                path = None
                self.add_issue("epub_remote_resources", "Зовнішні ресурси EPUB збережено без завантаження. Вони можуть потребувати мережі у програмі читання.")
            else:
                path = self.resolve_uri(self.package_path, href)
                if path not in self.parts or path.endswith("/"):
                    raise FormaError("Пакет EPUB посилається на відсутній ресурс.")
                if path in paths:
                    raise FormaError("У EPUB кілька записів маніфесту посилаються на один ресурс.")
                paths.add(path)
            self.manifest[identifier] = ManifestItem(identifier, href, media_type, path, frozenset(node.get("properties", "").split()))
        spine_paths, seen = [], set()
        for itemref in spine:
            self.check_cancel()
            if not isinstance(itemref.tag, str):
                continue
            identifier = itemref.get("idref", "")
            item = self.manifest.get(identifier)
            if itemref.tag != f"{{{OPF}}}itemref" or item is None or identifier in seen:
                raise FormaError("У EPUB некоректний порядок читання spine.")
            if item.path is None or item.media_type != _XHTML:
                raise FormaError("Підтримується EPUB з локальними XHTML-документами в основному порядку читання.")
            seen.add(identifier)
            spine_paths.append(item.path)
        if not spine_paths:
            raise FormaError("У EPUB порожній порядок читання spine.")
        self.spine = tuple(spine_paths)
        toc = spine.get("toc")
        if toc and (toc not in self.manifest or self.manifest[toc].media_type != _NCX or self.manifest[toc].path is None):
            raise FormaError("У EPUB некоректне посилання на навігацію NCX.")
        nav = [item for item in self.manifest.values() if "nav" in item.properties]
        if len(nav) > 1 or any(item.path is None or item.media_type != _XHTML for item in nav):
            raise FormaError("У EPUB некоректний документ навігації XHTML.")
        auxiliary = [item.path for item in self.manifest.values() if item.path and item.media_type == _XHTML and "nav" not in item.properties and item.path not in self.spine]
        ncx = [item.path for item in self.manifest.values() if item.path and item.media_type == _NCX]
        self.content_order = tuple(dict.fromkeys([*self.spine, *auxiliary, *(item.path for item in nav), *ncx]))
        self.content_roles = {part: "ncx" if part in ncx else "nav" if any(item.path == part for item in nav) else "spine" if part in self.spine else "auxiliary" for part in self.content_order}
        if sum(self.parts.infos[part].file_size for part in self.content_order) > MAX_CONTENT_BYTES:
            raise FormaError("Текстові ресурси EPUB завеликі. Сумарний ліміт XHTML і NCX — 64 МБ.")

    def _encryption(self):
        if "META-INF/encryption.xml" not in self.parts:
            return
        root = self.tree("META-INF/encryption.xml").getroot()
        if root.tag != f"{{{OCF}}}encryption":
            raise FormaError(_ENCRYPTION)
        declared = {item.path: item for item in self.manifest.values() if item.path}
        seen = set()
        for encrypted in root:
            if not isinstance(encrypted.tag, str):
                continue
            methods = encrypted.findall(f"{{{ENC}}}EncryptionMethod")
            references = encrypted.findall(f"{{{ENC}}}CipherData/{{{ENC}}}CipherReference")
            if (encrypted.tag != f"{{{ENC}}}EncryptedData" or len(methods) != 1 or len(references) != 1
                    or methods[0].get("Algorithm") not in _OBFUSCATION
                    or any(etree.QName(node).localname in {"EncryptedKey", "KeyInfo", "CipherValue"} for node in encrypted.iter() if isinstance(node.tag, str))):
                raise FormaError(_ENCRYPTION)
            part = self.resolve_uri("", references[0].get("URI", ""))
            item = declared.get(part)
            if part in seen or item is None or item.media_type not in _FONTS or Path(part).suffix.lower() not in {".otf", ".ttf", ".woff", ".woff2"}:
                raise FormaError(_ENCRYPTION)
            seen.add(part)
        if seen:
            self.add_issue("epub_font_obfuscation", "Стандартне приховування шрифтів EPUB збережено разом з ідентифікатором публікації; це не розшифрування захищеної книги.")

    def _extract(self):
        from forma.documents.epub_content import EpubContent

        for document_index, part in enumerate(self.content_order, 1):
            self.check_cancel()
            if self.parts.infos[part].file_size > MAX_XML:
                raise FormaError(f"XML-частина документа завелика: {part}")
            document = EpubContent(self.parts[part], part=part, role=self.content_roles[part], check_cancel=self.check_cancel)
            self.content_documents[part] = document
            if len(self.segments) + len(document.segments) > MAX_CONTENT_SEGMENTS:
                raise FormaError("EPUB містить забагато текстових фрагментів. Розділіть публікацію на менші книги.")
            for fragment_index, segment in enumerate(document.segments, 1):
                if segment.id in self.targets:
                    raise FormaError("Не вдалося безпечно зіставити текст зі структурою документа.")
                segment.location = f"EPUB · document {document_index} · fragment {fragment_index}"
                self.segments.append(segment)
                self.targets[segment.id] = part
            for issue in document.issues:
                if not any(existing.code == issue.code and existing.segment_id == issue.segment_id for existing in self.issues):
                    self.issues.append(issue)
        self.add_issue("epub_metadata_preserved", "Назва, автори, мови та ідентифікатори у видавничих метаданих EPUB збережені. Перекладено лише підтримуваний видимий текст і навігаційні написи.")
        self.add_issue("epub_visual_review", "Перевірте EPUB у програмі читання: переноси, розміщення тексту та підтримка шрифтів залежать від неї.")

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
            raise FormaError(_ERROR) from exc
        if self._digest() != self.source_sha256:
            raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")

    def layout_issues(self, translations):
        return []

    def comparison_text(self):
        return "\n".join(document.comparison_text() for document in self.content_documents.values())

    def structure_signature(self):
        result = []
        try:
            with self.parts.reading() as archive:
                for info in self.infos:
                    self.check_cancel()
                    if info.filename in self.content_documents:
                        value = self.content_documents[info.filename].structure_signature()
                    else:
                        digest = hashlib.sha256()
                        with archive.open(info.filename) as stream:
                            while chunk := stream.read(1024 * 1024):
                                self.check_cancel()
                                digest.update(chunk)
                        value = digest.hexdigest()
                    result.append((info.filename, value))
        except (BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
            self.check_cancel()
            raise FormaError(_ERROR) from exc
        return self.extraction_profile, tuple(result)

    def write(self, translations, output: Path, *, decimal_comma_to_point=False):
        output = Path(output)
        self.check_cancel()
        if output.resolve() == self.path.resolve():
            raise FormaError("Результат має зберігатися в окремий файл.")
        if set(translations) != set(self.targets):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".forma-", dir=output.parent)
        temporary = Path(name)
        content_size = 0
        immutable_size = sum(info.file_size for info in self.infos if info.filename not in self.content_documents)
        try:
            with os.fdopen(fd, "w+b") as stream, self.parts.reading() as original, ZipFile(stream, "w") as archive:
                archive.comment = self.zip_comment
                for info in self.infos:
                    self.parts.check()
                    document = self.content_documents.get(info.filename)
                    if document is not None:
                        values = {segment.id: translations[segment.id] for segment in document.segments}
                        data = document.write(values)
                        content_size += len(data)
                        if content_size > MAX_CONTENT_BYTES:
                            raise FormaError("Текстові ресурси EPUB завеликі. Сумарний ліміт XHTML і NCX — 64 МБ.")
                        if content_size + immutable_size > MAX_UNPACKED:
                            raise FormaError("Документ завеликий для безпечного розпакування (ліміт 512 МБ).")
                        archive.writestr(deepcopy(info), data)
                    else:
                        with original.open(info.filename) as source, archive.open(deepcopy(info), "w") as destination:
                            while chunk := source.read(1024 * 1024):
                                self.check_cancel()
                                destination.write(chunk)
                archive.close()
                stream.flush()
                if stream.tell() > MAX_ARCHIVE_BYTES:
                    raise FormaError("Файл EPUB завеликий. Ліміт архіву — 512 МБ.")
                os.fsync(stream.fileno())
            self.parts.check()
            if self._digest() != self.source_sha256:
                raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
            os.link(temporary, output)
        except (BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
            self.check_cancel()
            raise FormaError(_ERROR) from exc
        finally:
            temporary.unlink(missing_ok=True)
