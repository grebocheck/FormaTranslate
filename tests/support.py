"""Shared offline providers and event-loop waits; never a collected test module."""
from __future__ import annotations

import copy
import time
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from forma.core.models import Cancelled
from forma.providers.client import JobControl
from forma.settings.config import Settings


def make_pptx(path, *, title="Hello", body="Hello world 25 kW", second_title="Other slide", reverse_order=False, extra_parts=None):
    """Small real OOXML deck shared by domain and desktop workflow tests."""
    from forma.documents.ooxml import A, R
    from forma.documents.pptx import PACKAGE_R, P

    def shape(sid, text, *, placeholder="", runs=""):
        ph = f'<p:ph type="{placeholder}"/>' if placeholder else ""
        runs = runs or f'<a:r><a:rPr sz="1800"/><a:t>{escape(text)}</a:t></a:r>'
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Shape {sid}"/>'
                f'<p:cNvSpPr/><p:nvPr>{ph}</p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="1000" y="1000"/><a:ext cx="7000000" cy="1500000"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>'
                f'<a:p>{runs}</a:p></p:txBody></p:sp>')

    def slide(content):
        return (f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
                + content + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')

    rich = (f'<a:r><a:rPr b="1" sz="1800"/><a:t>{escape(body)}</a:t></a:r>'
            '<a:br/><a:fld id="{00000000-0000-0000-0000-000000000001}" type="slidenum"><a:t>1</a:t></a:fld>')
    group = ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="4" name="Group"/><p:cNvGrpSpPr/><p:nvPr/>'
             '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="1000" y="2000000"/>'
             '<a:ext cx="7000000" cy="1500000"/><a:chOff x="0" y="0"/>'
             '<a:chExt cx="7000000" cy="1500000"/></a:xfrm></p:grpSpPr>'
             + shape(5, "Grouped text") + '</p:grpSp>')
    table = ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="6" name="Table"/><p:cNvGraphicFramePr/>'
             '<p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="0" y="0"/><a:ext cx="5000000" cy="1500000"/>'
             '</p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
             '<a:tbl><a:tblPr/><a:tblGrid><a:gridCol w="2500000"/><a:gridCol w="2500000"/></a:tblGrid>'
             '<a:tr h="1000000">'
             + ''.join('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>' + value
                       + '</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc>' for value in ("Category", "Energy"))
             + '</a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    ids = ("rSecond", "rFirst") if reverse_order else ("rFirst", "rSecond")
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/ppt/slides/slide9.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        '<Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{PACKAGE_R}"><Relationship Id="office" Type="{R}/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rMaster"/></p:sldMasterIdLst><p:sldIdLst>'
        + ''.join(f'<p:sldId id="{256 + index}" r:id="{rid}"/>' for index, rid in enumerate(ids))
        + '</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PACKAGE_R}">'
        f'<Relationship Id="rMaster" Type="{R}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        f'<Relationship Id="rFirst" Type="{R}/slide" Target="slides/slide9.xml"/>'
        f'<Relationship Id="rSecond" Type="{R}/slide" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide2.xml": slide(shape(2, second_title, placeholder="title") + shape(3, "Other body")),
        "ppt/slides/slide9.xml": slide(shape(2, title, placeholder="title") + shape(3, "", runs=rich) + group + table),
        "custom/untouched.bin": b"unchanged binary asset",
    }
    empty_tree = ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
                  '</p:nvGrpSpPr><p:grpSpPr/></p:spTree>')
    parts["ppt/slideMasters/slideMaster1.xml"] = (
        f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld>{empty_tree}</p:cSld>'
        '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6"'
        ' bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rLayout"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
    )
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = (
        f'<Relationships xmlns="{PACKAGE_R}"><Relationship Id="rLayout" Type="{R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rTheme" Type="{R}/theme" Target="../theme/theme1.xml"/></Relationships>'
    )
    parts["ppt/slideLayouts/slideLayout1.xml"] = (
        f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" type="blank" preserve="1">'
        f'<p:cSld name="Blank">{empty_tree}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = (
        f'<Relationships xmlns="{PACKAGE_R}"><Relationship Id="rMaster" Type="{R}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'
    )
    for slide_name in ("slide9", "slide2"):
        parts[f"ppt/slides/_rels/{slide_name}.xml.rels"] = (
            f'<Relationships xmlns="{PACKAGE_R}"><Relationship Id="rLayout" Type="{R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'
        )
    colors = ("000000", "FFFFFF", "1F497D", "EEECE1", "4F81BD", "C0504D", "9BBB59", "8064A2", "4BACC6", "F79646", "0000FF", "800080")
    color_names = ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink")
    solid = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    parts["ppt/theme/theme1.xml"] = (
        f'<a:theme xmlns:a="{A}" name="Synthetic theme"><a:themeElements><a:clrScheme name="Synthetic colors">'
        + ''.join(f'<a:{name}><a:srgbClr val="{value}"/></a:{name}>' for name, value in zip(color_names, colors, strict=True))
        + '</a:clrScheme><a:fontScheme name="Synthetic fonts">'
        '<a:majorFont><a:latin typeface="DejaVu Sans"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="DejaVu Sans"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        '</a:fontScheme><a:fmtScheme name="Synthetic format"><a:fillStyleLst>' + solid * 3
        + '</a:fillStyleLst><a:lnStyleLst>' + ('<a:ln w="9525">' + solid + '<a:prstDash val="solid"/></a:ln>') * 3
        + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
        + '</a:effectStyleLst><a:bgFillStyleLst>' + solid * 3
        + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
    )
    parts.update(extra_parts or {})
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return path


