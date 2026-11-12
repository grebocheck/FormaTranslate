import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    spacing: 14
    ScrollView {
        id: settingsScroll
        objectName: "settingsScroll"
        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(880, settingsScroll.availableWidth); x: (settingsScroll.availableWidth - width) / 2; spacing: 16
            PageControls.Panel {
                Layout.fillWidth: true; implicitHeight: connectionBody.implicitHeight + 40
                ColumnLayout {
                    id: connectionBody
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 10
                    PageControls.Heading { text: qsTr("Підключення до AI") }
                    PageControls.Caption { text: qsTr("Оберіть готове підключення або задайте власне. Збережіть налаштування перед перекладом."); wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.bottomMargin: 4 }
                    PageControls.Caption { text: qsTr("Провайдер і пресет") }
                    PageControls.Select {
                        id: providerPreset; objectName: "providerPresetSelector"
                        Layout.fillWidth: true
                        model: backend.providerPresets.map(function(item) { return item.id })
                        optionLabels: backend.providerPresets.map(function(item) { return item.label })
                        currentIndex: Math.max(0, model.indexOf(page.providerPresetId))
                        enabled: !backend.busy && !backend.modelsBusy
                        onActivated: function(index) { page.applyProviderPreset(model[index]) }
                        Accessible.name: qsTr("Провайдер і пресет")
                    }
                    PageControls.Caption { objectName: "providerPresetDescription"; text: page.activeProviderPreset.description || ""; visible: text.length > 0; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.bottomMargin: 6 }
                    PageControls.Caption { text: qsTr("API-ключ"); Layout.topMargin: 8 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        PageControls.Field {
                            id: apiKey; objectName: "apiKeyField"; Layout.fillWidth: true
                            echoMode: page.keyVisible ? TextInput.Normal : TextInput.Password
                            placeholderText: page.removeSavedKey ? qsTr("Ключ буде видалено після збереження") : page.savedEndpointSelected && backend.settings.keyPresent ? qsTr("Ключ задано. Введіть новий, щоб замінити") : qsTr("Вставте API-ключ")
                            enabled: !backend.busy; Accessible.name: qsTr("API-ключ")
                            onTextEdited: { if (text.length > 0) page.removeSavedKey = false; backend.clearModels() }
                        }
                        PageControls.Action { text: page.keyVisible ? qsTr("Приховати") : qsTr("Показати"); iconName: page.keyVisible ? "eye-off" : "eye"; enabled: apiKey.text.length > 0; onClicked: page.keyVisible = !page.keyVisible }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        PageControls.Toggle { id: rememberKey; objectName: "rememberKey"; text: qsTr("Запам’ятати ключ на цьому комп’ютері"); checked: true; enabled: !backend.busy }
                        Item { Layout.fillWidth: true }
                        PageControls.Action {
                            text: page.removeSavedKey ? qsTr("Скасувати видалення") : qsTr("Видалити ключ"); iconName: page.removeSavedKey ? "undo" : "trash"; subtle: true; danger: !page.removeSavedKey; implicitHeight: 30
                            visible: page.savedEndpointSelected && !!backend.settings.keyPresent; enabled: !backend.busy
                            onClicked: { page.removeSavedKey = !page.removeSavedKey; apiKey.clear(); page.keyVisible = false }
                        }
                    }
                    PageControls.Caption {
                        objectName: "credentialStatusCaption"
                        text: {
                            if (page.removeSavedKey) return qsTr("Ключ буде видалено після натискання «Зберегти».")
                            if (page.localConnectionPreset && !apiKey.text.trim().length && !(page.savedEndpointSelected && backend.settings.keyPresent))
                                return qsTr("Залиште поле порожнім, якщо локальний сервер не вимагає ключа.")
                            if (!page.savedEndpointSelected) return qsTr("Ключ іншого сервісу не переноситься.")
                            return backend.settings.credentialStatus || qsTr("Ключ не відображається після збереження.")
                        }
                        wrapMode: Text.WordWrap; Layout.fillWidth: true
                        color: page.removeSavedKey ? Theme.warning : shell.muted
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 16; Layout.topMargin: 6
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.preferredWidth: 3; spacing: 7
                            PageControls.Caption { text: qsTr("Адреса API") }
                            PageControls.Field { id: baseUrl; objectName: "baseUrlField"; Layout.fillWidth: true; placeholderText: "https://api.openai.com/v1"; enabled: !backend.busy; Accessible.name: qsTr("Адреса API"); onTextChanged: backend.clearModels(); onTextEdited: { page.providerPresetId = "custom"; page.clearDraftKey() } }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.preferredWidth: 2; spacing: 7
                            PageControls.Caption { text: qsTr("Модель перекладу") }
                            PageControls.ModelPicker { id: modelField; objectName: "modelField"; Layout.fillWidth: true; placeholderText: qsTr("Виберіть або введіть назву"); enabled: !backend.busy; Accessible.name: qsTr("Модель перекладу") }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12; Layout.topMargin: 4
                        PageControls.Caption { text: backend.modelsBusy ? qsTr("Отримуємо доступні моделі…") : backend.modelsMessage || qsTr("Оновіть список моделей із сервісу або введіть назву вручну."); objectName: "modelsMessage"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        PageControls.Action { text: backend.modelsBusy ? qsTr("Оновлюємо…") : qsTr("Оновити моделі"); iconName: "refresh"; objectName: "refreshModelsButton"; implicitHeight: 36; enabled: !backend.busy && !backend.modelsBusy && !page.removeSavedKey && baseUrl.text.trim().length > 0; onClicked: backend.refreshModels({baseUrl: baseUrl.text, apiKey: apiKey.text, apiMode: apiMode.currentText}) }
                    }
                    PageControls.Divider { Layout.topMargin: 9 }
                    PageControls.Action { text: qsTr("Додаткові параметри підключення"); iconName: page.connectionOptionsOpen ? "chevron-down" : "chevron-right"; subtle: true; implicitHeight: 32; Layout.leftMargin: -10; onClicked: page.connectionOptionsOpen = !page.connectionOptionsOpen }
                    ColumnLayout {
                        visible: page.connectionOptionsOpen; Layout.fillWidth: true; spacing: 10
                        PageControls.Caption { text: qsTr("Окрема модель редактора") }
                        PageControls.ModelPicker { id: reviewModel; objectName: "reviewModelField"; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — та сама модель"); enabled: !backend.busy; Accessible.name: qsTr("Окрема модель редактора") }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 16
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Протокол API") } PageControls.Select { id: apiMode; objectName: "apiModeSelector"; model: ["chat", "responses", "gemini", "anthropic"]; Layout.fillWidth: true; enabled: !backend.busy; onActivated: { page.providerPresetId = "custom"; backend.clearModels(); if (page.nativeProvider) reasoning.currentIndex = 0 } } }
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Формат відповіді") } PageControls.Select { id: responseFormat; model: ["auto", "strict", "json", "plain"]; Layout.fillWidth: true; enabled: !backend.busy } }
                        }

                    }
                }
            }
            PageControls.Panel {
                Layout.fillWidth: true; implicitHeight: appearanceBody.implicitHeight + 40
                ColumnLayout {
                    id: appearanceBody
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 16
                    PageControls.Heading { text: qsTr("Вигляд") }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 20
                        PageControls.LabelText { text: qsTr("Мова інтерфейсу"); Layout.fillWidth: true }
                        PageControls.Select {
                            id: interfaceLanguage; objectName: "interfaceLanguageSelector"
                            Layout.preferredWidth: 250
                            model: shell.uiLanguageCodes
                            optionLabels: shell.uiLanguageCodes.map(function(code) { var language = backend.uiLanguages.find(function(item) { return item.code === code }); return language ? language.label : code })
                            currentIndex: shell.uiLanguageCodes.indexOf(backend.uiLanguage)
                            enabled: !backend.busy
                            onActivated: function(index) { backend.setUiLanguage(shell.uiLanguageCodes[index]) }
                            Accessible.name: qsTr("Мова інтерфейсу")
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 20
                        PageControls.LabelText { text: qsTr("Тема"); Layout.fillWidth: true }
                        PageControls.Select {
                            objectName: "interfaceThemeSelector"; Layout.preferredWidth: 250
                            model: shell.themeCodes; optionLabels: shell.themeLabels
                            currentIndex: shell.themeCodes.indexOf(Theme.preference)
                            onActivated: function(index) { backend.setUiTheme(shell.themeCodes[index]) }
                            Accessible.name: qsTr("Тема")
                        }
                    }
                    PageControls.Caption { text: qsTr("Використовує оформлення операційної системи та змінюється разом із ним."); visible: Theme.preference === "system"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
            }
            PageControls.Panel {
                Layout.fillWidth: true; implicitHeight: performanceBody.implicitHeight + 36
                ColumnLayout {
                    id: performanceBody
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                    RowLayout {
                        PageControls.Heading { text: qsTr("Швидкість і ліміти") }
                        Item { Layout.fillWidth: true }
                        PageControls.Caption { text: qsTr("%1 документи · %2 запити").arg(parallelDocuments.text || "2").arg(parallelRequests.text || "8") }
                        PageControls.Action { text: page.performanceOptionsOpen ? qsTr("Згорнути") : qsTr("Змінити"); iconName: page.performanceOptionsOpen ? "chevron-down" : "settings"; objectName: "performanceOptionsButton"; subtle: true; implicitHeight: 32; onClicked: page.performanceOptionsOpen = !page.performanceOptionsOpen }
                    }
                    PageControls.Caption { text: Number(requestsPerMinute.text) > 0 ? qsTr("Не більше %1 запитів за хвилину. Ліміти провайдера можуть бути нижчими.").arg(requestsPerMinute.text) : qsTr("Кількість паралельних запитів впливає на швидкість і навантаження на API."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    ColumnLayout {
                        visible: page.performanceOptionsOpen; Layout.fillWidth: true; spacing: 12
                        RowLayout {
                            Layout.fillWidth: true; spacing: 16
                            ColumnLayout { visible: !page.nativeProvider; Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Рівень міркування") } PageControls.Select { id: reasoning; objectName: "reasoningSelect"; model: shell.reasoningCodes; optionLabels: shell.reasoningLabels; Layout.fillWidth: true; enabled: !backend.busy } }
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Очікування відповіді, с") } PageControls.Field { id: timeoutField; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; enabled: !backend.busy } }
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Ліміт вихідних токенів") } PageControls.Field { id: maxTokens; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; enabled: !backend.busy } }
                        }
                        PageControls.Caption { text: qsTr("0 — автоматично. Підтримка цих параметрів залежить від моделі."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 16
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Документів одночасно, 1–4") } PageControls.Field { id: parallelDocuments; objectName: "parallelDocumentsField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 1; top: 4 } enabled: !backend.busy } }
                            ColumnLayout { Layout.fillWidth: true; Layout.preferredWidth: 1; PageControls.Caption { text: qsTr("Запитів до API загалом, 1–16") } PageControls.Field { id: parallelRequests; objectName: "parallelRequestsField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 1; top: 16 } enabled: !backend.busy } }
                        }
                        PageControls.Caption { text: qsTr("Запитів за хвилину, 0 — без обмеження") }
                        PageControls.Field { id: requestsPerMinute; objectName: "requestsPerMinuteField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhDigitsOnly; validator: IntValidator { bottom: 0; top: 6000 } enabled: !backend.busy }
                        PageControls.Caption { text: qsTr("Ліміт запитів спільний для всіх документів. Більше паралельних запитів допомагає, доки сервіс не обмежує їхню частоту."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
            PageControls.Panel {
                Layout.fillWidth: true; implicitHeight: pdfBody.implicitHeight + 36
                ColumnLayout {
                    id: pdfBody
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                    RowLayout {
                        PageControls.Heading { text: qsTr("Переклад PDF") }
                        Item { Layout.fillWidth: true }
                        PageControls.Caption { text: backend.settings.pdfReady ? qsTr("Рушій знайдено") : qsTr("Рушій не знайдено"); color: backend.settings.pdfReady ? Theme.success : shell.muted }
                        PageControls.Action { text: page.pdfOptionsOpen ? qsTr("Згорнути") : qsTr("Налаштувати"); iconName: page.pdfOptionsOpen ? "chevron-down" : "settings"; subtle: true; implicitHeight: 30; onClicked: page.pdfOptionsOpen = !page.pdfOptionsOpen }
                    }
                    PageControls.Caption { text: qsTr("PDF із текстом перекладається зі збереженням макета. Скановані сторінки програма підготує через OCR під час перекладу."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    ColumnLayout {
                        visible: page.pdfOptionsOpen; Layout.fillWidth: true; spacing: 8
                        PageControls.Caption { text: qsTr("Шлях до BabelDOC") }
                        PageControls.Field { id: babeldocPath; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                        AppButton { text: qsTr("Як налаштувати PDF та OCR"); iconName: "external"; subtle: true; leftAligned: true; leftPadding: 0; onClicked: Qt.openUrlExternally("https://github.com/grebocheck/FormaTranslate/blob/main/docs/PDF.md") }
                            PageControls.Divider { Layout.topMargin: 8; Layout.bottomMargin: 4 }
                        PageControls.Caption { text: qsTr("Розпізнавання сканів") }
                        PageControls.Select {
                            id: scanProvider; objectName: "scanProviderSelector"
                            model: shell.scanProviderCodes; optionLabels: shell.scanProviderLabels
                            Layout.fillWidth: true; enabled: !backend.busy
                            Accessible.name: qsTr("Розпізнавання сканів")
                        }
                        PageControls.Caption { text: qsTr("Автоматично: спочатку локальний OCR, інакше встановлений ABBYY. Звичайний FineReader потребує ручного експорту DOCX."); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        PageControls.Caption { text: qsTr("Шлях до ABBYY FineReader"); visible: scanProvider.currentIndex !== 1 }
                        PageControls.Field { id: abbyyPath; objectName: "abbyyPathField"; visible: scanProvider.currentIndex !== 1; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                        PageControls.Caption { objectName: "abbyyDetectionStatus"; text: backend.settings.abbyyReady ? qsTr("ABBYY FineReader знайдено") + (backend.settings.abbyyDetectedPath ? "\n" + backend.settings.abbyyDetectedPath : "") : qsTr("ABBYY FineReader не знайдено"); visible: scanProvider.currentIndex !== 1; color: backend.settings.abbyyReady ? Theme.success : Theme.muted; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        PageControls.Toggle { id: abbyyExtendedCliConfirmed; objectName: "abbyyExtendedCliConfirmed"; visible: scanProvider.currentIndex === 3; text: qsTr("Моя ліцензія ABBYY дозволяє Extended CLI"); enabled: !backend.busy }
                        PageControls.Caption { text: qsTr("Автоматичний експорт доступний лише з відповідною ліцензією Extended CLI. Звичайна або Corporate-ліцензія сама по собі цього не підтверджує."); visible: scanProvider.currentIndex === 3; color: Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        PageControls.Caption { text: qsTr("Шлях до OCR-конвертера"); visible: page.showLocalOcrSettings }
                            PageControls.Field { id: scanConverterPath; objectName: "scanConverterPathField"; visible: page.showLocalOcrSettings; Layout.fillWidth: true; placeholderText: qsTr("Порожньо — знайти автоматично"); enabled: !backend.busy }
                            PageControls.Caption { objectName: "scanConverterStatus"; visible: page.showLocalOcrSettings; text: backend.settings.scanConverterReady ? qsTr("OCR-конвертер знайдено") : qsTr("OCR-конвертер не знайдено"); color: backend.settings.scanConverterReady ? Theme.success : Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            PageControls.Caption { text: qsTr("Потрібен окремий локальний OCR-конвертер. API-модель для розпізнавання не використовується."); visible: page.showLocalOcrSettings; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PageControls.Caption { text: qsTr("Версія %1").arg(backend.appVersion) + "\nQt/PySide6 · LGPLv3"; font.pixelSize: 10 }
        PageControls.Caption { text: backend.apiStatus; Layout.fillWidth: true; elide: Text.ElideRight }
        PageControls.Action { text: qsTr("Перевірити підключення"); iconName: "connect"; objectName: "testConnectionButton"; enabled: !backend.busy; onClicked: { if (page.saveConfig()) backend.testConnection() } ToolTip.visible: hovered; ToolTip.text: qsTr("Зберігає налаштування й надсилає короткий запит до моделі") }
        PageControls.Action { text: qsTr("Зберегти"); iconName: "save"; objectName: "saveSettingsButton"; primary: true; enabled: !backend.busy; onClicked: page.saveConfig() }
    }

    property bool connectionOptionsOpen: false
    property bool pdfOptionsOpen: false
    property bool performanceOptionsOpen: false
    property bool removeSavedKey: false
    property bool keyVisible: false
    property string providerPresetId: "custom"
    readonly property var activeProviderPreset: backend.providerPresets.find(function(item) { return item.id === page.providerPresetId }) || ({})
    readonly property bool localConnectionPreset: page.providerPresetId === "lmstudio" || page.providerPresetId === "ollama"
    readonly property bool nativeProvider: apiMode.currentText === "gemini" || apiMode.currentText === "anthropic"
    readonly property bool savedEndpointSelected: baseUrl.text.trim().replace(/\/+$/, "") === (backend.settings.baseUrl || "").trim().replace(/\/+$/, "")
    readonly property bool showLocalOcrSettings: scanProvider.currentIndex === 1 || (scanProvider.currentIndex === 0 && (scanConverterPath.text.trim().length > 0 || backend.settings.scanConverterReady || !backend.settings.abbyyReady))
    Timer {
        id: scanSettingsScrollTimer
        interval: 1
        onTriggered: {
            (page.showLocalOcrSettings ? scanConverterPath : scanProvider).forceActiveFocus()
            page.scrollToScanConverter()
        }
    }
    Connections {
        target: backend
        function onSettingsSaved() {
            apiKey.clear()
            page.keyVisible = false
            page.removeSavedKey = false
            page.loadConfig()
        }
    }
    Connections {
        target: settingsScroll.contentItem
        function onContentHeightChanged() {
            if (scanConverterPath.activeFocus || scanProvider.activeFocus)
                Qt.callLater(page.scrollToScanConverter)
        }
    }

    function saveConfig() {
        var saved = backend.saveSettings({baseUrl: baseUrl.text, model: modelField.text, reviewModel: reviewModel.text,
            providerPreset: page.providerPresetId, requestsPerMinute: requestsPerMinute.text,
            apiMode: apiMode.currentText, responseFormat: responseFormat.currentText, babeldocPath: babeldocPath.text, scanConverterPath: scanConverterPath.text,
            scanProvider: shell.scanProviderCodes[scanProvider.currentIndex], abbyyPath: abbyyPath.text, abbyyExtendedCliConfirmed: abbyyExtendedCliConfirmed.checked,
            reasoningEffort: page.nativeProvider ? "" : shell.reasoningCodes[reasoning.currentIndex],
            maxOutputTokens: maxTokens.text, timeout: timeoutField.text,
            parallelDocuments: parallelDocuments.text, maxParallelRequests: parallelRequests.text,
            apiKey: apiKey.text, rememberKey: rememberKey.checked, removeKey: page.removeSavedKey})
        return saved
    }

    function scrollToScanConverter() {
        if (shell.page !== 2 || (!scanConverterPath.activeFocus && !scanProvider.activeFocus))
            return
        var flickable = settingsScroll.contentItem
        var target = scanConverterPath.activeFocus ? scanConverterPath : scanProvider
        var position = target.mapToItem(flickable.contentItem, 0, 0)
        flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, position.y - 48))
    }

    function loadConfig() {
        page.providerPresetId = backend.settings.providerPreset || "custom"
        baseUrl.text = backend.settings.baseUrl || "https://api.openai.com/v1"
        modelField.text = backend.settings.model || ""
        reviewModel.text = backend.settings.reviewModel || ""
        babeldocPath.text = backend.settings.babeldocPath || ""
        scanConverterPath.text = backend.settings.scanConverterPath || ""
        scanProvider.currentIndex = Math.max(0, shell.scanProviderCodes.indexOf(backend.settings.scanProvider || "auto"))
        abbyyPath.text = backend.settings.abbyyPath || ""
        abbyyExtendedCliConfirmed.checked = !!backend.settings.abbyyExtendedCliConfirmed
        apiMode.currentIndex = Math.max(0, apiMode.model.indexOf(backend.settings.apiMode || "chat"))
        responseFormat.currentIndex = Math.max(0, responseFormat.model.indexOf(backend.settings.responseFormat || "auto"))
        reasoning.currentIndex = Math.max(0, shell.reasoningCodes.indexOf(backend.settings.reasoningEffort || ""))
        timeoutField.text = String(backend.settings.timeout || 180)
        maxTokens.text = String(backend.settings.maxOutputTokens || 0)
        parallelDocuments.text = String(backend.settings.parallelDocuments || 2)
        parallelRequests.text = String(backend.settings.maxParallelRequests || 8)
        requestsPerMinute.text = String(backend.settings.requestsPerMinute || 0)
    }

    function clearDraftKey() {
        apiKey.clear()
        page.keyVisible = false
        page.removeSavedKey = false
    }

    function applyProviderPreset(identifier) {
        var preset = backend.providerPresets.find(function(item) { return item.id === identifier })
        if (!preset || backend.busy) return
        page.providerPresetId = identifier
        page.clearDraftKey()
        backend.clearModels()
        if (identifier === "custom") return
        baseUrl.text = preset.baseUrl
        apiMode.currentIndex = Math.max(0, apiMode.model.indexOf(preset.apiMode))
        modelField.text = preset.model || ""
        reviewModel.text = preset.reviewModel || ""
        responseFormat.currentIndex = Math.max(0, responseFormat.model.indexOf(preset.responseFormat || "auto"))
        reasoning.currentIndex = Math.max(0, shell.reasoningCodes.indexOf(preset.reasoningEffort || ""))
        maxTokens.text = String(preset.maxOutputTokens || 0)
        parallelRequests.text = String(preset.maxParallelRequests || 1)
        parallelDocuments.text = String(preset.parallelDocuments || 1)
        requestsPerMinute.text = String(preset.requestsPerMinute || 0)
    }

    function openScanSettings() {
        page.pdfOptionsOpen = true
        scanSettingsScrollTimer.restart()
    }
}
