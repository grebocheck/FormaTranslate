"""Synthetic EPUB package fixtures; no publisher data or external dependencies."""
from __future__ import annotations

from base64 import b64decode
from copy import copy
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

UID = "urn:uuid:22222222-2222-4222-8222-222222222222"
XHTML = "http://www.w3.org/1999/xhtml"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_DOCTYPE = '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
NCX_DOCTYPE = '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">'
CHAPTER = (f'<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
           '<head><title>PRIVATE_TITLE</title><link rel="stylesheet" href="style.css"/><script>PRIVATE_SCRIPT()</script></head>'
           '<body><h1 id="PRIVATE_HEADING">Hello chapter</h1><p>Hello <em>world</em> 25 kW.<br/>Second line.</p>'
           '<p>Read <a href="chapter2.xhtml#PRIVATE_ANCHOR">the next chapter</a>.</p>'
           '<p>Use <code>PRIVATE_CODE()</code> carefully.</p><p hidden="hidden">PRIVATE_HIDDEN</p>'
           '<p translate="no">PRIVATE_UNTRANSLATED</p><pre>PRIVATE_PRE</pre>'
           '<p><img src="images/cover.png" alt="PRIVATE_ALT"/>Visible image caption.</p>'
           '<p>42</p></body></html>')
CHAPTER2 = (f'<html xmlns="{XHTML}"><head><title>PRIVATE_SECOND_TITLE</title></head><body>'
            '<p id="PRIVATE_ANCHOR">Hello again.</p></body></html>')
NAV = (f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>PRIVATE_NAV_TITLE</title></head>'
       '<body><nav epub:type="toc" id="PRIVATE_TOC"><h1>Contents</h1><ol>'
       '<li><a href="chapter.xhtml#PRIVATE_HEADING">First chapter</a></li>'
       '<li><a href="chapter2.xhtml#PRIVATE_ANCHOR">Second chapter</a></li></ol></nav></body></html>')
NCX = (f'<?xml version="1.0" encoding="UTF-8"?>\n{NCX_DOCTYPE}\n<ncx xmlns="{NCX_NS}" version="2005-1">'
       '<head><meta name="dtb:uid" content="urn:uuid:PRIVATE_UID"/></head><docTitle><text>PRIVATE_TITLE</text></docTitle>'
       '<navMap><navPoint id="PRIVATE_NAV_1" playOrder="1"><navLabel><text>First chapter</text></navLabel>'
       '<content src="chapter.xhtml#PRIVATE_HEADING"/></navPoint><navPoint id="PRIVATE_NAV_2" playOrder="2">'
       '<navLabel><text>Second chapter</text></navLabel><content src="chapter2.xhtml#PRIVATE_ANCHOR"/></navPoint></navMap></ncx>')


def make_epub(path, *, version="3.0", legacy_doctype=False, extra_parts=None, parts=None, opf=None):
    """Write a small EPUB2/3 with rich prose, nav, NCX and immutable resources."""
    modern = version.startswith("3")
    navigation = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if modern else ""
    extras = {**(parts or {}), **(extra_parts or {})}
    extra_items = []
    known = {"OPS/chapter.xhtml", "OPS/chapter2.xhtml", "OPS/nav.xhtml", "OPS/toc.ncx", "OPS/style.css", "OPS/images/cover.png", "OPS/package.opf"}
    for index, (name, data) in enumerate(extras.items()):
        if name.startswith("OPS/") and name not in known and data is not None:
            media = {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2",
                     "xhtml": "application/xhtml+xml", "html": "application/xhtml+xml", "css": "text/css",
                     "png": "image/png", "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            href = escape(name[4:], {'"': "&quot;"})
            extra_items.append(f'<item id="extra{index}" href="{href}" media-type="{media}"/>')
    package = opf or (
        f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="pub-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="pub-id">' + UID + '</dc:identifier>'
        '<dc:title>PRIVATE_BOOK_TITLE</dc:title><dc:language>en</dc:language>'
        + ('<meta property="dcterms:modified">2026-11-17T00:00:00Z</meta>' if modern else '') + '</metadata><manifest>'
        '<item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"' + (' properties="scripted"' if modern else '') + '/>' + navigation +
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="style" href="style.css" media-type="text/css"/>'
        '<item id="cover" href="images/cover.png" media-type="image/png"/>'
        + ''.join(extra_items) + '</manifest><spine toc="ncx"><itemref idref="chapter"/><itemref idref="chapter2"/></spine></package>'
    )
    entries = {
        "META-INF/container.xml": '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles>'
                                  '<rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OPS/package.opf": package, "OPS/chapter.xhtml": CHAPTER, "OPS/chapter2.xhtml": CHAPTER2,
        "OPS/toc.ncx": NCX.replace("urn:uuid:PRIVATE_UID", UID), "OPS/style.css": '/* PRIVATE_CSS */ p { color: black; }',
        "OPS/images/cover.png": b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII="),
    }
    if modern:
        entries["OPS/nav.xhtml"] = NAV
    else:
        entries["OPS/chapter.xhtml"] = entries["OPS/chapter.xhtml"].replace('hidden="hidden"', 'style="display:none"').replace('translate="no"', 'style="display:none"').replace('<script>', '<script type="text/javascript">')
    if legacy_doctype:
        for name in ("OPS/chapter.xhtml", "OPS/chapter2.xhtml"):
            raw = entries[name]
            start = raw.find("?>") + 2 if raw.startswith("<?xml") else 0
            entries[name] = raw[:start] + XHTML_DOCTYPE + raw[start:]
    entries.update(extras)
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
        for name, content in entries.items():
            if content is not None:
                archive.writestr(name, content.encode("utf-8") if isinstance(content, str) else content, compress_type=ZIP_DEFLATED)
    return path


def epub_parts(path):
    with ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def replace_epub_part(path, name, transform):
    with ZipFile(path) as archive:
        entries = [(copy(info), archive.read(info)) for info in archive.infolist()]
        comment = archive.comment
    with ZipFile(path, "w") as archive:
        archive.comment = comment
        for info, data in entries:
            archive.writestr(info, transform(data) if info.filename == name else data)
