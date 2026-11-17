"""Prepare a private, inactive Office copy for installed LibreOffice rendering."""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree

from forma.core.models import FormaError


def find_libreoffice() -> str:
    for name in ("soffice.com", "libreoffice", "soffice", "soffice.exe"):
        if found := shutil.which(name):
            return found
    candidates = [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    if sys.platform == "darwin":
        candidates.append(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice")
    if sys.platform == "win32":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if folder := os.environ.get(key):
                candidates.append(Path(folder) / "LibreOffice/program/soffice.exe")
    return next((str(path) for path in candidates if path.is_file()), "")


def prepare_office(source: Path, destination: Path, check_cancel) -> None:
    """Keep cached visible content; remove external/active loading instructions.

    This copy is only for viewing. Hyperlink labels and cached field values stay
    visible, while external images/objects cannot be fetched during conversion.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as result:
            entries = archive.infolist()
            members = {entry.filename for entry in entries}
            if (len(entries) > 10000 or len(members) != len(entries)
                    or sum(entry.file_size for entry in entries) > 250 * 1024**2):
                raise ValueError("size")
            for entry in entries:
                check_cancel()
                if entry.file_size > 64 * 1024**2 or entry.flag_bits & 1:
                    raise ValueError("size")
                content = archive.read(entry)
                if entry.filename.endswith((".xml", ".rels")):
                    tree = etree.fromstring(content, parser)
                    if tree.getroottree().docinfo.doctype:
                        raise ValueError("doctype")
                    for node in list(tree.iter()):
                        if not isinstance(node.tag, str):
                            continue
                        local = etree.QName(node).localname
                        target = unquote(node.get("Target", "")).replace("\\", "/")
                        base = entry.filename.rsplit("/_rels/", 1)[0] if "/_rels/" in entry.filename else ""
                        relative = target.split("#", 1)[0]
                        internal = posixpath.normpath(relative.lstrip("/") if relative.startswith("/")
                                                     else posixpath.join(base, relative))
                        if local == "Relationship" and (
                            node.get("TargetMode", "").lower() == "external"
                            or urlsplit(target).scheme or target.startswith("//")
                            or internal not in members
                            or any(term in node.get("Type", "").lower() for term in ("vbaproject", "oleobject", "attachedtemplate", "/audio", "/video", "/media"))
                        ):
                            node.getparent().remove(node)
                        elif source.suffix.lower() == ".pptx" and local in {"hlinkClick", "hlinkMouseOver", "timing", "transition"}:
                            # Render a static slide; never follow program actions or play media.
                            if node.getparent() is not None:
                                node.getparent().remove(node)
                        elif local == "instrText" and node.text and not re.fullmatch(r"\s*(?:PAGE|NUMPAGES)\s*", node.text, re.I):
                            node.text = ""
                        for attribute, value in list(node.attrib.items()):
                            if etree.QName(attribute).localname == "instr" and not re.fullmatch(r"\s*(?:PAGE|NUMPAGES)\s*", value, re.I):
                                node.attrib[attribute] = ""
                            # Legacy VML images may specify a URL without a relationship.
                            if etree.QName(attribute).localname in {"src", "href"} and re.match(r"(?i)(?:[a-z][a-z0-9+.-]*:|\\\\|//)", value):
                                del node.attrib[attribute]
                    content = etree.tostring(tree, encoding="UTF-8", xml_declaration=True)
                result.writestr(entry, content)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        message = ("Не вдалося підготувати PPTX для перегляду. Перевірте, чи відкривається файл у редакторі презентацій."
                   if source.suffix.lower() == ".pptx" else
                   "Не вдалося підготувати DOCX для перегляду. Перевірте, чи відкривається файл у текстовому редакторі.")
        raise FormaError(message) from exc


def prepare_docx(source: Path, destination: Path, check_cancel) -> None:
    prepare_office(source, destination, check_cancel)


def prepare_profile(profile: Path) -> None:
    user = profile / "user"
    user.mkdir(parents=True, mode=0o700)
    # Never reuse a personal profile, update links, run macros, or contact update services.
    settings = '''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop><prop oor:name="Field" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']/Arguments"><prop oor:name="AutoCheckEnabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetProxyType" oor:op="fuse"><value>1</value></prop><prop oor:name="ooInetHTTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop><prop oor:name="ooInetHTTPProxyPort" oor:op="fuse"><value>9</value></prop><prop oor:name="ooInetHTTPSProxyName" oor:op="fuse"><value>127.0.0.1</value></prop><prop oor:name="ooInetHTTPSProxyPort" oor:op="fuse"><value>9</value></prop><prop oor:name="ooInetFTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop><prop oor:name="ooInetFTPProxyPort" oor:op="fuse"><value>9</value></prop><prop oor:name="ooInetNoProxy" oor:op="fuse"><value></value></prop></item>
</oor:items>'''
    (user / "registrymodifications.xcu").write_text(settings, encoding="utf-8")
