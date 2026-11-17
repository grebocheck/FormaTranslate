"""Small native fixtures for source-bound OpenDocument compatibility tests."""

from zipfile import ZipFile

from support import make_odf


def make_hidden_layer_odf(path):
    """Mirror LibreOffice's standard layer flags; keep hidden prose before visible IDs."""
    def frame(layer, text):
        assignment = f' draw:layer="{layer}"' if layer else ""
        return (f'<draw:frame{assignment} draw:style-name="gr1" svg:width="18cm" svg:height="2cm">'
                f'<draw:text-box><text:p>{text}</text:p></draw:text-box></draw:frame>')

    body = (frame("private", "PRIVATE HIDDEN LAYER") + frame("public", "Hello world 25 kW")
            + '<draw:g draw:layer="private">' + frame("", "PRIVATE HIDDEN GROUP") + '</draw:g>'
            + frame("screen", "Screen label") + frame("printer", "Printer label")
            + frame("", "Unassigned visible label"))
    if path.suffix.lower() == ".odp":
        body = '<draw:page draw:name="Slide1" draw:style-name="dp1" draw:master-page-name="Standard">' + body + '</draw:page>'
    source = make_odf(path, body=body)
    with ZipFile(source) as archive:
        infos, comment = archive.infolist(), archive.comment
        parts = {info.filename: archive.read(info) for info in infos}
    layers = ('<draw:layer-set><draw:layer draw:name="private" draw:display="none"/>'
              '<draw:layer draw:name="public" draw:display="always"/>'
              '<draw:layer draw:name="screen" draw:display="screen"/>'
              '<draw:layer draw:name="printer" draw:display="printer"/></draw:layer-set>')
    parts["styles.xml"] = parts["styles.xml"].replace(b"<office:styles>", b"<office:styles>" + layers.encode())
    with ZipFile(source, "w") as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(info, parts[info.filename])
    return source