def make_odf(path, *, body=None, extra_parts=None):
    """Synthetic native ODF fixture; includes genuine manifest and page styles."""
    from forma.documents.odf import (
        DRAW,
        MANIFEST,
        OFFICE,
        PRESENTATION,
        STYLE,
        SVG,
        TABLE,
        TEXT,
        XLINK,
    )

    kind = {".odt": "text", ".ods": "spreadsheet", ".odp": "presentation"}[path.suffix]
    media = "application/vnd.oasis.opendocument." + kind
    namespaces = ' '.join(f'xmlns:{name}="{value}"' for name, value in {
        "office": OFFICE, "text": TEXT, "style": STYLE, "table": TABLE, "draw": DRAW,
        "presentation": PRESENTATION, "svg": SVG, "xlink": XLINK,
        "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    }.items())
    rich = ('<text:p>Hello <text:span text:style-name="Strong">world</text:span> 25 kW.'
            '<text:s text:c="2"/>Read<text:tab/>next<text:line-break/>'
            '<text:a xlink:type="simple" xlink:href="https://example.invalid/path">label</text:a>'
            ' page <text:page-number text:select-page="current">7</text:page-number> ending.</text:p>')
    table = ('<table:table table:name="Table1"><table:table-column table:number-columns-repeated="2"/>'
             '<table:table-row><table:table-cell office:value-type="string"><text:p>Category</text:p></table:table-cell>'
             '<table:table-cell office:value-type="string"><text:p>Energy</text:p></table:table-cell></table:table-row></table:table>')
    frame = '<draw:frame draw:style-name="gr1" svg:x="1cm" svg:y="1cm" svg:width="18cm" svg:height="6cm"><draw:text-box>' + rich + '</draw:text-box></draw:frame>'
    if body is None:
        body = {"text": rich + table + '<text:p>Note<text:note text:id="ft1" text:note-class="footnote">'
                '<text:note-citation>1</text:note-citation><text:note-body><text:p>Footnote text</text:p></text:note-body></text:note></text:p>',
                "spreadsheet": '<table:table table:name="Sheet1"><table:table-column/>'
                '<table:table-row><table:table-cell office:value-type="string"><text:p>Hello world</text:p>'
                '</table:table-cell></table:table-row></table:table>',
                "presentation": '<draw:page draw:name="Slide1" draw:style-name="dp1" draw:master-page-name="Standard">'
                + frame + '<draw:g><draw:frame draw:style-name="gr1" svg:x="1cm" svg:y="8cm" svg:width="18cm" svg:height="2cm">'
                '<draw:text-box><text:p>Grouped text</text:p></draw:text-box></draw:frame></draw:g>'
                '<draw:frame draw:style-name="gr1" svg:x="1cm" svg:y="11cm" svg:width="18cm" svg:height="2cm">'
                + table + '</draw:frame><presentation:notes><draw:frame draw:style-name="gr1" svg:width="18cm" svg:height="2cm">'
                '<draw:text-box><text:p>Private speaker notes</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>',
                }[kind]
    automatic = ('<office:automatic-styles><style:style style:name="gr1" style:family="graphic">'
                 '<style:graphic-properties draw:stroke="none" draw:fill="none"/>'
                 '<style:text-properties fo:font-size="18pt"/></style:style>'
                 '<style:style style:name="dp1" style:family="drawing-page"><style:drawing-page-properties/></style:style></office:automatic-styles>')
    parts = {
        "content.xml": f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {namespaces} office:version="1.3">'
        f'{automatic}<office:body><office:{kind}>{body}</office:{kind}></office:body></office:document-content>',
        "styles.xml": f'<?xml version="1.0" encoding="UTF-8"?><office:document-styles {namespaces} office:version="1.3">'
        '<office:styles><style:style style:name="Strong" style:family="text"><style:text-properties fo:font-weight="bold"/>'
        '</style:style></office:styles><office:automatic-styles><style:page-layout style:name="pm1">'
        '<style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm" fo:margin="1cm"/>'
        '</style:page-layout></office:automatic-styles><office:master-styles>'
        '<style:master-page style:name="Standard" style:page-layout-name="pm1">'
        + ('<style:header><text:p>Header text</text:p></style:header><style:footer><text:p>Footer text</text:p></style:footer>' if kind == "text" else '')
        + '</style:master-page></office:master-styles></office:document-styles>',
        "custom/untouched.bin": b"unchanged binary asset",
    }
    parts.update(extra_parts or {})
    manifest = f'<manifest:manifest xmlns:manifest="{MANIFEST}" manifest:version="1.3">'
    manifest += f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{media}" manifest:version="1.3"/>'
    for name in parts:
        if name not in {"mimetype", "META-INF/manifest.xml"}:
            mime = "text/xml" if name.endswith(".xml") else "application/octet-stream"
            manifest += f'<manifest:file-entry manifest:full-path="{escape(name)}" manifest:media-type="{mime}"/>'
    manifest += '</manifest:manifest>'
    parts.setdefault("META-INF/manifest.xml", manifest)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", media, compress_type=ZIP_STORED)
        for name, data in parts.items():
            if name != "mimetype":
                archive.writestr(name, data)
    return path


def pump(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        # Let Python workers acquire the GIL between Qt event-loop passes.
        # QTest.qWait can otherwise starve QThread tasks doing Python work.
        time.sleep(0.001)
    assert predicate(), "Desktop operation did not finish"


def settle(app, backend, timeout=5):
    pump(app, lambda: not backend._threads, timeout=timeout)


class FakeProvider:
    input_tokens = 120
    output_tokens = 80

    def __init__(self, *, invalid=False, interrupt_after=None, control=None):
        self.calls = []
        self.invalid = invalid
        self.interrupt_after = interrupt_after
        self.control = control

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append((instruction, payload))
        if self.interrupt_after is not None and len(self.calls) > self.interrupt_after:
            raise Cancelled("Interrupted")
        if "terms" in schema["properties"]:
            return {"terms": [{"source": "heat meter", "target": "лічильник тепла"}]}
        segments = [
            {
                "id": s["id"],
                "text": s["text"]
                .replace("Heat meter", "Лічильник тепла")
                .replace("sensor", "датчик")
                .replace("Item", "Елемент")
                .replace("Value", "Значення")
                .replace("Current clause", "Поточне положення"),
            }
            for s in payload["segments"]
        ]
        if self.invalid:
            for segment in segments:
                segment["text"] += "<r999>hallucination 999</r999>"
        return {"segments": segments}



def local_settings():
    return Settings(
        base_url="http://localhost/v1",
        model="test-model",
        max_parallel_requests=1,
        parallel_documents=1,
    )



class TextProvider:
    def __init__(self, transform=None, *, review_model=""):
        self.control = JobControl()
        self.settings = Settings(model="translator", review_model=review_model)
        self.calls = []
        self.transform = transform

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append((instruction, copy.deepcopy(payload), kwargs))
        segments = [
            {"id": item["id"], "text": item.get("draft", item.get("text", ""))}
            for item in payload["segments"]
        ]
        if self.transform:
            return self.transform(segments, len(self.calls))
        for segment in segments:
            segment["text"] = segment["text"].replace("Hello", "Вітаю").replace("world", "світ")
        return {"segments": segments}

