import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: workspace
    required property var shell
    property alias sourceLanguage: translationPage.sourceLanguage
    property alias targetLanguage: translationPage.targetLanguage
    property alias domain: translationPage.domain
    readonly property var activeDocument: backend.workspaceDocuments.find(function(item) { return item.id === backend.workspaceDocumentId }) || ({})
    property string menuDocumentId: ""
    readonly property var menuDocument: backend.workspaceDocuments.find(function(item) { return item.id === workspace.menuDocumentId }) || ({})
    readonly property int menuQueueIndex: backend.documents.findIndex(function(item) { return item.id === workspace.menuDocumentId })
    readonly property int sidebarMinimum: 240
    readonly property int sidebarMaximum: Math.max(sidebarMinimum, Math.min(440, Math.floor(width - 648)))
    property int draggedSidebarWidth: -1
    readonly property int sidebarWidth: boundedSidebarWidth(draggedSidebarWidth >= 0 ? draggedSidebarWidth : backend.workspaceView.sidebarWidth)
    spacing: 0

    function boundedSidebarWidth(value) {
        return Math.max(sidebarMinimum, Math.min(sidebarMaximum, Math.round(value)))
    }

    function resizeSidebar(value) {
        backend.setWorkspaceView({sidebarWidth: boundedSidebarWidth(value)})
    }

    function showFileMenu(identifier) {
        menuDocumentId = identifier
        fileMenu.open()
    }

    PageControls.Panel {
        objectName: "workspaceSidebar"
        Layout.preferredWidth: workspace.sidebarWidth
        Layout.minimumWidth: workspace.sidebarWidth
        Layout.maximumWidth: workspace.sidebarWidth
        Layout.fillHeight: true
        color: Theme.surfaceMuted
        DropArea { anchors.fill: parent; enabled: backend.canAddDocuments && !shell.reviewLocked; onDropped: function(event) { if (event.hasUrls) backend.addFiles(event.urls) } }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 12; spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 4
                PageControls.Heading { text: qsTr("Сесія документів"); Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 14 }
                PageControls.Action {
                    objectName: "addDocumentsButton"; iconName: "add"; hint: qsTr("Додати файли"); Accessible.name: hint
                    subtle: true; implicitWidth: 32; implicitHeight: 32
                    enabled: backend.canAddDocuments && !shell.reviewLocked; onClicked: backend.pickDocuments()
                }
                PageControls.Action {
                    objectName: "workspaceSessionMenuButton"; iconName: "chevron-down"; hint: qsTr("Дії із сесією"); Accessible.name: hint
                    subtle: true; implicitWidth: 28; implicitHeight: 32; enabled: !shell.reviewLocked
                    onClicked: sessionMenu.open()
                    Menu {
                        id: sessionMenu; width: Math.min(shell.width - 40, 360)
                        MenuItem { text: qsTr("Папка перекладів…"); objectName: "outputFolderButton"; enabled: !backend.busy; onTriggered: backend.pickOutputFolder() }
                        MenuItem { text: qsTr("Відкрити папку перекладів"); enabled: !!backend.outputFolder; onTriggered: backend.openPath(backend.outputFolder) }
                        MenuSeparator { }
                        MenuItem { text: qsTr("Прибрати завершені"); objectName: "clearCompletedButton"; enabled: backend.hasCompletedDocuments; onTriggered: backend.clearCompleted() }
                    }
                }
            }
            PageControls.Caption { text: qsTr("Зберігається автоматично"); font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            PageControls.Divider { }
            ListView {
                id: documentList
                objectName: "workspaceDocumentList"
                Layout.fillWidth: true; Layout.fillHeight: true
                model: backend.workspaceDocumentModel; clip: true; spacing: 6
                enabled: !shell.reviewLocked
                currentIndex: backend.workspaceDocuments.findIndex(function(item) { return item.id === backend.workspaceDocumentId })
                onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                ScrollBar.vertical: PageControls.ReadingScrollBar { }
                Keys.onUpPressed: if (currentIndex > 0) backend.selectWorkspaceDocument(backend.workspaceDocuments[currentIndex - 1].id)
                Keys.onDownPressed: if (currentIndex + 1 < count) backend.selectWorkspaceDocument(backend.workspaceDocuments[currentIndex + 1].id)
                delegate: ItemDelegate {
                    id: documentRow
                    required property var modelData
                    objectName: "workspaceDocumentRow_" + modelData.id
                    width: ListView.view.width - 18
                    height: Math.max(60, contentItem.implicitHeight + 16)
                    highlighted: modelData.id === backend.workspaceDocumentId
                    leftPadding: 10; rightPadding: 10; topPadding: 8; bottomPadding: 8
                    Accessible.name: modelData.name + ", " + (modelData.statusText || "")
                    ToolTip.visible: hovered
                    ToolTip.delay: 700
                    ToolTip.text: modelData.name + "\n" + (modelData.history ? modelData.output || modelData.path : modelData.path || "")
                    onClicked: backend.selectWorkspaceDocument(modelData.id)
                    background: Rectangle {
                        radius: 7; color: documentRow.highlighted ? Theme.accentLight : documentRow.hovered ? Theme.hover : Theme.surface
                        border.color: documentRow.visualFocus || documentRow.highlighted ? Theme.accent : Theme.line
                        Rectangle { width: 3; radius: 1; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.leftMargin: 1; anchors.topMargin: 8; anchors.bottomMargin: 8; color: Theme.accent; visible: documentRow.highlighted }
                    }
                    contentItem: ColumnLayout {
                        spacing: 4
                        RowLayout {
                            Layout.fillWidth: true; spacing: 7
                            Icon { name: documentRow.modelData.history ? "history" : "document"; color: documentRow.highlighted ? Theme.accent : Theme.muted; width: 16; height: 16; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                            PageControls.LabelText {
                                objectName: "workspaceDocumentName_" + documentRow.modelData.id
                                text: documentRow.modelData.name; font.pixelSize: 12; font.weight: Font.DemiBold
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 5
                            Icon {
                                name: documentRow.modelData.status === "done" ? ((documentRow.modelData.warningCount || 0) > 0 ? "attention" : "check") : documentRow.modelData.status === "error" ? "attention" : "document"
                                color: documentRow.modelData.status === "error" ? Theme.danger : (documentRow.modelData.warningCount || 0) > 0 ? Theme.warning : documentRow.modelData.status === "done" ? Theme.success : Theme.muted
                                Layout.preferredWidth: 12; Layout.preferredHeight: 12
                            }
                            PageControls.Caption {
                                Layout.fillWidth: true; font.pixelSize: 11; elide: Text.ElideRight
                                text: documentRow.modelData.history ? shell.historyDate(documentRow.modelData.finishedAt || documentRow.modelData.startedAt) : documentRow.modelData.statusText || ""
                                color: documentRow.modelData.status === "error" ? Theme.danger : documentRow.modelData.status === "translating" ? Theme.accent : Theme.muted
                            }
                            PageControls.Caption { text: documentRow.modelData.format; font.pixelSize: 10; color: Theme.muted }
                        }
                        Rectangle {
                            Layout.fillWidth: true; height: 3; radius: 2; color: Theme.accentLine
                            visible: documentRow.modelData.status === "translating"
                            Rectangle { width: parent.width * Math.max(0, Math.min(100, documentRow.modelData.progress || 0)) / 100; height: 3; radius: 2; color: Theme.accent }
                        }
                    }
                }
                PageControls.Caption {
                    anchors.centerIn: parent; width: parent.width - 16; visible: documentList.count === 0
                    text: qsTr("Документи цієї сесії з’являться тут."); horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                }
            }
            PageControls.Caption {
                text: qsTr("Збережіть або скасуйте правку, щоб перейти до іншого документа.")
                visible: backend.reviewEditing; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.accent; font.pixelSize: 11
            }
            ColumnLayout {
                visible: backend.translating || backend.recognizingScan; Layout.fillWidth: true; spacing: 6
                PageControls.Divider { }
                PageControls.Caption { text: backend.recognizingScan ? backend.scanMessage : backend.queueSummary; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                RowLayout {
                    visible: backend.workspaceMode === "review"; Layout.fillWidth: true
                    PageControls.Action { text: backend.paused ? qsTr("Продовжити") : qsTr("Пауза"); iconName: backend.paused ? "play" : "pause"; subtle: true; implicitHeight: 32; Layout.fillWidth: true; enabled: backend.translating && !shell.preparingDocuments; onClicked: backend.togglePause() }
                    PageControls.Action { text: ""; hint: qsTr("Стоп"); Accessible.name: hint; iconName: "stop"; subtle: true; implicitWidth: 32; implicitHeight: 32; onClicked: backend.cancel() }
                }
            }
        }
    }

    Item {
        id: sidebarDivider; objectName: "workspaceSidebarDivider"
        readonly property string resizeHint: qsTr("Перетягніть роздільник або змініть ширину стрілками ліворуч і праворуч.")
        Layout.preferredWidth: 16; Layout.minimumWidth: 16; Layout.maximumWidth: 16
        Layout.fillHeight: true; activeFocusOnTab: true
        Accessible.role: Accessible.Separator
        Accessible.name: qsTr("Ширина панелі документів")
        Accessible.description: resizeHint
        Keys.onLeftPressed: workspace.resizeSidebar(workspace.sidebarWidth - 10)
        Keys.onRightPressed: workspace.resizeSidebar(workspace.sidebarWidth + 10)
        Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Home || event.key === Qt.Key_End) {
                workspace.resizeSidebar(event.key === Qt.Key_Home ? workspace.sidebarMinimum : workspace.sidebarMaximum)
                event.accepted = true
            }
        }
        ToolTip.visible: resizePointer.containsMouse || activeFocus
        ToolTip.delay: 700
        ToolTip.text: resizeHint
        Rectangle {
            anchors.centerIn: parent; height: parent.height - 24
            width: sidebarDivider.activeFocus || resizePointer.containsMouse ? 2 : 1
            radius: 1; color: sidebarDivider.activeFocus || resizePointer.containsMouse ? Theme.accent : Theme.line
        }
        MouseArea {
            id: resizePointer; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SplitHCursor
            property real startPosition: 0
            property int startWidth: 0
            onPressed: function(mouse) {
                sidebarDivider.forceActiveFocus()
                startPosition = mapToItem(workspace, mouse.x, mouse.y).x
                startWidth = workspace.sidebarWidth
            }
            onPositionChanged: function(mouse) {
                if (pressed) workspace.draggedSidebarWidth = workspace.boundedSidebarWidth(startWidth + mapToItem(workspace, mouse.x, mouse.y).x - startPosition)
            }
            onReleased: {
                if (workspace.draggedSidebarWidth >= 0) workspace.resizeSidebar(workspace.draggedSidebarWidth)
                workspace.draggedSidebarWidth = -1
            }
            onCanceled: workspace.draggedSidebarWidth = -1
        }
    }

    ColumnLayout {
        objectName: "workspaceContent"
        Layout.fillWidth: true; Layout.fillHeight: true
        // Allocate the remaining width without feeding wrapped text back into it.
        Layout.minimumWidth: 0; Layout.preferredWidth: 1; spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 4
                PageControls.Heading {
                    id: documentTitle; objectName: "workspaceDocumentTitle"
                    text: workspace.activeDocument.name || qsTr("Переклад документів")
                    Layout.fillWidth: true; Layout.minimumWidth: 0; font.pixelSize: 21; elide: Text.ElideMiddle
                    HoverHandler { id: titleHover }
                    ToolTip.visible: titleHover.hovered && documentTitle.truncated
                    ToolTip.delay: 700; ToolTip.text: documentTitle.text
                }
                PageControls.Caption {
                    text: workspace.activeDocument.id ? (workspace.activeDocument.history ? qsTr("З історії перекладів") + " · " + shell.historyDate(workspace.activeDocument.finishedAt || workspace.activeDocument.startedAt) : workspace.activeDocument.statusText || "") : qsTr("Додайте файли, виберіть мови та почніть переклад.")
                    Layout.fillWidth: true; Layout.minimumWidth: 0; elide: Text.ElideRight
                }
            }
            TabBar {
                objectName: "workspaceModes"; implicitHeight: 40
                Layout.minimumWidth: implicitWidth; Layout.preferredWidth: implicitWidth; Layout.maximumWidth: implicitWidth
                enabled: !shell.reviewLocked
                currentIndex: backend.workspaceMode === "review" ? 1 : 0
                background: Rectangle { color: "transparent" }
                PageControls.NavigationTab { objectName: "translationModeTab"; text: qsTr("Переклад"); iconName: "translate"; implicitHeight: 40; onClicked: backend.setWorkspaceMode("translation") }
                PageControls.NavigationTab { objectName: "reviewTab"; text: qsTr("Перевірка"); iconName: "review"; implicitHeight: 40; onClicked: backend.setWorkspaceMode("review") }
            }
            PageControls.Action {
                objectName: "workspaceFileActionsButton"; text: qsTr("Дії"); iconName: "chevron-down"; subtle: true; implicitHeight: 34
                Layout.minimumWidth: implicitWidth
                visible: !!workspace.activeDocument.id; enabled: !shell.reviewLocked
                onClicked: workspace.showFileMenu(backend.workspaceDocumentId)
                Menu {
                    id: fileMenu; objectName: "workspaceFileMenu"; width: Math.min(shell.width - 40, 360)
                    MenuItem { text: qsTr("Відкрити оригінал"); objectName: "reviewOriginalButton"; enabled: !!workspace.menuDocument.path; onTriggered: backend.openPath(workspace.menuDocument.path) }
                    MenuItem { text: qsTr("Відкрити переклад"); objectName: "reviewOutputButton"; enabled: !!workspace.menuDocument.output && workspace.menuDocument.outputExists !== false; onTriggered: backend.openPath(workspace.menuDocument.output) }
                    MenuItem { text: qsTr("Відкрити звіт"); objectName: "reviewReportButton"; enabled: !!workspace.menuDocument.report || !!workspace.menuDocument.diagnosticReport; onTriggered: backend.openWorkspaceReport(workspace.menuDocumentId) }
                    MenuSeparator { visible: !workspace.menuDocument.history }
                    MenuItem { text: qsTr("Перекласти раніше"); objectName: "moveUpButton"; visible: !workspace.menuDocument.history; enabled: !!workspace.menuDocument.canMove && shell.canMoveQueue(workspace.menuQueueIndex, -1); onTriggered: backend.moveDocument(workspace.menuDocumentId, -1) }
                    MenuItem { text: qsTr("Перекласти пізніше"); objectName: "moveDownButton"; visible: !workspace.menuDocument.history; enabled: !!workspace.menuDocument.canMove && shell.canMoveQueue(workspace.menuQueueIndex, 1); onTriggered: backend.moveDocument(workspace.menuDocumentId, 1) }
                    MenuSeparator { }
                    MenuItem { text: qsTr("Прибрати із сесії"); objectName: "removeDocumentButton"; enabled: !!workspace.menuDocument.canRemove; onTriggered: backend.closeWorkspaceDocument(workspace.menuDocumentId) }
                }
            }
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: backend.workspaceMode === "review" ? 1 : 0
            DocumentsPage { id: translationPage; shell: workspace.shell }
            ReviewPage { shell: workspace.shell }
        }
    }
}
