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
    readonly property var snapshot: backend.previewState.documentId === documentId && backend.previewState.page === currentPage && backend.previewState.mode === mode ? backend.previewState : ({})
    readonly property var sourceState: snapshot.source || ({})
    readonly property var translationState: snapshot.translation || ({})
    property int knownSourcePages: 0
    property int knownTranslationPages: 0
    readonly property int pageCount: mode === "original" ? knownSourcePages : mode === "translation" ? knownTranslationPages : Math.max(knownSourcePages, knownTranslationPages)
    readonly property real zoom: [1, 1, 1.25, 1.5, 2][zoomSelector.currentIndex]
    readonly property bool fitPage: zoomSelector.currentIndex === 1
    readonly property real paneWidth: Math.max(80, (width - (mode === "paired" ? 12 : 0)) / (mode === "paired" ? 2 : 1) - 24)
    property int currentPage: 1
    property bool syncingScroll: false

    function updatePageCounts() {
        var state = backend.previewState
        if (state.documentId !== documentId || state.mode !== mode) return
        if (state.source.status !== "loading" && state.source.status !== "idle") knownSourcePages = state.source.pageCount || 0
        if (state.translation.status !== "loading" && state.translation.status !== "idle") knownTranslationPages = state.translation.pageCount || 0
    }
    function request() {
        if (active && documentId)
            backend.requestDocumentPreview(mode, currentPage, Math.min(2400, Math.max(240, Math.round(paneWidth * zoom * 1.5))))
    }
    function goToPage(value) {
        currentPage = Math.max(1, Math.min(Math.max(1, pageCount), value))
        pageNumber.text = String(currentPage)
    }
    function resetScroll() {
        if (sourcePane) sourcePane.resetScroll()
        if (translationPane) translationPane.resetScroll()
    }
    function syncScroll(fromSource, fraction) {
        if (syncingScroll || mode !== "paired") return
        syncingScroll = true
        if (fromSource) translationPane.setScrollFraction(fraction)
        else sourcePane.setScrollFraction(fraction)
        syncingScroll = false
    }
    onDocumentIdChanged: { knownSourcePages = 0; knownTranslationPages = 0; currentPage = 1; resetScroll(); updatePageCounts(); requestTimer.restart() }
    onModeChanged: { currentPage = 1; resetScroll(); requestTimer.restart() }
    onActiveChanged: if (active) requestTimer.restart()
    onCurrentPageChanged: { if (pageNumber) pageNumber.text = String(currentPage); resetScroll(); requestTimer.restart() }
    onPaneWidthChanged: requestTimer.restart()
    onZoomChanged: requestTimer.restart()
    Component.onCompleted: { updatePageCounts(); requestTimer.restart() }
    Connections {
        target: backend
        function onChanged() { if (preview.active && !requestTimer.running) requestTimer.start() }
        function onPreviewChanged() { preview.updatePageCounts() }
    }
    // Resizing a window or a sidebar only renders the final requested size.
    Timer { id: requestTimer; interval: 160; onTriggered: preview.request() }

    ColumnLayout {
        anchors.fill: parent; spacing: 8
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            PageControls.Select {
                id: modeSelector; objectName: "documentPreviewMode"
                model: preview.modes; optionLabels: preview.modeLabels
                currentIndex: Math.max(0, model.indexOf(preview.mode))
                Layout.preferredWidth: Math.min(210, preview.width * 0.32)
                implicitHeight: 34
                onActivated: function(index) { backend.setWorkspaceView({previewMode: model[index]}) }
                Accessible.name: qsTr("Режим перегляду документа")
            }
            Item { Layout.fillWidth: true }
            PageControls.Action {
                objectName: "previewPreviousPageButton"; iconName: "chevron-left"; text: ""
                implicitWidth: 30; implicitHeight: 34; subtle: true
                hint: qsTr("Попередня сторінка"); Accessible.name: hint
                enabled: preview.currentPage > 1
                onClicked: preview.goToPage(preview.currentPage - 1)
            }
            PageControls.Field {
                id: pageNumber; objectName: "documentPreviewPageField"
                Layout.preferredWidth: 48; implicitHeight: 34
                text: String(preview.currentPage); horizontalAlignment: TextInput.AlignHCenter
                leftPadding: 4; rightPadding: 4; selectByMouse: true
                enabled: preview.pageCount > 0
                validator: IntValidator { bottom: 1; top: Math.max(1, preview.pageCount) }
                onEditingFinished: {
                    if (preview.pageCount > 0) preview.goToPage(parseInt(text) || preview.currentPage)
                    else text = String(preview.currentPage)
                }
                Accessible.name: qsTr("Номер сторінки")
            }
            PageControls.Caption { text: "/ " + (preview.pageCount || "—"); font.pixelSize: 12 }
            PageControls.Action {
                objectName: "previewNextPageButton"; iconName: "chevron-right"; text: ""
                implicitWidth: 30; implicitHeight: 34; subtle: true
                hint: qsTr("Наступна сторінка"); Accessible.name: hint
                enabled: preview.currentPage < preview.pageCount
                onClicked: preview.goToPage(preview.currentPage + 1)
            }
            PageControls.Select {
                id: zoomSelector; objectName: "documentPreviewZoom"
                model: ["fit", "page", "125", "150", "200"]
                optionLabels: [qsTr("За шириною"), qsTr("Сторінка цілком"), "125%", "150%", "200%"]
                Layout.preferredWidth: Math.min(148, preview.width * 0.25); implicitHeight: 34
                Accessible.name: qsTr("Масштаб відносно ширини сторінки")
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            PreviewPagePane {
                id: sourcePane; objectName: "documentOriginalPreview"
                visible: preview.mode !== "translation"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0
                heading: qsTr("Оригінал"); snapshot: preview.sourceState
                page: preview.currentPage; zoom: preview.zoom; fitPage: preview.fitPage
                filePath: preview.document.path || ""
                onScrollFractionChanged: function(fraction) { preview.syncScroll(true, fraction) }
            }
            PreviewPagePane {
                id: translationPane; objectName: "documentTranslationPreview"
                visible: preview.mode !== "original"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0
                heading: qsTr("Переклад"); snapshot: preview.translationState
                page: preview.currentPage; zoom: preview.zoom; fitPage: preview.fitPage
                filePath: preview.document.output || ""
                onScrollFractionChanged: function(fraction) { preview.syncScroll(false, fraction) }
            }
        }
        PageControls.Caption {
            visible: preview.document.format === "DOCX" && (preview.sourceState.status === "ready" || preview.translationState.status === "ready")
            text: qsTr("Вигляд DOCX може дещо відрізнятися від вигляду у Word.")
            Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11
        }
    }
}
