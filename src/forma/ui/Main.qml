import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1220
    height: 820
    minimumWidth: 960
    minimumHeight: 680
    visible: true
    title: qsTr("Forma — перекладач")
    color: bg
    font.pixelSize: 14
    font.family: Theme.fontFamily
    property color bg: Theme.canvas
    property color ink: Theme.ink
    property color muted: Theme.muted
    property color line: Theme.line
    property color accent: Theme.accent
    property color accentLight: Theme.accentLight
    palette.window: Theme.canvas
    palette.windowText: Theme.ink
    palette.base: Theme.surface
    palette.alternateBase: Theme.surfaceMuted
    palette.text: Theme.ink
    palette.button: Theme.surface
    palette.buttonText: Theme.ink
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.accentText
    palette.mid: Theme.line
    palette.dark: Theme.lineStrong
    palette.light: Theme.surfaceRaised
    palette.toolTipBase: Theme.ink
    palette.toolTipText: Theme.canvas
    Binding { target: Theme; property: "preference"; value: typeof backend.uiTheme === "string" ? backend.uiTheme : "system" }
    property int page: 0
    onPageChanged: {
        if (page === 1) backend.activateReview()
        if (page === 2) backend.refreshWorkerAvailability()
        if (page === 1 || page === 3) backend.refreshFileAvailability()
    }
    property bool translationOptionsOpen: false
    property bool previewOpen: false
    property bool connectionOptionsOpen: false
    property bool pdfOptionsOpen: false
    property bool pdfPreparationOptionsOpen: false
    property bool performanceOptionsOpen: false
    property bool removeSavedKey: false
    property bool keyVisible: false
    property string providerPresetId: "custom"
    readonly property var activeProviderPreset: backend.providerPresets.find(function(item) { return item.id === root.providerPresetId }) || ({})
    readonly property bool nativeProvider: apiMode.currentText === "gemini" || apiMode.currentText === "anthropic"
    readonly property bool savedEndpointSelected: baseUrl.text.trim().replace(/\/+$/, "") === (backend.settings.baseUrl || "").trim().replace(/\/+$/, "")
    property double clockNow: Date.now()
    readonly property var translationCandidates: {
        var files = backend.documents
        if (backend.translating)
            return files.filter(function(d) { return d.status === "queued" || d.status === "translating" })
        var pending = files.filter(function(d) { return ["ready", "error", "cancelled"].indexOf(d.status) >= 0 })
        return pending.length > 0 ? pending : files.length > 0 && files.every(function(d) { return d.status === "done" }) ? files : []
    }
    readonly property bool hasPdf: translationCandidates.some(function(d) { return d.format === "PDF" })
    readonly property bool needsSourceLanguage: hasPdf && sourceLanguage.currentIndex === 0
    readonly property bool sameLanguage: sourceCodes[sourceLanguage.currentIndex] === targetCodes[targetLanguage.currentIndex]
    property var sourceCodes: ["auto", "en", "uk", "de", "pl", "fr", "es", "it", "pt", "zh", "ja"]
    property var targetCodes: ["en", "uk", "de", "pl", "fr", "es", "it", "pt", "zh", "ja"]
    property var languageLabels: [qsTr("Англійська"), qsTr("Українська"), qsTr("Німецька"), qsTr("Польська"), qsTr("Французька"), qsTr("Іспанська"), qsTr("Італійська"), qsTr("Португальська"), qsTr("Китайська"), qsTr("Японська")]
    property var reasoningCodes: ["", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
    property var reasoningLabels: [qsTr("За замовчуванням"), qsTr("Вимкнено"), qsTr("Мінімальний"), qsTr("Низький"), qsTr("Середній"), qsTr("Високий"), qsTr("Дуже високий"), qsTr("Максимальний")]
    // Canonical domain values are prompt inputs, independent of translated labels.
    property var domainValues: ["Загальна тематика", "Технічна документація", "Ділові документи", "Наукові матеріали", "Юридичні документи"]
    property var domainLabels: [qsTr("Загальна тематика"), qsTr("Технічна документація"), qsTr("Ділові документи"), qsTr("Наукові матеріали"), qsTr("Юридичні документи")]
    property var uiLanguageCodes: backend.uiLanguages.map(function(item) { return item.code })
    property var titles: [qsTr("Переклад документів"), qsTr("Перевірка документа"), qsTr("Налаштування"), qsTr("Історія перекладів"), qsTr("Переклад тексту та фото")]
    readonly property var navigationPages: [0, 4, 1, 3, 2]
    readonly property bool hasCompleted: backend.documents.some(function(d) { return d.status === "done" })
    readonly property int reviewEntryIndex: backend.reviewEntries.findIndex(function(entry) { return entry.id === backend.selectedReviewId })
    readonly property bool reviewLocked: backend.reviewEditing || backend.reviewSaving || backend.memoryWorking
    property string languageMemoryProjectId: ""
    property var themeCodes: ["system", "light", "dark"]
    property var themeLabels: [qsTr("Як у системі"), qsTr("Світла"), qsTr("Темна")]
    property var scanProviderCodes: ["auto", "forma", "abbyy", "abbyy_extended"]
    property var scanProviderLabels: [qsTr("Автоматично"), qsTr("Вбудований / локальний OCR"), qsTr("ABBYY FineReader — вручну"), qsTr("ABBYY Extended CLI")]
    readonly property bool showLocalOcrSettings: scanProvider.currentIndex === 1 || (scanProvider.currentIndex === 0 && (backend.settings.scanConverterReady || !backend.settings.abbyyReady))
    readonly property bool manualAbbyy: backend.settings.scanProviderEffective === "abbyy" || backend.settings.scanProvider === "abbyy"
    readonly property var currentAbbyyHandoff: typeof backend.abbyyHandoff === "object" && backend.abbyyHandoff ? backend.abbyyHandoff : ({})
    readonly property bool preparingDocuments: backend.recognizingScan || backend.translationPreparing
    readonly property bool canImportAbbyyResult: !backend.busy || (!!currentAbbyyHandoff.continueTranslation && !currentAbbyyHandoff.importing)
    property string abbyyPromptId: ""

    onClosing: function(close) { close.accepted = backend.requestClose() }
    Component.onCompleted: { loadConfig(); syncMemoryLanguages() }
    Shortcut { sequences: [StandardKey.Open]; enabled: root.page !== 4 && backend.canAddDocuments && !root.reviewLocked; onActivated: backend.pickDocuments() }
    Timer { interval: 1000; repeat: true; running: backend.translating; onTriggered: root.clockNow = Date.now() }

    component LabelText: Text { font.family: Theme.fontFamily;
        color: root.ink
        font.pixelSize: 14
        textFormat: Text.PlainText
        renderType: Text.NativeRendering
    }
    component Caption: LabelText { color: root.muted; font.pixelSize: 12 }
    component Heading: LabelText { font.pixelSize: 16; font.weight: Font.Medium }
    component Panel: Rectangle { color: Theme.surface; border.color: root.line; radius: 12 }
    component Divider: Rectangle { color: root.line; implicitHeight: 1; Layout.fillWidth: true }
    component ReadingScrollBar: ScrollBar {
        id: control
        policy: ScrollBar.AsNeeded; hoverEnabled: true; minimumSize: 0.08
        orientation: Qt.Vertical; visible: size > 0 && size < 1
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 8; implicitWidth: 8; padding: 1
        contentItem: Rectangle { implicitWidth: 4; implicitHeight: 28; radius: 2; color: control.pressed ? Theme.lineStrong : Theme.scrollbar; opacity: control.active || control.hovered ? 1 : 0.4 }
        background: null
    }
    component ReadOnlyText: TextArea {
        readOnly: true; selectByMouse: true; persistentSelection: true
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        color: root.ink; selectionColor: root.accent; selectedTextColor: Theme.accentText
        placeholderTextColor: root.muted; font.pixelSize: 14; padding: 0; rightPadding: 12
        background: null
    }
    component ReviewTextPanel: Panel {
        id: textPanel
        property string heading: ""
        property alias text: textContent.text
        property alias textObjectName: textContent.objectName
        property bool editing: false
        property bool edited: false
        property string editError: ""
        signal draftEdited(string value)
        onTextChanged: if (!editing && textScroll.contentItem) textScroll.contentItem.contentY = 0
        onEditingChanged: if (editing) Qt.callLater(function() { textContent.forceActiveFocus() })
        border.color: editing ? root.accent : root.line
        border.width: editing ? 2 : 1
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Caption { text: textPanel.heading; font.weight: Font.DemiBold; Layout.fillWidth: true }
                Caption { text: textPanel.editing ? qsTr("Редагування") : qsTr("Чернетка"); visible: textPanel.editing || textPanel.edited; color: root.accent; font.weight: Font.DemiBold }
            }
            Divider { }
            ScrollView {
                id: editErrorScroll
                visible: textPanel.editError.length > 0
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(64, editErrorText.implicitHeight)
                Layout.maximumHeight: 64; clip: true; contentWidth: availableWidth
                ScrollBar.vertical: ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ReadOnlyText { id: editErrorText; objectName: textPanel.textObjectName + "Error"; width: editErrorScroll.availableWidth; text: textPanel.editError; color: Theme.danger; font.pixelSize: 12; Accessible.name: qsTr("Помилка збереження правки") }
            }
            ScrollView {
                id: textScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                ScrollBar.vertical: ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ReadOnlyText {
                    id: textContent
                    width: textScroll.availableWidth
                    readOnly: !textPanel.editing
                    onTextChanged: if (textPanel.editing) textPanel.draftEdited(text)
                    placeholderText: textPanel.editing ? qsTr("Введіть переклад") : qsTr("Текст відсутній")
                    Accessible.name: textPanel.heading
                }
            }
        }
    }
    component Action: AppButton { }
    component NavigationTab: TabButton {
        id: control
        property string iconName: "document"
        implicitHeight: 46
        implicitWidth: tabContent.implicitWidth + 36
        width: implicitWidth
        contentItem: Item {
            implicitWidth: tabContent.implicitWidth
            implicitHeight: tabContent.implicitHeight
            Row {
                id: tabContent
                anchors.centerIn: parent; spacing: 9
                Icon { name: control.iconName; color: !control.enabled ? Theme.muted : control.checked ? root.accent : Theme.muted; width: 20; height: 20; anchors.verticalCenter: parent.verticalCenter }
                LabelText { text: control.text; font.pixelSize: 14; font.weight: Font.Medium; color: !control.enabled ? Theme.muted : control.checked ? root.accent : Theme.muted; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        background: Rectangle {
            color: control.hovered ? Theme.hover : "transparent"
            radius: 6
            border.color: control.visualFocus ? root.accent : "transparent"
            border.width: 2
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.leftMargin: 12; anchors.rightMargin: 12; height: 2; radius: 1; color: root.accent; visible: control.checked }
        }
    }
    component Field: AppField { }
    component Select: AppSelect { }
    component ModelPicker: Item {
        id: control
        property alias text: modelInput.text
        property alias placeholderText: modelInput.placeholderText
        implicitHeight: 42
        implicitWidth: 240
        Field {
            id: modelInput
            objectName: control.objectName + "Input"
            anchors.fill: parent; rightPadding: 42
            Accessible.name: control.Accessible.name
            Keys.onDownPressed: function(event) {
                if (backend.modelChoices.length > 0) {
                    modelPopup.open()
                    modelList.currentIndex = 0
                    modelList.forceActiveFocus()
                }
                event.accepted = true
            }
        }
        Action {
            objectName: control.objectName + "DropdownButton"
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: 4
            text: ""; iconName: "chevron-down"; hint: qsTr("Вибрати зі списку моделей"); subtle: true
            implicitWidth: 34; implicitHeight: 34; enabled: backend.modelChoices.length > 0
            Accessible.name: hint
            onClicked: modelPopup.visible ? modelPopup.close() : modelPopup.open()
        }
        Popup {
            id: modelPopup
            objectName: control.objectName + "Popup"
            y: control.height + 4; width: control.width; padding: 4
            topMargin: 8; bottomMargin: 8
            implicitHeight: Math.min(modelList.contentHeight + 8, 300)
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
            background: Rectangle { color: Theme.surface; radius: 7; border.color: Theme.lineStrong }
            onOpened: modelList.currentIndex = backend.modelChoices.indexOf(control.text)
            contentItem: ListView {
                id: modelList
                clip: true; model: backend.modelChoices; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }
                Keys.onReturnPressed: chooseCurrent()
                Keys.onEnterPressed: chooseCurrent()
                Keys.onEscapePressed: modelPopup.close()
                function chooseCurrent() {
                    if (currentIndex >= 0 && currentIndex < count) {
                        control.text = backend.modelChoices[currentIndex]
                        modelPopup.close()
                        modelInput.forceActiveFocus()
                    }
                }
                delegate: ItemDelegate {
                    id: modelOption
                    required property string modelData
                    required property int index
                    width: modelList.width; height: 38
                    highlighted: modelList.currentIndex === index
                    contentItem: LabelText { text: modelOption.modelData; font.pixelSize: 13; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; font.weight: control.text === modelOption.modelData ? Font.DemiBold : Font.Normal }
                    background: Rectangle { radius: 4; color: modelOption.highlighted || modelOption.hovered ? Theme.accentLight : Theme.surface }
                    onClicked: { modelList.currentIndex = index; modelList.chooseCurrent() }
                }
            }
        }
    }
    component Toggle: CheckBox {
        id: control
        implicitHeight: Math.max(30, contentItem.implicitHeight + topPadding + bottomPadding)
        Layout.fillWidth: true
        spacing: 10
        indicator: Rectangle {
            width: 18; height: 18
            x: control.leftPadding; y: (control.height - height) / 2
            color: control.checked ? root.accent : Theme.surface
            radius: 4
            border.color: control.visualFocus ? root.accent : control.checked ? root.accent : Theme.lineStrong
            border.width: control.visualFocus ? 2 : 1
            Icon { anchors.centerIn: parent; name: "check"; width: 14; height: 14; color: Theme.accentText; visible: control.checked }
        }
        contentItem: LabelText {
            text: control.text
            wrapMode: Text.WordWrap
            font.pixelSize: 13
            color: control.enabled ? root.ink : root.muted
            leftPadding: control.indicator.width + control.spacing
            verticalAlignment: Text.AlignVCenter
        }
    }

    Timer { interval: 5000; repeat: true; running: root.visible; onTriggered: backend.usage.refresh() }
    Timer {
        id: scanSettingsScrollTimer
        interval: 1
        onTriggered: {
            (root.showLocalOcrSettings ? scanConverterPath : scanProvider).forceActiveFocus()
            root.scrollToScanConverter()
        }
    }
    Connections {
        target: settingsScroll.contentItem
        function onContentHeightChanged() {
            if (scanConverterPath.activeFocus || scanProvider.activeFocus)
                Qt.callLater(root.scrollToScanConverter)
        }
    }
    Connections {
        target: backend
        function onChanged() {
            root.syncMemoryLanguages()
            if (root.currentAbbyyHandoff.showPrompt && root.currentAbbyyHandoff.promptId && root.currentAbbyyHandoff.promptId !== root.abbyyPromptId) {
                root.abbyyPromptId = root.currentAbbyyHandoff.promptId
                abbyyHandoffDialog.open()
            }
            if (!root.currentAbbyyHandoff.sourceId)
                abbyyHandoffDialog.close()
            if (backend.memoryConflict.active && !rememberWordingDialog.visible)
                rememberWordingDialog.open()
        }
    }
    InstructionsDialog { id: instructionsDialog; controller: backend }
    AbbyyHandoffDialog { id: abbyyHandoffDialog; controller: backend; handoff: root.currentAbbyyHandoff }
    function openGlossary() {
        backend.glossaryEditor.prepare(root.sourceCodes[sourceLanguage.currentIndex], root.targetCodes[targetLanguage.currentIndex], root.domainValues[domain.currentIndex])
        glossaryDialog.open()
    }
    GlossaryDialog { id: glossaryDialog; controller: backend.glossaryEditor }
    TranslationMemoryDialog { id: memoryDialog; controller: backend; sourceLanguageCode: root.sourceCodes[sourceLanguage.currentIndex]; targetLanguageCode: root.targetCodes[targetLanguage.currentIndex]; onEditGlossaryRequested: root.openGlossary() }
    RememberWordingDialog { id: rememberWordingDialog; controller: backend }
    UsagePanel { id: usagePanel; parent: Overlay.overlay; controller: backend.usage }

    header: Rectangle {
        height: 96; color: Theme.surface
        Rectangle { height: 1; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; color: root.line }
        RowLayout {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 28; anchors.rightMargin: 28; height: 50; spacing: 10
            Image { Layout.preferredWidth: 28; Layout.preferredHeight: 28; source: "forma.svg"; sourceSize.width: 56; sourceSize.height: 56; smooth: true }
            LabelText { text: "Forma"; font.pixelSize: 21; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            Caption {
                text: backend.reviewEditing ? qsTr("Редагування перекладу") : backend.recognizingScan ? backend.scanMessage : backend.paused ? qsTr("На паузі") : backend.translating ? backend.queueSummary : backend.busy ? qsTr("Виконуємо завдання…") : backend.settings.model || qsTr("Підключення не налаштовано")
                color: backend.busy ? root.accent : root.muted
                elide: Text.ElideMiddle; Layout.maximumWidth: 300
            }
            Action {
                objectName: "appearanceButton"; iconName: Theme.preference === "system" ? "monitor" : Theme.dark ? "moon" : "sun"
                hint: qsTr("Вигляд"); subtle: true; implicitWidth: 36; implicitHeight: 36
                onClicked: appearanceMenu.open()
                Menu {
                    id: appearanceMenu
                    y: parent.height + 6; width: 230
                    Repeater {
                        model: root.themeCodes
                        MenuItem {
                            required property string modelData
                            required property int index
                            text: root.themeLabels[index]; checkable: true
                            checked: Theme.preference === modelData
                            onTriggered: backend.setUiTheme(modelData)
                        }
                    }
                }
            }
            Action {
                objectName: "usageButton"
                text: qsTr("Витрати") + " · " + (backend.usage.summary.today ? backend.usage.summary.today.costLabel : "—") + (backend.usage.summary.month && backend.usage.summary.month.unknown > 0 ? " *" : "")
                hint: qsTr("Витрати API та баланс")
                subtle: true; implicitHeight: 36
                onClicked: usagePanel.open()
            }
        }
        TabBar {
            id: navigation
            objectName: "mainNavigation"
            anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.leftMargin: 24; anchors.bottomMargin: 1
            width: parent.width - 48; height: 42
            spacing: 4
            enabled: !root.reviewLocked
            currentIndex: root.navigationPages.indexOf(root.page)
            onCurrentIndexChanged: if (currentIndex >= 0 && root.page !== root.navigationPages[currentIndex]) root.page = root.navigationPages[currentIndex]
            background: Rectangle { color: "transparent" }
            NavigationTab { text: qsTr("Документи"); iconName: "translate"; objectName: "translationTab" }
            NavigationTab { text: qsTr("Текст і фото"); iconName: "image-text"; objectName: "quickTranslationTab" }
            NavigationTab { text: qsTr("Перевірка"); iconName: "review"; objectName: "reviewTab" }
            NavigationTab { text: qsTr("Історія"); iconName: "history"; objectName: "historyTab" }
            NavigationTab { text: qsTr("Налаштування"); iconName: "settings"; objectName: "settingsButton" }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 16
        RowLayout {
            Layout.fillWidth: true; spacing: 16
            LabelText { text: root.titles[root.page]; font.pixelSize: 26; font.weight: Font.Medium; Layout.fillWidth: true }
        }
        Rectangle {
            objectName: "noticeBanner"
            Layout.fillWidth: true; implicitHeight: noticeRow.implicitHeight + 22
            visible: backend.message.length > 0 && backend.messageKind !== "success"
            color: backend.messageKind === "error" ? Theme.dangerFill : backend.messageKind === "warning" ? Theme.warningFill : Theme.accentLight
            border.color: backend.messageKind === "error" ? Theme.dangerLine : backend.messageKind === "warning" ? Theme.warningLine : Theme.accentLine
            radius: 6
            RowLayout {
                id: noticeRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11
                LabelText { text: backend.message; color: backend.messageKind === "error" ? Theme.danger : root.ink; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 13 }
                Action { text: ""; iconName: "close"; hint: qsTr("Закрити повідомлення"); subtle: true; implicitWidth: 30; implicitHeight: 30; onClicked: backend.dismissMessage(); Accessible.name: hint }
            }
        }
        StackLayout {
            currentIndex: root.page; Layout.fillWidth: true; Layout.fillHeight: true

            RowLayout {
                spacing: 18
                ColumnLayout {
                    id: documentColumn
                    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                    Panel {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: root.previewOpen ? 280 : 240
                        border.color: documentDrop.containsDrag ? root.accent : root.line
                        DropArea { id: documentDrop; anchors.fill: parent; enabled: backend.canAddDocuments; onDropped: function(event) { if (event.hasUrls) backend.addFiles(event.urls) } }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 12
                            RowLayout {
                                Heading { text: qsTr("Документи") }
                                Caption { text: backend.documents.length ? String(backend.documents.length) : "" }
                                Item { Layout.fillWidth: true }
                                Action { text: ""; iconName: "clear-done"; hint: qsTr("Прибрати завершені з черги. Вони залишаться в історії"); objectName: "clearCompletedButton"; subtle: true; visible: root.hasCompleted; implicitHeight: 36; implicitWidth: 36; onClicked: backend.clearCompleted(); Accessible.name: qsTr("Прибрати завершені") }
                                Action { text: qsTr("Додати файли"); iconName: "add"; objectName: "addDocumentsButton"; visible: backend.documents.length > 0; enabled: backend.canAddDocuments; implicitHeight: 36; onClicked: backend.pickDocuments() }
                            }
                            Caption { text: qsTr("Нові файли перекладатимуться з поточними параметрами цього запуску."); visible: backend.translating; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Divider { }
                            ListView {
                                id: fileList
                                Layout.fillWidth: true; Layout.fillHeight: true
                                model: backend.documents; clip: true; spacing: 2
                                ScrollBar.vertical: ScrollBar { }
                                delegate: Rectangle {
                                    id: fileRow
                                    required property var modelData
                                    required property int index
                                    width: ListView.view.width; height: modelData.status === "translating" ? 90 : 74; radius: 6
                                    color: backend.selectedId === modelData.id ? root.accentLight : rowMouse.containsMouse ? Theme.surfaceMuted : Theme.surface
                                    MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; onClicked: backend.selectDocument(fileRow.modelData.id) }
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 10; spacing: 12
                                        Rectangle {
                                            width: 40; height: 46; radius: 5
                                            color: fileRow.modelData.format === "DOCX" ? Theme.accentLight : fileRow.modelData.format === "XLSX" ? Theme.successFill : Theme.dangerFill
                                            LabelText { anchors.centerIn: parent; text: fileRow.modelData.format; font.pixelSize: 10; font.weight: Font.Bold; color: fileRow.modelData.format === "DOCX" ? Theme.accent : fileRow.modelData.format === "XLSX" ? Theme.success : Theme.danger }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 5
                                            RowLayout {
                                                Layout.fillWidth: true
                                                LabelText { text: fileRow.modelData.name; Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; font.pixelSize: 13 }
                                                Caption { visible: !!fileRow.modelData.startedAt; text: root.duration(fileRow.modelData.status === "translating" ? (root.clockNow - fileRow.modelData.startedAt) / 1000 : fileRow.modelData.elapsedSeconds || 0) }
                                            }
                                            Caption { text: fileRow.modelData.size + " · " + fileRow.modelData.statusText; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; color: fileRow.modelData.status === "error" ? Theme.danger : root.muted }
                                            Rectangle {
                                                Layout.fillWidth: true; height: 3; radius: 2; visible: fileRow.modelData.status === "translating"; color: Theme.accentLine
                                                Rectangle { width: parent.width * fileRow.modelData.progress / 100; height: 3; radius: 2; color: root.accent }
                                            }
                                        }
                                        ColumnLayout {
                                            visible: !!fileRow.modelData.canMove; spacing: 0
                                            Action { text: ""; iconName: "arrow-up"; hint: qsTr("Перекласти раніше"); objectName: "moveUpButton"; subtle: true; implicitHeight: 26; implicitWidth: 30; enabled: root.canMoveQueue(fileRow.index, -1); onClicked: backend.moveDocument(fileRow.modelData.id, -1); Accessible.name: hint }
                                            Action { text: ""; iconName: "arrow-down"; hint: qsTr("Перекласти пізніше"); objectName: "moveDownButton"; subtle: true; implicitHeight: 26; implicitWidth: 30; enabled: root.canMoveQueue(fileRow.index, 1); onClicked: backend.moveDocument(fileRow.modelData.id, 1); Accessible.name: hint }
                                        }
                                        Action { text: qsTr("Відкрити"); iconName: "external"; visible: fileRow.modelData.status === "done"; implicitHeight: 32; onClicked: backend.openPath(fileRow.modelData.output) }
                                        Action { text: ""; iconName: "trash"; hint: qsTr("Прибрати документ із черги"); objectName: "removeDocumentButton"; subtle: true; implicitHeight: 34; implicitWidth: 34; enabled: !!fileRow.modelData.canRemove; onClicked: backend.removeDocument(fileRow.modelData.id); Accessible.name: hint }
                                    }
                                }
                                ColumnLayout {
                                    anchors.centerIn: parent; width: Math.min(360, parent.width - 24); spacing: 12; visible: backend.documents.length === 0
                                    Item {
                                        Layout.alignment: Qt.AlignHCenter; width: 56; height: 60
                                        Icon { anchors.centerIn: parent; name: "document"; width: 36; height: 36; color: Theme.muted }
                                    }
                                    Heading { text: qsTr("Додайте документи для перекладу"); font.pixelSize: 18; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                                    Caption { text: qsTr("Перетягніть файли сюди або виберіть їх на комп’ютері."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 1.3 }
                                    Action { text: qsTr("Вибрати файли"); iconName: "folder"; primary: true; Layout.alignment: Qt.AlignHCenter; enabled: backend.canAddDocuments; onClicked: backend.pickDocuments() }
                                    Caption { text: "DOCX  ·  XLSX  ·  PDF"; Layout.alignment: Qt.AlignHCenter; font.pixelSize: 11 }
                                }
                            }
                            Divider { visible: !!backend.selectedDocument.path }
                            ColumnLayout {
                                visible: backend.selectedDocument.format === "PDF" || backend.recognizingScan
                                Layout.fillWidth: true; spacing: 8
                                RowLayout {
                                    objectName: "documentWorkflowInfo"
                                    visible: !!backend.selectedDocument.workflowText && !backend.recognizingScan
                                    Layout.fillWidth: true; spacing: 9
                                    Icon { name: "info"; color: root.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.alignment: Qt.AlignTop; Layout.topMargin: 1 }
                                    Caption { text: backend.selectedDocument.workflowText || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.25 }
                                }
                                RowLayout {
                                    visible: backend.recognizingScan
                                    Layout.fillWidth: true; spacing: 9
                                    BusyIndicator { running: backend.recognizingScan; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                                    Caption { objectName: "scanStatusText"; text: backend.scanMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                }
                                ProgressBar { objectName: "scanProgressBar"; visible: backend.recognizingScan; from: 0; to: 100; value: backend.scanProgress; Layout.fillWidth: true }
                                RowLayout {
                                    visible: !backend.recognizingScan
                                    Layout.fillWidth: true
                                    Action {
                                        objectName: "pdfPreparationOptionsButton"
                                        text: qsTr("Додаткові дії з PDF"); iconName: root.pdfPreparationOptionsOpen ? "chevron-down" : "chevron-right"
                                        subtle: true; implicitHeight: 30; leftPadding: 0; enabled: !backend.busy
                                        onClicked: root.pdfPreparationOptionsOpen = !root.pdfPreparationOptionsOpen
                                    }
                                    Item { Layout.fillWidth: true }
                                    Action {
                                        objectName: "importAbbyyResultButton"; text: root.currentAbbyyHandoff.continueTranslation ? qsTr("Імпортувати й продовжити") : qsTr("Імпортувати DOCX…"); iconName: "import"; subtle: true; implicitHeight: 30
                                        visible: root.currentAbbyyHandoff.sourceId === backend.selectedDocument.id && !!root.currentAbbyyHandoff.sourceId
                                        enabled: root.canImportAbbyyResult; onClicked: abbyyHandoffDialog.open()
                                    }
                                }
                                ColumnLayout {
                                    visible: root.pdfPreparationOptionsOpen && !backend.recognizingScan
                                    Layout.fillWidth: true; spacing: 6
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Action {
                                            objectName: "recognizePdfButton"
                                            text: root.manualAbbyy ? qsTr("Відкрити в ABBYY…") : qsTr("Скан PDF → DOCX"); iconName: "document"; implicitHeight: 34
                                            enabled: !backend.busy && backend.selectedDocument.status !== "loading"
                                            onClicked: backend.recognizeSelectedPdf(root.sourceCodes[sourceLanguage.currentIndex])
                                        }
                                        Item { Layout.fillWidth: true }
                                        Action { objectName: "scanSettingsButton"; text: root.width < 1120 ? "" : qsTr("Налаштувати OCR"); hint: qsTr("Налаштувати OCR"); iconName: "settings"; subtle: true; implicitHeight: 32; enabled: !backend.busy; onClicked: root.openScanSettings() }
                                    }
                                    Caption {
                                        text: root.manualAbbyy ? qsTr("Збережіть розпізнаний документ як DOCX у FineReader, перевірте його та поверніть у чергу.") : qsTr("Створює DOCX локально й замінює PDF у черзі. Перевірте текст і таблиці перед перекладом.")
                                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    }
                                }
                            }
                            RowLayout {
                                visible: !!backend.selectedDocument.path; Layout.fillWidth: true; spacing: 4
                                Action { text: qsTr("Оригінал"); iconName: "document"; subtle: true; implicitHeight: 30; onClicked: backend.openPath(backend.selectedDocument.path) }
                                Action { text: root.previewOpen ? qsTr("Сховати текст") : qsTr("Переглянути текст"); iconName: root.previewOpen ? "eye-off" : "eye"; subtle: true; implicitHeight: 30; visible: backend.selectedDocument.format !== "PDF"; onClicked: root.previewOpen = !root.previewOpen }
                                Item { Layout.fillWidth: true }
                                Action { text: backend.selectedDocument.warningCount ? qsTr("Зауваження: %1").arg(backend.selectedDocument.warningCount) : qsTr("Звіт"); iconName: "review"; subtle: true; implicitHeight: 30; visible: !!backend.selectedDocument.report; onClicked: root.page = 1 }
                                Action { text: qsTr("Переглянути помилки"); objectName: "openFailureReportButton"; iconName: "review"; subtle: true; implicitHeight: 30; visible: !!backend.selectedDocument.diagnosticReport; onClicked: backend.openFailureReport() }
                            }
                              Caption { text: qsTr("З пам’яті: %1 · повтори: %2 · адаптовано: %3").arg(backend.selectedDocument.memorySegments || 0).arg(backend.selectedDocument.repeatedSegments || 0).arg(backend.selectedDocument.adaptedSegments || 0); visible: (backend.selectedDocument.memorySegments || 0) > 0 || (backend.selectedDocument.repeatedSegments || 0) > 0 || (backend.selectedDocument.adaptedSegments || 0) > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Divider { }
                            RowLayout {
                                Layout.fillWidth: true
                                Caption { text: qsTr("Результати") }
                                Caption { text: backend.outputFolder; Layout.fillWidth: true; elide: Text.ElideMiddle; color: root.ink }
                                Action { text: qsTr("Відкрити"); iconName: "folder"; objectName: "openOutputFolderButton"; subtle: true; implicitHeight: 32; onClicked: backend.openOutputFolder(); hint: qsTr("Відкрити папку результатів") }
                                Action { text: qsTr("Змінити…"); iconName: "settings"; subtle: true; implicitHeight: 32; enabled: !backend.busy; onClicked: backend.pickOutputFolder() }
                            }
                        }
                    }
                    Panel {
                        visible: root.previewOpen && !!backend.selectedDocument.path && backend.selectedDocument.format !== "PDF"
                        Layout.fillWidth: true; Layout.preferredHeight: Math.min(230, documentColumn.height * 0.3); Layout.minimumHeight: 80
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 10
                            RowLayout {
                                Caption { text: qsTr("ОРИГІНАЛ"); Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold; font.pixelSize: 11 }
                                Caption { text: qsTr("ПЕРЕКЛАД"); Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold; font.pixelSize: 11 }
                            }
                            Divider { }
                            ListView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 14; model: backend.segments
                                ScrollBar.vertical: ScrollBar { }
                                delegate: Item {
                                    id: segmentRow
                                    required property var modelData
                                    width: ListView.view.width; height: Math.max(sourceText.implicitHeight, targetText.implicitHeight) + 22
                                    Caption { text: segmentRow.modelData.location; font.pixelSize: 10; width: parent.width; elide: Text.ElideRight }
                                    RowLayout {
                                        anchors.top: parent.top; anchors.topMargin: 20; width: parent.width; spacing: 24
                                        LabelText { id: sourceText; text: segmentRow.modelData.source; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop }
                                        LabelText { id: targetText; text: segmentRow.modelData.translation || qsTr("Після перекладу"); color: segmentRow.modelData.translation ? root.ink : root.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop }
                                    }
                                }
                            }
                        }
                    }
                }
                Panel {
                    Layout.preferredWidth: 320; Layout.minimumWidth: 280; Layout.fillHeight: true
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 20; spacing: 16
                        ScrollView {
                            id: translationScroll
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                              ScrollBar.vertical: ReadingScrollBar { }
                              ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                            ColumnLayout {
                                width: translationScroll.availableWidth - 12; spacing: 10
                                Heading { text: qsTr("Мови перекладу"); Layout.bottomMargin: 6 }
                                Caption { text: qsTr("Мова оригіналу") }
                                  Select { id: sourceLanguage; objectName: "sourceLanguage"; model: root.sourceCodes; optionLabels: [qsTr("Автовизначення")].concat(root.languageLabels); Layout.fillWidth: true; enabled: !backend.busy && !backend.memoryProject.id }
                                Caption { text: qsTr("Перекласти на"); Layout.topMargin: 7 }
                                  Select { id: targetLanguage; objectName: "targetLanguage"; model: root.targetCodes; optionLabels: root.languageLabels; currentIndex: 0; Layout.fillWidth: true; enabled: !backend.busy && !backend.memoryProject.id }
                                Caption {
                                    visible: root.needsSourceLanguage || root.sameLanguage
                                    text: root.needsSourceLanguage ? qsTr("Для PDF виберіть мову оригіналу.") : qsTr("Виберіть іншу мову перекладу.")
                                    color: Theme.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true
                                }
                                  Caption { text: qsTr("Пам’ять проєкту") }
                                  Select {
                                      objectName: "memoryProjectSelector"
                                      Layout.fillWidth: true
                                      model: backend.memoryProjects.map(function(project) { return project.id })
                                      optionLabels: backend.memoryProjects.map(function(project) { return project.name })
                                      currentIndex: Math.max(0, model.indexOf(backend.memoryProject.id || ""))
                                      enabled: !backend.busy && !root.reviewLocked
                                      onActivated: function(index) { backend.selectMemoryProject(model[index]); root.syncMemoryLanguages() }
                                      Accessible.name: qsTr("Пам’ять проєкту")
                                  }
                                  Action { text: backend.memoryProject.id ? qsTr("Еталони й глосарій…") : qsTr("Створити проєкт…"); objectName: "manageMemoryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; implicitHeight: 30; Layout.fillWidth: true; enabled: !backend.busy && !root.reviewLocked; onClicked: memoryDialog.open(); hint: qsTr("Схожі фрагменти потребують платного запиту редактора; точні збіги використовують готовий переклад.") }
                                  Caption { text: qsTr("Пам’ять поповнюється після перекладу автоматично. Глосарій та еталони необов’язкові."); visible: !!backend.memoryProject.id; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                                  Action { text: backend.glossaryCount ? qsTr("Глосарій: %1 термінів…").arg(backend.glossaryCount) : qsTr("Глосарій (необов’язково)…"); objectName: "editGlossaryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; Layout.fillWidth: true; enabled: !backend.busy; onClicked: root.openGlossary(); hint: qsTr("Додайте власні терміни або створіть пропозиції з документів. Готовий CSV не потрібен.") }
                                  Divider { Layout.topMargin: 10; Layout.bottomMargin: 4 }
                                Action { text: qsTr("Параметри перекладу"); iconName: root.translationOptionsOpen ? "chevron-down" : "chevron-right"; subtle: true; leftAligned: true; leftPadding: 0; Layout.fillWidth: true; implicitHeight: 34; onClicked: root.translationOptionsOpen = !root.translationOptionsOpen }
                                ColumnLayout {
                                    visible: root.translationOptionsOpen; Layout.fillWidth: true; spacing: 9
                                    Caption { text: qsTr("Тематика") }
                                    Select { id: domain; model: root.domainValues; optionLabels: root.domainLabels; Layout.fillWidth: true; enabled: !backend.busy }
                                    Toggle { id: review; objectName: "reviewEnabled"; text: qsTr("Редакторська перевірка"); checked: true; enabled: !backend.busy }
                                      Toggle { id: autoGlossary; text: qsTr("Автоматично добирати терміни для документа"); checked: true; enabled: !backend.busy && !backend.memoryProject.id }
                                      Caption { text: backend.memoryProject.id ? qsTr("У проєкті діє ваш спільний список термінів. Порожній список також дозволений.") : qsTr("Автоматичний добір виконує додатковий API-запит під час перекладу. Ваші власні терміни мають пріоритет."); Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                                }
                                Action {
                                    text: qsTr("Власні інструкції") + (backend.translationInstructions.trim().length ? " •" : "")
                                    objectName: "documentInstructionsButton"; iconName: "edit"; subtle: true; leftAligned: true; leftPadding: 0
                                    Layout.fillWidth: true; enabled: !backend.busy; onClicked: instructionsDialog.open()
                                }
                                Caption { text: qsTr("Перевіряємо переклад і зберігаємо оригінальний файл."); visible: review.checked && !root.hasPdf && root.translationOptionsOpen; wrapMode: Text.WordWrap; Layout.fillWidth: true; lineHeight: 1.4 }
                                Caption { text: qsTr("Редакторську перевірку вимкнено."); visible: !review.checked && !root.hasPdf; color: Theme.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                Caption { text: qsTr("PDF потребує візуальної перевірки. Редакторський прохід доступний для DOCX і XLSX."); visible: root.hasPdf; wrapMode: Text.WordWrap; Layout.fillWidth: true; lineHeight: 1.4 }
                            }
                        }
                        ColumnLayout {
                            visible: !backend.settings.model; Layout.fillWidth: true; spacing: 4
                            Caption { text: qsTr("Спочатку підключіть модель у налаштуваннях."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Action { text: qsTr("Налаштувати підключення"); iconName: "settings"; subtle: true; implicitHeight: 30; Layout.leftMargin: -10; onClicked: root.page = 2 }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Action {
                                objectName: "translateButton"
                                iconName: root.preparingDocuments ? "document" : backend.translating ? (backend.paused ? "play" : "pause") : "translate"
                                text: root.preparingDocuments ? qsTr("Підготовка…") : backend.translating ? (backend.paused ? qsTr("Продовжити") : qsTr("Пауза")) : qsTr("Перекласти")
                                primary: true; Layout.fillWidth: true; implicitHeight: 44
                                enabled: !root.preparingDocuments && (backend.translating || (!backend.busy && backend.documents.length > 0 && !!backend.settings.model && !root.needsSourceLanguage && !root.sameLanguage))
                                onClicked: backend.translating ? backend.togglePause() : backend.start(root.sourceCodes[sourceLanguage.currentIndex], root.targetCodes[targetLanguage.currentIndex], domain.currentText, review.checked, autoGlossary.checked)
                            }
                            Action { text: qsTr("Стоп"); iconName: "stop"; objectName: "cancelButton"; visible: backend.translating || backend.recognizingScan; implicitHeight: 44; onClicked: backend.cancel() }
                        }
                    }
                }
            }

            ColumnLayout {
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Caption { text: qsTr("Документ") }
                    Select {
                        id: reviewDocumentSelector
                        objectName: "reviewDocumentSelector"
                        Layout.fillWidth: true
                        model: backend.reviewDocuments.map(function(document) { return document.name })
                        currentIndex: backend.reviewDocuments.findIndex(function(document) { return document.id === backend.reviewDocumentId })
                        displayText: backend.reviewDocument.name || qsTr("Виберіть документ")
                        enabled: backend.reviewDocuments.length > 0 && !root.reviewLocked
                        onActivated: function(index) { backend.selectReviewDocument(backend.reviewDocuments[index].id) }
                        Accessible.name: qsTr("Документ для перевірки")
                    }
                    Action { text: qsTr("Оригінал"); iconName: "document"; objectName: "reviewOriginalButton"; subtle: true; enabled: !!backend.reviewDocument.path; onClicked: backend.openPath(backend.reviewDocument.path) }
                    Action { text: qsTr("Переклад"); iconName: "external"; objectName: "reviewOutputButton"; subtle: true; enabled: !!backend.reviewDocument.outputExists; onClicked: backend.openPath(backend.reviewDocument.output) }
                    Action { text: qsTr("Звіт"); iconName: "review"; objectName: "reviewReportButton"; subtle: true; enabled: !!backend.reviewDocument.reportExists; onClicked: backend.openReviewReport() }
                    Action { text: backend.reviewSaving ? qsTr("Зберігаємо…") : qsTr("Зберегти копію"); iconName: "save"; objectName: "reviewExportButton"; primary: true; visible: backend.reviewHasEdits || backend.reviewSaving; enabled: backend.reviewHasEdits && !root.reviewLocked; onClicked: backend.exportReviewEdits(); hint: qsTr("Створити виправлену копію. Попередній переклад залишиться без змін") }
                }
                Caption { text: backend.reviewActionError; objectName: "reviewActionError"; visible: text.length > 0; color: Theme.danger; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Caption { text: qsTr("Правки збережено як чернетку. Збережіть копію, щоб отримати виправлений документ."); visible: backend.reviewHasEdits && !backend.reviewEditing; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: root.accent }
                RowLayout {
                    Layout.fillWidth: true; spacing: 14
                    TabBar {
                        id: reviewModes
                        objectName: "reviewModes"
                        enabled: !root.reviewLocked
                        implicitWidth: Math.max(316, 2 * Math.max(reviewIssuesMode.implicitWidth, reviewAllMode.implicitWidth) + 2)
                        Layout.preferredWidth: implicitWidth; Layout.minimumWidth: implicitWidth; implicitHeight: 38
                        currentIndex: backend.reviewFilter === "all" ? 1 : 0
                        onCurrentIndexChanged: {
                            var filter = currentIndex === 1 ? "all" : "issues"
                            if (backend.reviewFilter !== filter) backend.setReviewFilter(filter)
                        }
                        background: Rectangle { color: Theme.disabled; radius: 7 }
                        NavigationTab { id: reviewIssuesMode; text: qsTr("Зауваження"); iconName: "attention"; objectName: "reviewIssuesFilter"; implicitHeight: 38 }
                        NavigationTab { id: reviewAllMode; text: qsTr("Увесь текст"); iconName: "list"; objectName: "reviewAllFilter"; implicitHeight: 38 }
                    }
                    Field {
                        id: reviewSearchField
                        objectName: "reviewSearchField"
                        enabled: !root.reviewLocked
                        Layout.fillWidth: true; implicitHeight: 38
                        placeholderText: qsTr("Пошук у тексті та зауваженнях")
                        text: backend.reviewSearch
                        onTextEdited: backend.setReviewSearch(text)
                        Accessible.name: qsTr("Пошук у документі")
                        rightPadding: 38
                        Action { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: 3; text: ""; iconName: "close"; hint: qsTr("Очистити пошук"); subtle: true; implicitHeight: 30; implicitWidth: 30; visible: reviewSearchField.text.length > 0; onClicked: backend.setReviewSearch(""); Accessible.name: hint }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
                    Panel {
                        Layout.preferredWidth: reviewModes.width; Layout.minimumWidth: 270; Layout.fillHeight: true
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 12; spacing: 8
                            LabelText {
                                text: backend.reviewSummary.loading ? qsTr("Завантажуємо…") : backend.reviewFilter === "issues" ? qsTr("Потребують уваги: %1").arg(backend.reviewSummary.attention || 0) : qsTr("Фрагментів: %1").arg(backend.reviewSummary.segments || 0)
                                font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true
                            }
                            Caption { text: qsTr("Приміток до документа: %1").arg(backend.reviewSummary.notes || 0); visible: backend.reviewFilter === "all" && backend.reviewSummary.notes > 0; Layout.fillWidth: true }
                            Caption { text: qsTr("Коректні: %1 · З правками: %2").arg(backend.reviewSummary.accepted || 0).arg(backend.reviewSummary.edited || 0); visible: backend.reviewSummary.accepted > 0 || backend.reviewSummary.edited > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Caption { text: qsTr("Знайдено: %1").arg(backend.reviewEntries.length); visible: backend.reviewSearch.length > 0; Layout.fillWidth: true }
                            Divider { }
                            ListView {
                                id: reviewEntryList
                                objectName: "reviewEntryList"
                                enabled: !root.reviewLocked
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                                model: backend.reviewEntries
                                currentIndex: root.reviewEntryIndex
                                onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                                ScrollBar.vertical: ReadingScrollBar { }
                                Keys.onUpPressed: backend.stepReviewEntry(-1)
                                Keys.onDownPressed: backend.stepReviewEntry(1)
                                delegate: ItemDelegate {
                                    id: reviewEntryRow
                                    required property var modelData
                                    width: ListView.view.width - 10; height: 96
                                    leftPadding: 12; rightPadding: 10; topPadding: 10; bottomPadding: 10
                                    highlighted: modelData.id === backend.selectedReviewId
                                    Accessible.name: modelData.issueCount > 0 ? qsTr("%1, зауважень: %2").arg(modelData.location).arg(modelData.issueCount) : modelData.location
                                    background: Rectangle {
                                        radius: 6; color: reviewEntryRow.highlighted ? root.accentLight : reviewEntryRow.hovered ? Theme.surfaceMuted : Theme.surface
                                        border.color: reviewEntryRow.visualFocus ? root.accent : reviewEntryRow.highlighted ? Theme.accentLine : "transparent"
                                        Rectangle { x: 0; y: 10; width: 3; height: parent.height - 20; radius: 1; visible: reviewEntryRow.modelData.issueCount > 0 && !reviewEntryRow.modelData.accepted; color: reviewEntryRow.modelData.severity === "error" ? Theme.danger : Theme.warning }
                                    }
                                    contentItem: ColumnLayout {
                                        spacing: 5
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 6
                                            Caption { text: reviewEntryRow.modelData.location || qsTr("Документ"); Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; color: root.ink }
                                            Icon { name: "check"; color: Theme.success; visible: !!reviewEntryRow.modelData.accepted; Accessible.name: qsTr("Позначено як коректне") }
                                            Icon { name: "edit"; color: root.accent; visible: !!reviewEntryRow.modelData.edited; Accessible.name: qsTr("Є правка") }
                                            Caption { text: String(reviewEntryRow.modelData.issueCount || ""); visible: reviewEntryRow.modelData.issueCount > 0 && !reviewEntryRow.modelData.accepted; font.weight: Font.DemiBold; color: reviewEntryRow.modelData.severity === "error" ? Theme.danger : Theme.warning }
                                        }
                                        LabelText {
                                            text: (reviewEntryRow.modelData.source || reviewEntryRow.modelData.translation || (reviewEntryRow.modelData.messages || [])[0] || qsTr("Примітка до документа")).replace(/\s+/g, " ")
                                            font.pixelSize: 12; Layout.fillWidth: true; Layout.fillHeight: true; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight
                                        }
                                    }
                                    onClicked: backend.selectReviewEntry(modelData.id)
                                }
                                Caption { anchors.centerIn: parent; width: parent.width - 24; text: backend.reviewSummary.loading ? qsTr("Завантаження звіту…") : backend.reviewSearch.length > 0 ? qsTr("Збігів немає") : qsTr("Список порожній"); visible: backend.reviewEntries.length === 0; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                        visible: !!backend.selectedReviewEntry.id
                        RowLayout {
                            Layout.fillWidth: true; spacing: 6
                            LabelText { text: backend.selectedReviewEntry.location || qsTr("Документ"); Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; font.pixelSize: 13 }
                            Action { text: qsTr("Редагувати"); iconName: "edit"; objectName: "reviewEditButton"; subtle: true; implicitHeight: 32; visible: backend.selectedReviewEntry.kind === "segment" && !backend.reviewEditing; enabled: !!backend.selectedReviewEntry.canEdit && !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.beginReviewEdit(); hint: backend.selectedReviewEntry.editReason || qsTr("Редагувати текст перекладу цього фрагмента") }
                            Action { text: ""; iconName: "undo"; objectName: "reviewDiscardEditButton"; implicitWidth: 34; implicitHeight: 32; visible: !!backend.selectedReviewEntry.edited && !backend.reviewEditing; enabled: !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.discardReviewEdit(); hint: qsTr("Скасувати збережену правку цього фрагмента"); Accessible.name: hint }
                            Action { text: qsTr("Застосувати"); iconName: "check"; objectName: "reviewApplyButton"; primary: true; implicitHeight: 32; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.applyReviewEdit(); hint: qsTr("Зберегти правку в чернетці") }
                            Action { text: qsTr("Скасувати"); iconName: "close"; objectName: "reviewCancelEditButton"; implicitHeight: 32; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.cancelReviewEdit() }
                            Action { text: ""; iconName: "chevron-left"; hint: qsTr("Попередній запис"); objectName: "reviewPreviousButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !root.reviewLocked && root.reviewEntryIndex > 0; onClicked: backend.stepReviewEntry(-1); Accessible.name: hint }
                            Caption { text: (root.reviewEntryIndex + 1) + " / " + backend.reviewEntries.length; visible: !backend.reviewEditing; Layout.minimumWidth: 46; horizontalAlignment: Text.AlignHCenter }
                            Action { text: ""; iconName: "chevron-right"; hint: qsTr("Наступний запис"); objectName: "reviewNextButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !root.reviewLocked && root.reviewEntryIndex >= 0 && root.reviewEntryIndex < backend.reviewEntries.length - 1; onClicked: backend.stepReviewEntry(1); Accessible.name: hint }
                        }
                        Action { text: qsTr("Запам’ятати формулювання"); objectName: "rememberReviewWordingButton"; iconName: "book"; subtle: true; implicitHeight: 30; visible: !!backend.memoryProject.id && backend.selectedReviewEntry.kind === "segment" && !backend.reviewEditing; enabled: backend.canRememberReviewWording; onClicked: rememberWordingDialog.open() }
                        Panel {
                            id: explanationsPanel
                            Layout.fillWidth: true
                            Layout.fillHeight: backend.selectedReviewEntry.kind === "document"
                            Layout.preferredHeight: backend.selectedReviewEntry.kind === "document" ? 1 : Math.min(backend.reviewEditing ? 108 : root.height < 760 ? 112 : 160, explanationsText.implicitHeight + 70)
                            Layout.minimumHeight: 86
                            visible: (backend.selectedReviewEntry.messages || []).length > 0
                            color: backend.selectedReviewEntry.accepted ? Theme.successFill : backend.selectedReviewEntry.severity === "error" ? Theme.dangerFill : backend.selectedReviewEntry.severity === "warning" ? Theme.warningFill : Theme.surface
                            border.color: backend.selectedReviewEntry.accepted ? Theme.successLine : backend.selectedReviewEntry.severity === "error" ? Theme.dangerLine : backend.selectedReviewEntry.severity === "warning" ? Theme.warningLine : root.line
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 14; spacing: 9
                                RowLayout {
                                    Icon { name: backend.selectedReviewEntry.accepted ? "check" : backend.selectedReviewEntry.severity === "info" ? "info" : "attention"; color: backend.selectedReviewEntry.accepted ? Theme.success : backend.selectedReviewEntry.severity === "error" ? Theme.danger : backend.selectedReviewEntry.severity === "warning" ? Theme.warning : root.muted }
                                    LabelText { text: backend.selectedReviewEntry.accepted ? qsTr("Позначено як коректне") : backend.selectedReviewEntry.severity === "info" ? qsTr("Примітка до документа") : ((backend.selectedReviewEntry.messages || []).length > 1 ? qsTr("На що звернути увагу (%1)").arg(backend.selectedReviewEntry.messages.length) : qsTr("На що звернути увагу")); font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Action { text: qsTr("Це коректно"); iconName: "check"; objectName: "reviewConfirmButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.canAccept && !backend.selectedReviewEntry.accepted; enabled: !root.reviewLocked; onClicked: backend.confirmReviewEntry(); hint: qsTr("Позначити цей запис як перевірений. Він залишиться у вкладці «Увесь текст»") }
                                    Action { text: qsTr("Повернути зауваження"); iconName: "undo"; objectName: "reviewReopenButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.accepted; enabled: !root.reviewLocked; onClicked: backend.reopenReviewEntry() }
                                }
                                ScrollView {
                                    id: explanationsScroll
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                                    ScrollBar.vertical: ReadingScrollBar { }
                                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                    ReadOnlyText { id: explanationsText; objectName: "reviewExplanationText"; width: explanationsScroll.availableWidth; text: (backend.selectedReviewEntry.messages || []).join("\n\n") + (backend.selectedReviewEntry.severity === "error" ? "\n\n" + qsTr("Помилку потрібно виправити; позначити її коректною не можна.") : ""); onTextChanged: if (explanationsScroll.contentItem) explanationsScroll.contentItem.contentY = 0; font.pixelSize: 13; Accessible.name: qsTr("Пояснення до вибраного запису") }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                            visible: backend.selectedReviewEntry.kind !== "document"
                            ReviewTextPanel { heading: qsTr("ОРИГІНАЛ"); text: backend.selectedReviewEntry.source || ""; textObjectName: "reviewSourceText"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1 }
                            ReviewTextPanel {
                                heading: qsTr("ПЕРЕКЛАД"); text: backend.reviewEditing ? backend.reviewDraftText : backend.selectedReviewEntry.translation || ""
                                textObjectName: "reviewTranslationText"; editing: backend.reviewEditing; edited: !!backend.selectedReviewEntry.edited
                                editError: backend.reviewEditing ? backend.reviewEditError : ""
                                onDraftEdited: function(value) { if (backend.reviewEditing && value !== backend.reviewDraftText) backend.updateReviewDraft(value) }
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                            }
                        }
                    }
                    Panel {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        visible: !backend.selectedReviewEntry.id
                        ColumnLayout {
                            anchors.centerIn: parent; width: Math.min(400, parent.width - 40); spacing: 14
                            BusyIndicator { running: backend.reviewSummary.loading; visible: running; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 36; Layout.preferredHeight: 36 }
                            Icon { name: backend.reviewSummary.error ? "attention" : "review"; color: Theme.muted; visible: !backend.reviewSummary.loading; Layout.preferredWidth: 32; Layout.preferredHeight: 32; Layout.alignment: Qt.AlignHCenter }
                            Heading { text: root.reviewEmptyTitle(); Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
                            Caption { text: root.reviewEmptyDescription(); Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.3 }
                            Action { text: qsTr("Очистити пошук"); iconName: "close"; visible: backend.reviewSearch.length > 0 && !backend.reviewSummary.loading; Layout.alignment: Qt.AlignHCenter; onClicked: backend.setReviewSearch("") }
                            Action { text: qsTr("Показати всі записи"); iconName: "list"; objectName: "reviewShowAllButton"; visible: !backend.reviewSummary.loading && !backend.reviewSummary.error && backend.reviewFilter === "issues" && backend.reviewSearch.length === 0 && (backend.reviewSummary.segments > 0 || backend.reviewSummary.notes > 0); Layout.alignment: Qt.AlignHCenter; onClicked: backend.setReviewFilter("all") }
                        }
                    }
                }
            }

            ColumnLayout {
                spacing: 14
                ScrollView {
                    id: settingsScroll
                    objectName: "settingsScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: Math.min(880, settingsScroll.availableWidth); x: (settingsScroll.availableWidth - width) / 2; spacing: 16
                        Panel {
                            Layout.fillWidth: true; implicitHeight: connectionBody.implicitHeight + 40
                            ColumnLayout {
                                id: connectionBody
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 10
                                Heading { text: qsTr("Підключення до AI") }
                                Caption { text: qsTr("Оберіть готове підключення або задайте власне. Додайте API-ключ і збережіть налаштування."); wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.bottomMargin: 4 }
                                Caption { text: qsTr("Провайдер і пресет") }
                                Select {
                                    id: providerPreset; objectName: "providerPresetSelector"
                                    Layout.fillWidth: true
                                    model: backend.providerPresets.map(function(item) { return item.id })
                                    optionLabels: backend.providerPresets.map(function(item) { return item.label })
                                    currentIndex: Math.max(0, model.indexOf(root.providerPresetId))
                                    enabled: !backend.busy && !backend.modelsBusy
                                    onActivated: function(index) { root.applyProviderPreset(model[index]) }
                                    Accessible.name: qsTr("Провайдер і пресет")
                                }
                                Caption { objectName: "providerPresetDescription"; text: root.activeProviderPreset.description || ""; visible: text.length > 0; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.bottomMargin: 6 }
                                Caption { text: qsTr("API-ключ"); Layout.topMargin: 8 }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    Field {
                                        id: apiKey; objectName: "apiKeyField"; Layout.fillWidth: true
                                        echoMode: root.keyVisible ? TextInput.Normal : TextInput.Password
                                        placeholderText: root.removeSavedKey ? qsTr("Ключ буде видалено після збереження") : root.savedEndpointSelected && backend.settings.keyPresent ? qsTr("Ключ задано. Введіть новий, щоб замінити") : qsTr("Вставте API-ключ")
                                        enabled: !backend.busy; Accessible.name: qsTr("API-ключ")
                                        onTextEdited: { if (text.length > 0) root.removeSavedKey = false; backend.clearModels() }
                                    }
                                    Action { text: root.keyVisible ? qsTr("Приховати") : qsTr("Показати"); iconName: root.keyVisible ? "eye-off" : "eye"; enabled: apiKey.text.length > 0; onClicked: root.keyVisible = !root.keyVisible }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    Toggle { id: rememberKey; objectName: "rememberKey"; text: qsTr("Запам’ятати ключ на цьому комп’ютері"); checked: true; enabled: !backend.busy }
                                    Item { Layout.fillWidth: true }
                                    Action {
                                        text: root.removeSavedKey ? qsTr("Скасувати видалення") : qsTr("Видалити ключ"); iconName: root.removeSavedKey ? "undo" : "trash"; subtle: true; danger: !root.removeSavedKey; implicitHeight: 30
                                        visible: root.savedEndpointSelected && !!backend.settings.keyPresent; enabled: !backend.busy
                                        onClicked: { root.removeSavedKey = !root.removeSavedKey; apiKey.clear(); root.keyVisible = false }
                                    }
                                }
                                Caption { objectName: "credentialStatusCaption"; text: root.removeSavedKey ? qsTr("Ключ буде видалено після натискання «Зберегти».") : !root.savedEndpointSelected ? qsTr("Введіть ключ для вибраного провайдера. Ключ іншого сервісу не переноситься.") : backend.settings.credentialStatus || qsTr("Ключ не відображається після збереження."); wrapMode: Text.WordWrap; Layout.fillWidth: true; color: root.removeSavedKey ? Theme.warning : root.muted }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 16; Layout.topMargin: 6
                                    ColumnLayout {
                                        Layout.fillWidth: true; Layout.preferredWidth: 3; spacing: 7
                                        Caption { text: qsTr("Адреса API") }
                                        Field { id: baseUrl; objectName: "baseUrlField"; Layout.fillWidth: true; placeholderText: "https://api.openai.com/v1"; enabled: !backend.busy; Accessible.name: qsTr("Адреса API"); onTextChanged: backend.clearModels(); onTextEdited: { root.providerPresetId = "custom"; root.clearDraftKey() } }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; Layout.preferredWidth: 2; spacing: 7
                                        Caption { text: qsTr("Модель перекладу") }
                                        ModelPicker { id: modelField; objectName: "modelField"; Layout.fillWidth: true; placeholderText: qsTr("Виберіть або введіть назву"); enabled: !backend.busy; Accessible.name: qsTr("Модель перекладу") }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 12; Layout.topMargin: 4
                                    Caption { text: backend.modelsBusy ? qsTr("Отримуємо доступні моделі…") : backend.modelsMessage || qsTr("Оновіть список моделей із сервісу або введіть назву вручну."); objectName: "modelsMessage"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Action { text: backend.modelsBusy ? qsTr("Оновлюємо…") : qsTr("Оновити моделі"); iconName: "refresh"; objectName: "refreshModelsButton"; implicitHeight: 36; enabled: !backend.busy && !backend.modelsBusy && !root.removeSavedKey && baseUrl.text.trim().length > 0; onClicked: backend.refreshModels({baseUrl: baseUrl.text, apiKey: apiKey.text, apiMode: apiMode.currentText}) }
                                }
                                Divider { Layout.topMargin: 9 }
                                Action { text: qsTr("Додаткові параметри підключення"); iconName: root.connectionOptionsOpen ? "chevron-down" : "chevron-right"; subtle: true; implicitHeight: 32; Layout.leftMargin: -10; onClicked: root.connectionOptionsOpen = !root.connectionOptionsOpen }
                                ColumnLayout {
                                    visible: root.connectionOptionsOpen; Layout.fillWidth: true; spacing: 10
                                    Caption { text: qsTr("Окрема модель редактора") }
                                    ModelPicker { id: reviewModel; objectName: "reviewModelField"; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — та сама модель"); enabled: !backend.busy; Accessible.name: qsTr("Окрема модель редактора") }
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 16
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Протокол API") } Select { id: apiMode; objectName: "apiModeSelector"; model: ["chat", "responses", "gemini", "anthropic"]; Layout.fillWidth: true; enabled: !backend.busy; onActivated: { root.providerPresetId = "custom"; backend.clearModels(); if (root.nativeProvider) reasoning.currentIndex = 0 } } }
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Формат відповіді") } Select { id: responseFormat; model: ["auto", "strict", "json", "plain"]; Layout.fillWidth: true; enabled: !backend.busy } }
                                    }

                                }
                            }
                        }
                        Panel {
                            Layout.fillWidth: true; implicitHeight: appearanceBody.implicitHeight + 40
                            ColumnLayout {
                                id: appearanceBody
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 16
                                Heading { text: qsTr("Вигляд") }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 20
                                    LabelText { text: qsTr("Мова інтерфейсу"); Layout.fillWidth: true }
                                    Select {
                                        id: interfaceLanguage; objectName: "interfaceLanguageSelector"
                                        Layout.preferredWidth: 250
                                        model: root.uiLanguageCodes
                                        optionLabels: root.uiLanguageCodes.map(function(code) { var language = backend.uiLanguages.find(function(item) { return item.code === code }); return language ? language.label : code })
                                        currentIndex: root.uiLanguageCodes.indexOf(backend.uiLanguage)
                                        enabled: !backend.busy
                                        onActivated: function(index) { backend.setUiLanguage(root.uiLanguageCodes[index]) }
                                        Accessible.name: qsTr("Мова інтерфейсу")
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 20
                                    LabelText { text: qsTr("Тема"); Layout.fillWidth: true }
                                    Select {
                                        objectName: "interfaceThemeSelector"; Layout.preferredWidth: 250
                                        model: root.themeCodes; optionLabels: root.themeLabels
                                        currentIndex: root.themeCodes.indexOf(Theme.preference)
                                        onActivated: function(index) { backend.setUiTheme(root.themeCodes[index]) }
                                        Accessible.name: qsTr("Тема")
                                    }
                                }
                                Caption { text: qsTr("Використовує оформлення операційної системи та змінюється разом із ним."); visible: Theme.preference === "system"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            }
                        }
                        Panel {
                            Layout.fillWidth: true; implicitHeight: performanceBody.implicitHeight + 36
                            ColumnLayout {
                                id: performanceBody
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                                RowLayout {
                                    Heading { text: qsTr("Швидкість і ліміти") }
                                    Item { Layout.fillWidth: true }
                                    Caption { text: qsTr("%1 документи · %2 запити").arg(parallelDocuments.text || "2").arg(parallelRequests.text || "8") }
                                    Action { text: root.performanceOptionsOpen ? qsTr("Згорнути") : qsTr("Змінити"); iconName: root.performanceOptionsOpen ? "chevron-down" : "settings"; objectName: "performanceOptionsButton"; subtle: true; implicitHeight: 32; onClicked: root.performanceOptionsOpen = !root.performanceOptionsOpen }
                                }
                                Caption { text: Number(requestsPerMinute.text) > 0 ? qsTr("Не більше %1 запитів за хвилину. Ліміти провайдера можуть бути нижчими.").arg(requestsPerMinute.text) : qsTr("Кількість паралельних запитів впливає на швидкість і навантаження на API."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                ColumnLayout {
                                    visible: root.performanceOptionsOpen; Layout.fillWidth: true; spacing: 12
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 16
                                        ColumnLayout { visible: !root.nativeProvider; Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Рівень міркування") } Select { id: reasoning; objectName: "reasoningSelect"; model: root.reasoningCodes; optionLabels: root.reasoningLabels; Layout.fillWidth: true; enabled: !backend.busy } }
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Очікування відповіді, с") } Field { id: timeoutField; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; enabled: !backend.busy } }
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Ліміт вихідних токенів") } Field { id: maxTokens; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; enabled: !backend.busy } }
                                    }
                                    Caption { text: qsTr("0 — автоматично. Підтримка цих параметрів залежить від моделі."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 16
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Документів одночасно, 1–4") } Field { id: parallelDocuments; objectName: "parallelDocumentsField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 1; top: 4 } enabled: !backend.busy } }
                                        ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; Caption { text: qsTr("Запитів до API загалом, 1–16") } Field { id: parallelRequests; objectName: "parallelRequestsField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 1; top: 16 } enabled: !backend.busy } }
                                    }
                                    Caption { text: qsTr("Запитів за хвилину, 0 — без обмеження") }
                                    Field { id: requestsPerMinute; objectName: "requestsPerMinuteField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 0; top: 6000 } enabled: !backend.busy }
                                    Caption { text: qsTr("Ліміт запитів спільний для всіх документів. Більше паралельних запитів допомагає, доки сервіс не обмежує їхню частоту."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                }
                            }
                        }
                        Panel {
                            Layout.fillWidth: true; implicitHeight: pdfBody.implicitHeight + 36
                            ColumnLayout {
                                id: pdfBody
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                                RowLayout {
                                    Heading { text: qsTr("Переклад PDF") }
                                    Item { Layout.fillWidth: true }
                                    Caption { text: backend.settings.pdfReady ? qsTr("Рушій знайдено") : qsTr("Рушій не знайдено"); color: backend.settings.pdfReady ? Theme.success : root.muted }
                                    Action { text: root.pdfOptionsOpen ? qsTr("Згорнути") : qsTr("Налаштувати"); iconName: root.pdfOptionsOpen ? "chevron-down" : "settings"; subtle: true; implicitHeight: 30; onClicked: root.pdfOptionsOpen = !root.pdfOptionsOpen }
                                }
                                Caption { text: qsTr("PDF із текстом перекладається зі збереженням макета. Скановані сторінки програма підготує через OCR під час перекладу."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                ColumnLayout {
                                    visible: root.pdfOptionsOpen; Layout.fillWidth: true; spacing: 8
                                    Caption { text: qsTr("Шлях до BabelDOC") }
                                    Field { id: babeldocPath; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                                    AppButton { text: qsTr("Як налаштувати PDF та OCR"); iconName: "external"; subtle: true; leftAligned: true; leftPadding: 0; onClicked: Qt.openUrlExternally("https://github.com/grebocheck/FormaTranslate/blob/main/docs/PDF.md") }
                                        Divider { Layout.topMargin: 8; Layout.bottomMargin: 4 }
                                    Caption { text: qsTr("Розпізнавання сканів") }
                                    Select {
                                        id: scanProvider; objectName: "scanProviderSelector"
                                        model: root.scanProviderCodes; optionLabels: root.scanProviderLabels
                                        Layout.fillWidth: true; enabled: !backend.busy
                                        Accessible.name: qsTr("Розпізнавання сканів")
                                    }
                                    Caption { text: qsTr("Автоматично: спочатку локальний OCR, інакше встановлений ABBYY. Звичайний FineReader потребує ручного експорту DOCX."); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Caption { text: qsTr("Шлях до ABBYY FineReader"); visible: scanProvider.currentIndex !== 1 }
                                    Field { id: abbyyPath; objectName: "abbyyPathField"; visible: scanProvider.currentIndex !== 1; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                                    Caption { objectName: "abbyyDetectionStatus"; text: backend.settings.abbyyReady ? qsTr("ABBYY FineReader знайдено") + (backend.settings.abbyyDetectedPath ? "\n" + backend.settings.abbyyDetectedPath : "") : qsTr("ABBYY FineReader не знайдено"); visible: scanProvider.currentIndex !== 1; color: backend.settings.abbyyReady ? Theme.success : Theme.muted; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Toggle { id: abbyyExtendedCliConfirmed; objectName: "abbyyExtendedCliConfirmed"; visible: scanProvider.currentIndex === 3; text: qsTr("Моя ліцензія ABBYY дозволяє Extended CLI"); enabled: !backend.busy }
                                    Caption { text: qsTr("Автоматичний експорт доступний лише з відповідною ліцензією Extended CLI. Звичайна або Corporate-ліцензія сама по собі цього не підтверджує."); visible: scanProvider.currentIndex === 3; color: Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Caption { text: qsTr("Шлях до OCR-конвертера"); visible: root.showLocalOcrSettings }
                                        Field { id: scanConverterPath; objectName: "scanConverterPathField"; visible: root.showLocalOcrSettings; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                                        Caption { objectName: "scanConverterStatus"; visible: root.showLocalOcrSettings; text: backend.settings.scanConverterReady ? qsTr("OCR-конвертер знайдено") : qsTr("OCR-конвертер не знайдено"); color: backend.settings.scanConverterReady ? Theme.success : Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        Caption { text: qsTr("Потрібен окремий локальний OCR-конвертер. API-модель для розпізнавання не використовується."); visible: root.showLocalOcrSettings; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                }
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Caption { text: qsTr("Версія %1").arg(backend.appVersion) + "\nQt/PySide6 · LGPLv3"; font.pixelSize: 10 }
                    Caption { text: backend.apiStatus; Layout.fillWidth: true; elide: Text.ElideRight }
                    Action { text: qsTr("Перевірити підключення"); iconName: "connect"; objectName: "testConnectionButton"; enabled: !backend.busy; onClicked: { if (root.saveConfig()) backend.testConnection() } ToolTip.visible: hovered; ToolTip.text: qsTr("Зберігає налаштування й надсилає короткий запит до моделі") }
                    Action { text: qsTr("Зберегти"); iconName: "save"; objectName: "saveSettingsButton"; primary: true; enabled: !backend.busy; onClicked: root.saveConfig() }
                }
            }

            ColumnLayout {
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Caption { text: qsTr("Записів: %1").arg(backend.historyEntries.length); Layout.fillWidth: true }
                    Action { text: backend.historyLoading ? qsTr("Шукаємо переклади…") : qsTr("Імпортувати з папки"); iconName: "import"; objectName: "importHistoryButton"; enabled: !backend.historyLoading; onClicked: backend.importHistory(); hint: qsTr("Знайти попередні переклади в поточній папці результатів") }
                    Action { text: qsTr("Папка результатів"); iconName: "folder"; onClicked: backend.openOutputFolder() }
                }
                Caption { text: qsTr("Історія зберігається на цьому комп’ютері без вмісту документів. Повторний переклад використовує поточні налаштування."); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                ListView {
                    id: historyList
                    objectName: "historyList"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10
                    model: backend.historyEntries
                    ScrollBar.vertical: ScrollBar { }
                    delegate: Panel {
                        id: historyRow
                        required property var modelData
                        width: ListView.view.width
                        height: historyBody.implicitHeight + 32
                        ColumnLayout {
                            id: historyBody
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16; spacing: 10
                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                Icon { name: "document"; color: root.muted }
                                LabelText { text: historyRow.modelData.name; Layout.fillWidth: true; font.weight: Font.DemiBold; elide: Text.ElideMiddle }
                                Caption { text: historyRow.modelData.format || ""; font.pixelSize: 11 }
                                Rectangle {
                                    implicitWidth: historyStatusText.implicitWidth + 18; implicitHeight: 26; radius: 5
                                    color: root.historyColor(historyRow.modelData, true)
                                    Caption { id: historyStatusText; anchors.centerIn: parent; text: root.historyStatus(historyRow.modelData); color: root.historyColor(historyRow.modelData, false); font.weight: Font.DemiBold }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                Caption { text: root.historyDate(historyRow.modelData.finishedAt || historyRow.modelData.startedAt) }
                                Caption { text: root.languageName(historyRow.modelData.sourceLanguage) + " → " + root.languageName(historyRow.modelData.targetLanguage) }
                                Caption { text: historyRow.modelData.model || qsTr("Модель не вказано"); Layout.fillWidth: true; elide: Text.ElideRight }
                                Caption { text: root.duration(historyRow.modelData.elapsedSeconds || 0) }
                            }
                            Caption {
                                visible: !!historyRow.modelData.error || historyRow.modelData.warningCount > 0 || (historyRow.modelData.status === "done" && !historyRow.modelData.outputExists)
                                text: historyRow.modelData.error || (historyRow.modelData.warningCount > 0 ? qsTr("Зауважень у звіті: %1").arg(historyRow.modelData.warningCount) : qsTr("Файл результату переміщено або видалено."))
                                color: historyRow.modelData.error ? Theme.danger : Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Action { text: qsTr("Відкрити переклад"); iconName: "external"; objectName: "historyOpenButton"; implicitHeight: 34; enabled: !!historyRow.modelData.outputExists; onClicked: backend.openHistoryOutput(historyRow.modelData.id) }
                                Action { text: qsTr("Перевірити"); iconName: "eye"; objectName: "historyReviewButton"; implicitHeight: 34; enabled: !!historyRow.modelData.reviewAvailable && !root.reviewLocked; onClicked: { backend.reviewHistory(historyRow.modelData.id); root.page = 1 } }
                                Action { text: qsTr("Папка"); iconName: "folder"; objectName: "historyFolderButton"; implicitHeight: 34; enabled: !!historyRow.modelData.output; onClicked: backend.openHistoryFolder(historyRow.modelData.id) }
                                Action { text: qsTr("Звіт"); iconName: "review"; objectName: "historyReportButton"; implicitHeight: 34; enabled: !!historyRow.modelData.reportExists; onClicked: backend.openHistoryReport(historyRow.modelData.id) }
                                Item { Layout.fillWidth: true }
                                Action { text: qsTr("У чергу"); iconName: "refresh"; objectName: "historyRequeueButton"; implicitHeight: 34; enabled: backend.canAddDocuments && !!historyRow.modelData.sourceExists; onClicked: backend.requeueHistory(historyRow.modelData.id); hint: historyRow.modelData.sourceExists ? qsTr("Додати оригінал до черги з поточними налаштуваннями") : qsTr("Оригінальний файл переміщено або видалено") }
                                Action { text: qsTr("Прибрати"); iconName: "trash"; objectName: "historyForgetButton"; subtle: true; implicitHeight: 34; enabled: !backend.historyLoading; onClicked: backend.forgetHistory(historyRow.modelData.id); hint: qsTr("Прибрати запис з історії. Файли залишаться на диску") }
                            }
                        }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; width: Math.min(430, parent.width - 40); spacing: 12
                        visible: backend.historyEntries.length === 0
                        Icon { name: "history"; Layout.preferredWidth: 38; Layout.preferredHeight: 38; color: Theme.muted; Layout.alignment: Qt.AlignHCenter }
                        Heading { text: backend.historyLoading ? qsTr("Шукаємо попередні переклади…") : qsTr("Тут будуть ваші переклади"); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                        Caption { text: qsTr("Завершені переклади з’являються тут автоматично. Попередні результати можна імпортувати з вибраної папки."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 1.3 }
                    }
                }
            }
            QuickTranslate {
                objectName: "quickTranslationPage"
                controller: backend.quickTranslator
                sourceCodes: root.sourceCodes
                targetCodes: root.targetCodes
                languageLabels: root.languageLabels
                modelName: backend.settings.model || ""
                instructionsActive: backend.translationInstructions.trim().length > 0
                onInstructionsRequested: instructionsDialog.open()
                onConfigureRequested: root.page = 2
            }
        }
        Caption { text: root.page === 4 ? qsTr("Текст і фото для перекладу надсилаються вибраному AI-сервісу.") : qsTr("Документи зберігаються локально. Текст для перекладу надсилається вибраному AI-сервісу."); font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
    }

    function saveConfig() {
        var saved = backend.saveSettings({baseUrl: baseUrl.text, model: modelField.text, reviewModel: reviewModel.text,
            providerPreset: root.providerPresetId, requestsPerMinute: requestsPerMinute.text,
            apiMode: apiMode.currentText, responseFormat: responseFormat.currentText, babeldocPath: babeldocPath.text, scanConverterPath: scanConverterPath.text,
            scanProvider: root.scanProviderCodes[scanProvider.currentIndex], abbyyPath: abbyyPath.text, abbyyExtendedCliConfirmed: abbyyExtendedCliConfirmed.checked,
            reasoningEffort: root.nativeProvider ? "" : root.reasoningCodes[reasoning.currentIndex],
            maxOutputTokens: maxTokens.text, timeout: timeoutField.text,
            parallelDocuments: parallelDocuments.text, maxParallelRequests: parallelRequests.text,
            apiKey: apiKey.text, rememberKey: rememberKey.checked, removeKey: root.removeSavedKey})
        if (saved) {
            apiKey.clear()
            root.keyVisible = false
            root.removeSavedKey = false
            loadConfig()
        }
        return saved
    }
    function openScanSettings() {
        root.page = 2
        root.pdfOptionsOpen = true
        scanSettingsScrollTimer.restart()
    }
    function syncMemoryLanguages() {
        var project = backend.memoryProject
        var projectId = project.id || ""
        if (projectId && projectId !== root.languageMemoryProjectId) {
            var sourceIndex = root.sourceCodes.indexOf(project.sourceLanguage)
            var targetIndex = root.targetCodes.indexOf(project.targetLanguage)
            if (sourceIndex >= 0) sourceLanguage.currentIndex = sourceIndex
            if (targetIndex >= 0) targetLanguage.currentIndex = targetIndex
        }
        root.languageMemoryProjectId = projectId
    }
    function scrollToScanConverter() {
        if (root.page !== 2 || (!scanConverterPath.activeFocus && !scanProvider.activeFocus))
            return
        var flickable = settingsScroll.contentItem
        var target = scanConverterPath.activeFocus ? scanConverterPath : scanProvider
        var position = target.mapToItem(flickable.contentItem, 0, 0)
        flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, position.y - 48))
    }
    function loadConfig() {
        root.providerPresetId = backend.settings.providerPreset || "custom"
        baseUrl.text = backend.settings.baseUrl || "https://api.openai.com/v1"
        modelField.text = backend.settings.model || ""
        reviewModel.text = backend.settings.reviewModel || ""
        babeldocPath.text = backend.settings.babeldocPath || ""
        scanConverterPath.text = backend.settings.scanConverterPath || ""
        scanProvider.currentIndex = Math.max(0, root.scanProviderCodes.indexOf(backend.settings.scanProvider || "auto"))
        abbyyPath.text = backend.settings.abbyyPath || ""
        abbyyExtendedCliConfirmed.checked = !!backend.settings.abbyyExtendedCliConfirmed
        apiMode.currentIndex = Math.max(0, apiMode.model.indexOf(backend.settings.apiMode || "chat"))
        responseFormat.currentIndex = Math.max(0, responseFormat.model.indexOf(backend.settings.responseFormat || "auto"))
        reasoning.currentIndex = Math.max(0, root.reasoningCodes.indexOf(backend.settings.reasoningEffort || ""))
        timeoutField.text = String(backend.settings.timeout || 180)
        maxTokens.text = String(backend.settings.maxOutputTokens || 0)
        parallelDocuments.text = String(backend.settings.parallelDocuments || 2)
        parallelRequests.text = String(backend.settings.maxParallelRequests || 8)
        requestsPerMinute.text = String(backend.settings.requestsPerMinute || 0)
    }

    function clearDraftKey() {
        apiKey.clear()
        root.keyVisible = false
        root.removeSavedKey = false
    }

    function applyProviderPreset(identifier) {
        var preset = backend.providerPresets.find(function(item) { return item.id === identifier })
        if (!preset || backend.busy) return
        root.providerPresetId = identifier
        root.clearDraftKey()
        backend.clearModels()
        if (identifier === "custom") return
        baseUrl.text = preset.baseUrl
        apiMode.currentIndex = Math.max(0, apiMode.model.indexOf(preset.apiMode))
        modelField.text = preset.model || ""
        reviewModel.text = preset.reviewModel || ""
        responseFormat.currentIndex = Math.max(0, responseFormat.model.indexOf(preset.responseFormat || "auto"))
        reasoning.currentIndex = Math.max(0, root.reasoningCodes.indexOf(preset.reasoningEffort || ""))
        maxTokens.text = String(preset.maxOutputTokens || 0)
        parallelRequests.text = String(preset.maxParallelRequests || 1)
        parallelDocuments.text = String(preset.parallelDocuments || 1)
        requestsPerMinute.text = String(preset.requestsPerMinute || 0)
    }

    function duration(seconds) {
        seconds = Math.max(0, Math.floor(seconds))
        return Math.floor(seconds / 60) + ":" + (seconds % 60 < 10 ? "0" : "") + seconds % 60
    }

    function reviewEmptyTitle() {
        if (backend.reviewSummary.loading) return qsTr("Завантажуємо звіт…")
        if (backend.reviewSummary.error) return qsTr("Не вдалося відкрити перевірку")
        if (!backend.reviewDocument.id) return qsTr("Виберіть документ")
        if (!backend.reviewSummary.hasResult) return qsTr("Перекладу ще немає")
        if (backend.reviewSearch.length > 0) return qsTr("Нічого не знайдено")
        if (backend.reviewFilter === "issues" && backend.reviewSummary.accepted > 0) return qsTr("Немає відкритих зауважень")
        if (backend.reviewFilter === "issues") return qsTr("Автоматичних зауважень не знайдено")
        return qsTr("Текст для перевірки недоступний")
    }

    function reviewEmptyDescription() {
        if (backend.reviewSummary.loading) return qsTr("Готуємо текст і зауваження до документа.")
        if (backend.reviewSummary.error) return backend.reviewSummary.error
        if (!backend.reviewDocument.id) return qsTr("Виберіть документ зі списку або відкрийте перевірку з історії.")
        if (!backend.reviewSummary.hasResult) return qsTr("Результати перевірки з’являться після перекладу цього документа.")
        if (backend.reviewSearch.length > 0) return qsTr("Спробуйте інші слова або очистьте пошук.")
        if (backend.reviewFilter === "issues" && backend.reviewSummary.accepted > 0) return qsTr("Позначені як коректні записи залишаються в режимі «Увесь текст». Там можна повернути зауваження до перевірки.")
        if (backend.reviewFilter === "issues") return qsTr("Перегляньте весь текст і верстку документа, щоб перевірити зміст, термінологію та оформлення.")
        return qsTr("Відкрийте переклад або звіт, щоб перевірити документ.")
    }

    function canMoveQueue(index, delta) {
        for (var i = index + delta; i >= 0 && i < backend.documents.length; i += delta)
            if (backend.documents[i].canMove) return true
        return false
    }

    function languageName(code) {
        if (code === "auto") return qsTr("Авто")
        var index = root.targetCodes.indexOf(code)
        return index >= 0 ? root.languageLabels[index] : code || "—"
    }

    function historyDate(value) {
        if (!value) return qsTr("Дата невідома")
        var date = new Date(value)
        return isNaN(date.getTime()) ? qsTr("Дата невідома") : Qt.formatDateTime(date, "dd.MM.yyyy, HH:mm")
    }

    function historyStatus(entry) {
        if (entry.status === "done") return entry.warningCount > 0 ? qsTr("Готово із зауваженнями") : qsTr("Готово")
        if (entry.status === "error" || entry.status === "failed") return qsTr("Помилка")
        if (entry.status === "cancelled" || entry.status === "canceled") return qsTr("Зупинено")
        if (entry.status === "interrupted") return qsTr("Перервано")
        return qsTr("Завершено")
    }

    function historyColor(entry, background) {
        if (entry.status === "error" || entry.status === "failed") return background ? Theme.dangerFill : Theme.danger
        if (entry.warningCount > 0) return background ? Theme.warningFill : Theme.warning
        if (entry.status === "done") return background ? Theme.successFill : Theme.success
        return background ? Theme.disabled : Theme.muted
    }
}
