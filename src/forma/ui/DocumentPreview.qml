import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: preview
    required property var document
    property bool active: false
    readonly property string documentId: document.id || ""
    readonly property string mode: backend.workspaceView.previewMode || "original"
    readonly property var modes: ["original", "translation", "paired"]
    readonly property var modeLabels: [qsTr("Оригінал"), qsTr("Переклад"), qsTr("Поруч")]
    readonly property var snapshot: backend.previewState.documentId === documentId && backend.previewState.mode === mode ? backend.previewState : ({})
    readonly property int pageCount: snapshot.pageCount || 0
    readonly property var sourceState: snapshot.source || ({})
    readonly property var translationState: snapshot.translation || ({})
    readonly property int columns: mode === "paired" ? 2 : 1
    readonly property real zoom: [1, 1, 1.25, 1.5, 2][zoomSelector.currentIndex]
    readonly property bool fitPage: zoomSelector.currentIndex === 1
    readonly property real paneWidth: Math.max(64, (pageList.width - 32 - (columns - 1) * 16) / columns)
    readonly property int renderWidth: Math.min(2400, Math.max(256, Math.round(paneWidth * zoom * 1.5)))
    property int currentPage: 1
    property real pageFraction: 0
    property bool resetting: false

    function openPreview() {
        if (active && documentId) backend.openDocumentPreview(mode, renderWidth)
    }
    function resetPosition() {
        resetting = true
        currentPage = 1
        pageFraction = 0
        if (pageList) { pageList.positionViewAtBeginning(); pageList.contentX = 0 }
        resetting = false
        if (openTimer) openTimer.restart()
    }
    function resizePages() {
        var selectedDocument = documentId, selectedPage = currentPage, fraction = pageFraction
        if (openTimer) openTimer.restart()
        Qt.callLater(function() {
            if (selectedDocument !== documentId || !pageCount) return
            resetting = true
            pageList.positionViewAtIndex(selectedPage - 1, ListView.Beginning)
            var row = pageList.itemAtIndex(selectedPage - 1)
            if (row) pageList.contentY = Math.max(pageList.originY, Math.min(pageList.originY + pageList.contentHeight - pageList.height, row.y + row.height * fraction))
            resetting = false
            viewportTimer.restart()
        })
    }
    function visibleRange() {
        var first = -1, last = -1
        for (var y = 2; y < pageList.height; y += Math.max(16, pageList.height / 12)) {
            var index = pageList.indexAt(1, pageList.contentY + y)
            if (index >= 0) { if (first < 0) first = index; last = index }
        }
        if (first < 0) first = Math.max(0, currentPage - 1)
        if (last < first) last = first
        return [first, last]
    }
    function updateViewport() {
        if (!active || !pageCount || resetting || snapshot.width !== renderWidth) return
        var range = visibleRange()
        currentPage = Math.min(pageCount, range[0] + 1)
        var row = pageList.itemAtIndex(currentPage - 1)
        if (row && row.height > 0) pageFraction = Math.max(0, Math.min(1, (pageList.contentY - row.y) / row.height))
        var first = Math.max(1, range[0])
        var last = Math.min(pageCount, Math.max(first, range[1] + 2), first + 5)
        backend.requestDocumentPreviewRange(mode, first, last, renderWidth)
    }
    function goToPage(value) {
        if (!pageCount) return
        var target = Math.max(1, Math.min(pageCount, value))
        pageList.positionViewAtIndex(target - 1, ListView.Beginning)
        currentPage = target
        pageNumber.text = String(target)
        viewportTimer.restart()
    }
    onDocumentIdChanged: resetPosition()
    onModeChanged: resetPosition()
    onActiveChanged: if (active) openTimer.restart()
    onRenderWidthChanged: resizePages()
    onFitPageChanged: resizePages()
    onCurrentPageChanged: if (pageNumber && !pageNumber.activeFocus) pageNumber.text = String(currentPage)
    onPageCountChanged: viewportTimer.restart()
    Component.onCompleted: openTimer.restart()
    Connections {
        target: backend
        function onChanged() { if (preview.active && !openTimer.running) openTimer.start() }
        function onPreviewChanged() { if (preview.active && !viewportTimer.running) viewportTimer.start() }
    }
    Timer { id: openTimer; interval: 160; onTriggered: { preview.openPreview(); viewportTimer.restart() } }
    Timer { id: viewportTimer; interval: 70; onTriggered: preview.updateViewport() }

    ColumnLayout {
        anchors.fill: parent; spacing: 8
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            PageControls.Select {
                objectName: "documentPreviewMode"
                model: preview.modes; optionLabels: preview.modeLabels
                currentIndex: Math.max(0, model.indexOf(preview.mode))
                Layout.preferredWidth: Math.min(200, preview.width * 0.32); implicitHeight: 34
                onActivated: function(index) { backend.setWorkspaceView({previewMode: model[index]}) }
                Accessible.name: qsTr("Режим перегляду документа")
            }
            Item { Layout.fillWidth: true }
            PageControls.Field {
                id: pageNumber; objectName: "documentPreviewPageField"
                Layout.preferredWidth: 48; implicitHeight: 34
                text: String(preview.currentPage); horizontalAlignment: TextInput.AlignHCenter
                leftPadding: 4; rightPadding: 4; selectByMouse: true; enabled: preview.pageCount > 0
                validator: IntValidator { bottom: 1; top: Math.max(1, preview.pageCount) }
                onEditingFinished: {
                    if (preview.pageCount > 0) preview.goToPage(parseInt(text) || preview.currentPage)
                    else text = String(preview.currentPage)
                }
                Accessible.name: qsTr("Перейти до сторінки")
            }
            PageControls.Caption { text: "/ " + (preview.pageCount || "—"); font.pixelSize: 12 }
            PageControls.Select {
                id: zoomSelector; objectName: "documentPreviewZoom"
                model: ["fit", "page", "125", "150", "200"]
                optionLabels: [qsTr("За шириною"), qsTr("Сторінка цілком"), "125%", "150%", "200%"]
                Layout.preferredWidth: Math.min(160, preview.width * 0.3); implicitHeight: 34
                Accessible.name: qsTr("Масштаб відносно ширини сторінки")
            }
            Icon {
                visible: ["DOCX", "PPTX", "ODT", "ODP"].indexOf(preview.document.format) >= 0; name: "info"; color: Theme.muted
                Layout.preferredWidth: 16; Layout.preferredHeight: 16
                Accessible.name: ["ODT", "ODP"].indexOf(preview.document.format) >= 0 ? qsTr("Перегляд LibreOffice може відрізнятися від оригіналу; активний і вбудований вміст у ньому приховано.") : preview.document.format === "PPTX" ? qsTr("Вигляд PPTX може дещо відрізнятися від вигляду у PowerPoint.") : qsTr("Вигляд DOCX може дещо відрізнятися від вигляду у Word.")
                HoverHandler { id: docxHint }
                ToolTip.visible: docxHint.hovered; ToolTip.text: Accessible.name; ToolTip.delay: 400
            }
        }
        RowLayout {
            visible: preview.pageCount > 0; Layout.fillWidth: true; spacing: 16
            PageControls.Caption { text: qsTr("Оригінал") + " · " + (preview.sourceState.pageCount || 0); visible: preview.mode !== "translation"; Layout.fillWidth: true; Layout.preferredWidth: 1 }
            PageControls.Caption { text: qsTr("Переклад") + " · " + (preview.translationState.pageCount || 0); visible: preview.mode !== "original"; Layout.fillWidth: true; Layout.preferredWidth: 1 }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; color: Theme.surfaceMuted; radius: 6; border.color: Theme.line
            ListView {
                id: pageList; objectName: "documentContinuousPreview"
                anchors.fill: parent; anchors.margins: 1
                visible: preview.pageCount > 0; model: backend.previewPageModel
                clip: true; spacing: 16; topMargin: 12; bottomMargin: 12
                boundsBehavior: Flickable.StopAtBounds; flickableDirection: Flickable.AutoFlickIfNeeded
                cacheBuffer: Math.max(0, height * 0.6)
                contentWidth: Math.max(width, preview.paneWidth * preview.zoom * preview.columns + (preview.columns - 1) * 16 + 32)
                onContentYChanged: if (!preview.resetting) viewportTimer.restart()
                onHeightChanged: viewportTimer.restart()
                onCountChanged: viewportTimer.restart()
                ScrollBar.vertical: PageControls.ReadingScrollBar { }
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_PageDown || event.key === Qt.Key_PageUp) {
                        contentY = Math.max(originY, Math.min(originY + contentHeight - height, contentY + (event.key === Qt.Key_PageDown ? 1 : -1) * height * 0.9))
                        event.accepted = true
                    }
                }
                delegate: Item {
                    id: pageRow
                    required property var modelData
                    required property int index
                    objectName: "documentPreviewPage_" + modelData.page
                    width: pageList.contentWidth
                    readonly property real sourceRatio: modelData.source.width > 0 ? modelData.source.height / modelData.source.width : 0
                    readonly property real translationRatio: modelData.translation.width > 0 ? modelData.translation.height / modelData.translation.width : 0
                    readonly property real ratio: Math.max(0.1, preview.mode === "original" ? sourceRatio : preview.mode === "translation" ? translationRatio : Math.max(sourceRatio, translationRatio))
                    readonly property real paperWidth: Math.max(40, Math.min(preview.paneWidth * preview.zoom, preview.fitPage ? Math.max(100, pageList.height - 46) / ratio : preview.paneWidth * preview.zoom))
                    readonly property bool inViewport: y + height > pageList.contentY && y < pageList.contentY + pageList.height
                    height: paperWidth * ratio + 22
                    PageControls.Caption { x: 12; y: 0; text: qsTr("Сторінка %1").arg(pageRow.modelData.page); font.pixelSize: 11 }
                    Row {
                        x: 12; y: 22; spacing: 16; height: parent.height - 22
                        PreviewPagePane {
                            objectName: "documentOriginalPreview"
                            visible: preview.mode !== "translation"
                            width: preview.paneWidth * preview.zoom; height: parent.height
                            paperWidth: pageRow.paperWidth; inViewport: pageRow.inViewport
                            heading: qsTr("Оригінал"); snapshot: pageRow.modelData.source
                            page: pageRow.modelData.page; filePath: preview.document.path || ""
                        }
                        PreviewPagePane {
                            objectName: "documentTranslationPreview"
                            visible: preview.mode !== "original"
                            width: preview.paneWidth * preview.zoom; height: parent.height
                            paperWidth: pageRow.paperWidth; inViewport: pageRow.inViewport
                            heading: qsTr("Переклад"); snapshot: pageRow.modelData.translation
                            page: pageRow.modelData.page; filePath: preview.document.output || ""
                        }
                    }
                }
            }
            ColumnLayout {
                anchors.centerIn: parent; width: Math.max(0, Math.min(480, parent.width - 32))
                visible: preview.pageCount === 0; spacing: 12
                BusyIndicator { running: !!preview.snapshot.busy || !preview.snapshot.documentId; visible: running; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                PageControls.Caption {
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                    text: (preview.mode === "translation" ? preview.translationState.message : preview.sourceState.message) || preview.translationState.message || qsTr("Готуємо перегляд…")
                }
            }
        }
    }
}
