import QtQuick

Item {
    id: root
    property string name: "document"
    property color color: "#182230"
    implicitWidth: 18
    implicitHeight: 18
    width: implicitWidth
    height: implicitHeight
    // Small outline symbols kept as editable vector geometry, independent of fonts.
    readonly property var drawings: ({
        "document": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
        "review": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
        "settings": '<path d="M4 6h5m4 0h7M4 12h10m4 0h2M4 18h2m4 0h10"/><circle cx="11" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
        "add": '<path d="M12 5v14M5 12h14"/>',
        "folder": '<path d="M3 8V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v2M3 8h17a1 1 0 0 1 1 1l-2 10H4L2 9a1 1 0 0 1 1-1Z"/>',
        "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
        "eye-off": '<path d="m3 3 18 18M10 5a12 12 0 0 1 2 0c6 0 10 7 10 7a21 21 0 0 1-3 4M6 6a23 23 0 0 0-4 6s4 7 10 7a13 13 0 0 0 5-1M10 10a3 3 0 0 0 4 4"/>',
        "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
        "close": '<path d="m6 6 12 12M18 6 6 18"/>',
        "chevron-down": '<path d="m6 9 6 6 6-6"/>',
        "chevron-right": '<path d="m9 6 6 6-6 6"/>',
        "chevron-left": '<path d="m15 6-6 6 6 6"/>',
        "attention": '<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3v.01"/>',
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
        "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
        "back": '<path d="M20 12H4m6-6-6 6 6 6"/>',
        "external": '<path d="M14 3h7v7m0-7L11 13M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
        "translate": '<path d="M3 5h11M8 3v2M5 5c0 5 3 8 8 10M12 5c0 5-3 8-8 10M13 21l4-10 4 10M15 17h4"/>',
        "play": '<path d="m7 4 13 8-13 8Z"/>',
        "pause": '<path d="M7 4h3v16H7zM14 4h3v16h-3z"/>',
        "stop": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
        "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
        "connect": '<path d="m8 3 3 3m2-3 3 3M7 7l10 10M8 8l-2 2a5 5 0 0 0 7 7l2-2M6 18l-3 3m8-14 3-3 6 6-3 3"/>',
        "book": '<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1ZM12 5v15"/>',
        "undo": '<path d="M3 10h11a6 6 0 0 1 0 12M3 10l5-5M3 10l5 5"/>',
        "check": '<path d="m5 12 4 4L19 6"/>',
        "edit": '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z"/>',
        "history": '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',
        "refresh": '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-6v6h6M4 16a8 8 0 0 0 14 3l3-3m0 6v-6h-6"/>',
        "arrow-up": '<path d="M12 20V4m-6 6 6-6 6 6"/>',
        "arrow-down": '<path d="M12 4v16m-6-6 6 6 6-6"/>',
        "clear-done": '<path d="M3 5h12M3 11h8M3 17h7m4-2 3 3 5-6"/>',
        "import": '<path d="M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6"/>'
    })
    Image {
        anchors.fill: parent
        sourceSize.width: Math.ceil(root.width * 2)
        sourceSize.height: Math.ceil(root.height * 2)
        fillMode: Image.PreserveAspectFit
        smooth: true
        source: "data:image/svg+xml;utf8," + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="'
            + root.color + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            + (root.drawings[root.name] || root.drawings.document) + '</svg>')
    }
}
