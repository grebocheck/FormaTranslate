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
    property alias sourceLanguage: documentsPage.sourceLanguage
    property alias targetLanguage: documentsPage.targetLanguage
    property alias domain: documentsPage.domain
    property int page: 0
    onPageChanged: {
        if (page === 1) backend.activateReview()
        if (page === 2) backend.refreshWorkerAvailability()
        if (page === 1 || page === 3) backend.refreshFileAvailability()
    }
    property bool translationOptionsOpen: false
    property bool previewOpen: false
    property alias connectionOptionsOpen: settingsPage.connectionOptionsOpen
    property alias pdfOptionsOpen: settingsPage.pdfOptionsOpen
    property bool pdfPreparationOptionsOpen: false
    property alias performanceOptionsOpen: settingsPage.performanceOptionsOpen
    property alias removeSavedKey: settingsPage.removeSavedKey
    property alias keyVisible: settingsPage.keyVisible
    property alias providerPresetId: settingsPage.providerPresetId
    property alias activeProviderPreset: settingsPage.activeProviderPreset
    property alias nativeProvider: settingsPage.nativeProvider
    property alias savedEndpointSelected: settingsPage.savedEndpointSelected
    property double clockNow: Date.now()
    readonly property bool hasPdf: backend.pendingHasPdf
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
    readonly property bool hasCompleted: backend.hasCompletedDocuments
    readonly property int reviewEntryIndex: backend.reviewEntries.findIndex(function(entry) { return entry.id === backend.selectedReviewId })
    readonly property bool reviewLocked: backend.reviewEditing || backend.reviewSaving || backend.memoryWorking
    property string languageMemoryProjectId: ""
    property var themeCodes: ["system", "light", "dark"]
    property var themeLabels: [qsTr("Як у системі"), qsTr("Світла"), qsTr("Темна")]
    property var scanProviderCodes: ["auto", "forma", "abbyy", "abbyy_extended"]
    property var scanProviderLabels: [qsTr("Автоматично"), qsTr("Вбудований / локальний OCR"), qsTr("ABBYY FineReader — вручну"), qsTr("ABBYY Extended CLI")]
    property alias showLocalOcrSettings: settingsPage.showLocalOcrSettings
    readonly property bool manualAbbyy: backend.settings.scanProviderEffective === "abbyy" || backend.settings.scanProvider === "abbyy"
    readonly property var currentAbbyyHandoff: typeof backend.abbyyHandoff === "object" && backend.abbyyHandoff ? backend.abbyyHandoff : ({})
    readonly property bool preparingDocuments: backend.recognizingScan || backend.translationPreparing
    readonly property bool canImportAbbyyResult: !backend.busy || (!!currentAbbyyHandoff.continueTranslation && !currentAbbyyHandoff.importing)
    property string abbyyPromptId: ""

    onClosing: function(close) { close.accepted = backend.requestClose() }
    Component.onCompleted: { loadConfig(); syncMemoryLanguages(); backend.usage.refresh() }
    Shortcut { sequences: [StandardKey.Open]; enabled: root.page !== 4 && backend.canAddDocuments && !root.reviewLocked; onActivated: backend.pickDocuments() }
    Timer { interval: 1000; repeat: true; running: backend.translating; onTriggered: root.clockNow = Date.now() }

    Timer { interval: 5000; repeat: true; running: root.visible; onTriggered: backend.usage.refresh() }
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
    function showAbbyyHandoffDialog() { abbyyHandoffDialog.open() }
    function showMemoryDialog() { memoryDialog.open() }
    function showInstructionsDialog() { instructionsDialog.open() }
    function showRememberWordingDialog() { rememberWordingDialog.open() }
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
            PageControls.LabelText { text: "Forma"; font.pixelSize: 21; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            PageControls.Caption {
                text: backend.reviewEditing ? qsTr("Редагування перекладу") : backend.recognizingScan ? backend.scanMessage : backend.paused ? qsTr("На паузі") : backend.translating ? backend.queueSummary : backend.busy ? qsTr("Виконуємо завдання…") : backend.settings.model || qsTr("Підключення не налаштовано")
                color: backend.busy ? root.accent : root.muted
                elide: Text.ElideMiddle; Layout.maximumWidth: 300
            }
            PageControls.Action {
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
            PageControls.Action {
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
            PageControls.NavigationTab { text: qsTr("Документи"); iconName: "translate"; objectName: "translationTab" }
            PageControls.NavigationTab { text: qsTr("Текст і фото"); iconName: "image-text"; objectName: "quickTranslationTab" }
            PageControls.NavigationTab { text: qsTr("Перевірка"); iconName: "review"; objectName: "reviewTab" }
            PageControls.NavigationTab { text: qsTr("Історія"); iconName: "history"; objectName: "historyTab" }
            PageControls.NavigationTab { text: qsTr("Налаштування"); iconName: "settings"; objectName: "settingsButton" }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 16
        RowLayout {
            Layout.fillWidth: true; spacing: 16
            PageControls.LabelText { text: root.titles[root.page]; font.pixelSize: 26; font.weight: Font.Medium; Layout.fillWidth: true }
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
                PageControls.LabelText { text: backend.message; color: backend.messageKind === "error" ? Theme.danger : root.ink; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 13 }
                PageControls.Action { text: ""; iconName: "close"; hint: qsTr("Закрити повідомлення"); subtle: true; implicitWidth: 30; implicitHeight: 30; onClicked: backend.dismissMessage(); Accessible.name: hint }
            }
        }
        StackLayout {
            currentIndex: root.page; Layout.fillWidth: true; Layout.fillHeight: true

            DocumentsPage { id: documentsPage; shell: root }

            ReviewPage { id: reviewPage; shell: root }

            SettingsPage { id: settingsPage; shell: root }

            HistoryPage { id: historyPage; shell: root }
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
        PageControls.Caption { text: root.page === 4 ? qsTr("Текст і фото для перекладу надсилаються вибраному AI-сервісу.") : qsTr("Документи зберігаються локально. Текст для перекладу надсилається вибраному AI-сервісу."); font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
    }

    function saveConfig() { return settingsPage.saveConfig() }
    function openScanSettings() {
        root.page = 2
        settingsPage.openScanSettings()
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
    function scrollToScanConverter() { settingsPage.scrollToScanConverter() }
    function loadConfig() { settingsPage.loadConfig() }

    function clearDraftKey() { settingsPage.clearDraftKey() }

    function applyProviderPreset(identifier) { settingsPage.applyProviderPreset(identifier) }

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
        var count = backend.documentCount
        return index >= 0 && index < count && backend.canMoveQueue(index, delta)
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
