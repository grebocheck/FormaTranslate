"""Create an inactive OpenDocument copy exclusively for local page rendering."""
from __future__ import annotations

import posixpath
import struct
import zipfile
from copy import copy
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree

from forma.core.models import FormaError

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
SVG = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
XLINK = "http://www.w3.org/1999/xlink"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_ASSETS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ttf", ".otf"}
_REMOVED = {
    (OFFICE, name) for name in ("scripts", "forms", "dde-source", "event-listeners", "binary-data")
} | {
    (DRAW, name) for name in ("object", "object-ole", "plugin", "applet", "floating-frame", "control")
} | {(TEXT, name) for name in ("script", "section-source", "dde-connection-decls", "dde-connection-decl",
                              "variable-decls", "variable-decl", "user-field-decls", "user-field-decl",
                              "sequence-decls", "sequence-decl")} | {(TABLE, "table-source")}
_RESOURCES = {(DRAW, "image"), (DRAW, "fill-image"), (STYLE, "background-image"), (SVG, "font-face-uri")}
_STATIC_FIELDS = {"execute-macro", "expression", "dde-connection", "database-display", "database-name",
                  "database-next", "database-select", "database-row-number", "variable-set", "variable-get",
                  "variable-input", "user-field-get", "user-field-input", "sequence", "conditional-text"}


def _static_asset(archive, name, check_cancel):
    """Use binary signatures, since image importers ignore a misleading suffix."""
    check_cancel()
    suffix = Path(name).suffix.lower()
    with archive.open(name) as stream:
        header = stream.read(33)
        if suffix == ".png":
            return len(header) == 33 and header.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        if suffix in {".jpg", ".jpeg"}:
            return header.startswith(b"\xff\xd8\xff")
        if suffix == ".gif":
            return header.startswith((b"GIF87a", b"GIF89a"))
        if suffix == ".bmp":
            return len(header) >= 26 and header.startswith(b"BM")
        if suffix in {".tif", ".tiff"}:
            return header.startswith((b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"))
        if suffix == ".webp":
            return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if suffix in {".ttf", ".otf"} and len(header) >= 12 and header[:4] in {b"\x00\x01\x00\x00", b"true", b"OTTO"}:
            tables = struct.unpack_from(">H", header, 4)[0]
            if not 1 <= tables <= 4096:
                return False
            stream.seek(12)
            directory = stream.read(tables * 16)
            if len(directory) != tables * 16:
                return False
            size = archive.getinfo(name).file_size
            for offset in range(0, len(directory), 16):
                check_cancel()
                tag, _, start, length = struct.unpack_from(">4sIII", directory, offset)
                if tag == b"SVG " or start + length > size:
                    return False
            return True
    return False


def _remove(node):
    parent = node.getparent()
    if parent is not None:
        # Keep adjacent cached text when an active inline field is removed.
        if node.tail:
            previous = node.getprevious()
            if previous is None:
                parent.text = (parent.text or "") + node.tail
            else:
                previous.tail = (previous.tail or "") + node.tail
        parent.remove(node)


def _asset_target(value, members):
    value = unquote(value).replace("\\", "/")
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or value.startswith("/") or "%" in value):
        return ""
    target = posixpath.normpath(value)
    return target if target in members and not target.startswith("../") else ""


def _inactive_xml(data, members, check_cancel):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("doctype")
    for node in list(root.iter()):
        check_cancel()
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node)
        pair = name.namespace, name.localname
        if name.namespace == TEXT and name.localname in _STATIC_FIELDS:
            node.tag = f"{{{TEXT}}}span"
            node.attrib.clear()
            pair = TEXT, "span"
        if (pair in _REMOVED or name.namespace in {
            "urn:oasis:names:tc:opendocument:xmlns:script:1.0",
            "urn:oasis:names:tc:opendocument:xmlns:animation:1.0",
            "urn:oasis:names:tc:opendocument:xmlns:smil-compatible:1.0",
            "urn:oasis:names:tc:opendocument:xmlns:database:1.0",
        } or name.localname in {"event-listener", "event-listeners", "sound", "auto-reload"}):
            _remove(node)
            continue
        for attribute in list(node.attrib):
            attr = etree.QName(attribute)
            if attribute == f"{{{XLINK}}}href":
                target = _asset_target(node.get(attribute), members) if pair in _RESOURCES else ""
                if target:
                    node.set(attribute, target)
                else:
                    del node.attrib[attribute]
            elif (attribute == XML_BASE or attr.namespace == XLINK
                  or attr.namespace == "urn:oasis:names:tc:opendocument:xmlns:script:1.0"
                  or attr.localname in {"src", "archive", "code", "notify-on-update-of-ranges", "formula", "condition"}
                  or attr.localname.startswith("dde-")):
                del node.attrib[attribute]
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def prepare_odf(source: Path, destination: Path, check_cancel) -> None:
    """Keep static content and packaged raster/font resources in a private ZIP.

    No original is changed. Embedded objects, scripts, SVG, settings and external
    links are excluded from the renderer's copy; page appearance may differ.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (len(entries) > 20000 or len(names) != len(entries)
                    or sum(entry.file_size for entry in entries) > 512 * 1024**2
                    or not entries or entries[0].filename != "mimetype"
                    or entries[0].compress_type != zipfile.ZIP_STORED
                    or entries[0].extra):
                raise ValueError("package")
            if any(info.flag_bits & 1 or info.file_size > 64 * 1024**2 for info in entries):
                raise ValueError("size or encryption")
            kept = {name for name in names if name in {"mimetype", "content.xml", "styles.xml", "META-INF/manifest.xml"}
                    or (Path(name).suffix.lower() in _ASSETS
                        and not name.startswith(("/", "Basic/", "Scripts/", "META-INF/"))
                        and ".." not in name.split("/") and "\\" not in name
                        and _static_asset(archive, name, check_cancel))}
            manifest = etree.fromstring(_inactive_xml(archive.read("META-INF/manifest.xml"), kept, check_cancel),
                                       etree.XMLParser(resolve_entities=False, no_network=True))
            if (manifest.getroottree().docinfo.doctype or any(
                    isinstance(node.tag, str) and etree.QName(node).localname in {"encryption-data", "encrypted-key", "EncryptedKey"}
                    for node in manifest.iter())):
                raise ValueError("manifest")
            media_type = {".odt": b"application/vnd.oasis.opendocument.text",
                          ".odp": b"application/vnd.oasis.opendocument.presentation"}.get(source.suffix.lower())
            roots = [node for node in manifest if node.get(f"{{{MANIFEST}}}full-path") == "/"]
            if (media_type is None or archive.read("mimetype") != media_type or len(roots) != 1
                    or roots[0].get(f"{{{MANIFEST}}}media-type") != media_type.decode("ascii")):
                raise ValueError("mimetype")
            for node in list(manifest):
                if node.get(f"{{{MANIFEST}}}full-path") not in kept | {"/"}:
                    manifest.remove(node)
            with zipfile.ZipFile(destination, "w") as result:
                for entry in entries:
                    check_cancel()
                    if entry.filename not in kept:
                        continue
                    if entry.filename == "META-INF/manifest.xml":
                        content = etree.tostring(manifest, encoding="UTF-8", xml_declaration=True)
                    elif entry.filename in {"content.xml", "styles.xml"}:
                        content = _inactive_xml(archive.read(entry), kept, check_cancel)
                    else:
                        content = archive.read(entry)
                    result.writestr(copy(entry), content)
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        destination.unlink(missing_ok=True)
        raise FormaError("Не вдалося підготувати OpenDocument для перегляду. Перевірте, чи відкривається файл у редакторі.") from exc
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
