import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    property alias domain: domain
    property alias targetLanguage: targetLanguage
    property alias sourceLanguage: sourceLanguage
    property alias review: review
    property alias autoGlossary: autoGlossary
    readonly property var document: backend.selectedDocument
    readonly property bool hasDocument: !!document.id
    readonly property bool isPdf: document.format === "PDF"
    readonly property bool hasPagePreview: hasDocument && backend.documentCapabilities(document.format).page_preview
    readonly property bool hasPdfTools: isPdf && !document.history && document.status !== "done" || backend.recognizingScan
    readonly property bool hasPreview: !hasPagePreview && backend.segments.length > 0
    readonly property bool hasTranslation: hasPreview && document.status === "done"
    readonly property string previewDocumentId: document.id || ""
    readonly property string memoryProjectId: backend.memoryProject.id || ""
    property bool optionsRestored: false
    onPreviewDocumentIdChanged: previewList.positionViewAtBeginning()
    onMemoryProjectIdChanged: if (optionsRestored) Qt.callLater(page.syncProjectOptions)
    Component.onCompleted: {
        var options = backend.workspaceOptions
        sourceLanguage.currentIndex = Math.max(0, shell.sourceCodes.indexOf(options.sourceLanguage))
        targetLanguage.currentIndex = Math.max(0, shell.targetCodes.indexOf(options.targetLanguage))
        domain.currentIndex = Math.max(0, shell.domainValues.indexOf(options.domain))
        review.checked = options.review
        autoGlossary.checked = options.autoGlossary
        optionsRestored = true
        // Main applies a selected memory project's language pair after child creation.
        Qt.callLater(page.syncProjectOptions)
    }
    spacing: 12

    function saveWorkspaceOptions() {
        if (!optionsRestored) return
        backend.setWorkspaceOptions({
            sourceLanguage: shell.sourceCodes[sourceLanguage.currentIndex],
            targetLanguage: shell.targetCodes[targetLanguage.currentIndex],
            domain: shell.domainValues[domain.currentIndex],
            review: review.checked,
            autoGlossary: autoGlossary.checked
        })
    }

    function syncProjectOptions() {
        var project = backend.memoryProject
        if (project.id) {
            var sourceIndex = shell.sourceCodes.indexOf(project.sourceLanguage)
            var targetIndex = shell.targetCodes.indexOf(project.targetLanguage)
            if (sourceIndex >= 0) sourceLanguage.currentIndex = sourceIndex
            if (targetIndex >= 0) targetLanguage.currentIndex = targetIndex
        }
        saveWorkspaceOptions()
    }

    PageControls.Panel {
        objectName: "documentTranslationCard"
        Layout.fillWidth: true; implicitHeight: translationSettings.implicitHeight + 20
        ColumnLayout {
            id: translationSettings
            anchors.fill: parent; anchors.margins: 10; spacing: 6
            RowLayout {
                id: translationControls; objectName: "documentTranslationControls"
                Layout.fillWidth: true; spacing: 8
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.minimumWidth: 60; spacing: 4
                    PageControls.Caption { text: qsTr("Мова оригіналу") }
                    PageControls.Select {
                        id: sourceLanguage; objectName: "sourceLanguage"
                        model: shell.sourceCodes; optionLabels: [qsTr("Автовизначення")].concat(shell.languageLabels)
                        Layout.fillWidth: true; implicitHeight: 36; enabled: !backend.busy && !backend.memoryProject.id
                        onActivated: page.saveWorkspaceOptions()
                        Accessible.name: qsTr("Мова оригіналу")
                    }
                }
                Icon { name: "chevron-right"; color: Theme.muted; Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.topMargin: 18 }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.minimumWidth: 60; spacing: 4
                    PageControls.Caption { text: qsTr("Перекласти на") }
                    PageControls.Select {
                        id: targetLanguage; objectName: "targetLanguage"
                        model: shell.targetCodes; optionLabels: shell.languageLabels; currentIndex: 0
                        Layout.fillWidth: true; implicitHeight: 36; enabled: !backend.busy && !backend.memoryProject.id
                        onActivated: page.saveWorkspaceOptions()
                        Accessible.name: qsTr("Перекласти на")
                    }
                }
                PageControls.Action {
                    objectName: "translationOptionsButton"; text: page.width >= 900 ? qsTr("Параметри") : ""; hint: qsTr("Параметри"); Accessible.name: hint; iconName: "settings"; implicitHeight: 36
                    Layout.topMargin: 18; enabled: !shell.reviewLocked
                    onClicked: translationOptions.open()
                }
                PageControls.Action {
                    objectName: "translateButton"
                    iconName: shell.preparingDocuments ? "document" : backend.translating ? (backend.paused ? "play" : "pause") : "translate"
                    text: shell.preparingDocuments ? qsTr("Підготовка…") : backend.translating ? (backend.paused ? qsTr("Продовжити") : qsTr("Пауза")) : (page.width >= 900 ? qsTr("Перекласти документи (%1)") : qsTr("Перекласти (%1)")).arg(backend.pendingDocumentCount)
                    primary: true; implicitHeight: 36; Layout.topMargin: 18
                    enabled: !shell.preparingDocuments && !shell.reviewLocked && (backend.translating || (!backend.busy && backend.pendingDocumentCount > 0 && !!backend.settings.model && !shell.needsSourceLanguage && !shell.sameLanguage))
                    onClicked: backend.translating ? backend.togglePause() : backend.start(shell.sourceCodes[sourceLanguage.currentIndex], shell.targetCodes[targetLanguage.currentIndex], domain.currentText, review.checked, autoGlossary.checked)
                }
                PageControls.Action { text: ""; hint: qsTr("Стоп"); Accessible.name: hint; iconName: "stop"; objectName: "cancelButton"; visible: backend.translating || backend.recognizingScan; implicitHeight: 36; Layout.topMargin: 18; onClicked: backend.cancel() }

            }
            PageControls.Caption {
                visible: shell.needsSourceLanguage || shell.sameLanguage
                text: shell.needsSourceLanguage ? qsTr("Для PDF виберіть мову оригіналу.") : qsTr("Виберіть іншу мову перекладу.")
                color: Theme.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            PageControls.Caption {
                visible: !!backend.memoryProject.id
                text: qsTr("Пам’ять проєкту") + ": " + (backend.memoryProject.name || "")
                Layout.fillWidth: true; elide: Text.ElideRight
            }

            RowLayout {
                visible: !backend.settings.model || backend.translating || !review.checked
                Layout.fillWidth: true; spacing: 8
                PageControls.Caption {
                    text: !backend.settings.model ? qsTr("Спочатку підключіть модель у налаштуваннях.") : backend.translating ? qsTr("Нові файли перекладатимуться з поточними параметрами цього запуску.") : qsTr("Редакторську перевірку вимкнено.")
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11
                }
                PageControls.Action { text: qsTr("Налаштувати підключення"); iconName: "settings"; subtle: true; implicitHeight: 28; visible: !backend.settings.model; onClicked: shell.page = 2 }
            }
        }
    }

    PageControls.Panel {
        objectName: "documentPreviewPanel"
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 140
        border.color: documentDrop.containsDrag ? Theme.accent : Theme.line
        DropArea {
            id: documentDrop; anchors.fill: parent
            enabled: backend.canAddDocuments && !shell.reviewLocked
            onDropped: function(event) { if (event.hasUrls) backend.addFiles(event.urls) }
        }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 8
            RowLayout {
                visible: (!!page.document.report && page.document.warningCount > 0) || !!page.document.diagnosticReport
                Layout.fillWidth: true; spacing: 8
                Item { Layout.fillWidth: true }
                PageControls.Action {
                    objectName: "reviewDocumentButton"
                    text: page.document.warningCount ? qsTr("Зауваження: %1").arg(page.document.warningCount) : qsTr("Перевірити переклад")
                    iconName: "review"; subtle: true; implicitHeight: 32
                    visible: !!page.document.report && !page.document.diagnosticReport
                    enabled: !shell.reviewLocked
                    onClicked: backend.setWorkspaceMode("review")
                }
                PageControls.Action {
                    text: qsTr("Виправити й продовжити"); objectName: "recoverTranslationButton"; iconName: "edit"; primary: true; implicitHeight: 34
                    visible: !!page.document.diagnosticReport
                    enabled: !backend.busy && !shell.reviewLocked
                    onClicked: backend.setWorkspaceMode("review")
                }
            }
            ScrollView {
                id: pdfScroll; objectName: "documentPdfOptionsScroll"
                visible: page.hasPdfTools
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(pdfOptions.implicitHeight, page.height * 0.4)
                clip: true; contentWidth: availableWidth
                ScrollBar.vertical: PageControls.ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    id: pdfOptions
                    width: pdfScroll.availableWidth - 12; spacing: 8
                    RowLayout {
                        objectName: "documentWorkflowInfo"
                        visible: !!page.document.workflowText && !backend.recognizingScan
                        Layout.fillWidth: true; spacing: 8
                        Icon { name: "info"; color: Theme.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.alignment: Qt.AlignTop }
                        PageControls.Caption { text: page.document.workflowText || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.25 }
                    }
                    RowLayout {
                        visible: backend.recognizingScan; Layout.fillWidth: true; spacing: 8
                        BusyIndicator { running: backend.recognizingScan; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                        PageControls.Caption { objectName: "scanStatusText"; text: backend.scanMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                    ProgressBar { objectName: "scanProgressBar"; visible: backend.recognizingScan; from: 0; to: 100; value: backend.scanProgress; Layout.fillWidth: true }
                    RowLayout {
                        visible: !backend.recognizingScan; Layout.fillWidth: true
                        PageControls.Action {
                            objectName: "pdfPreparationOptionsButton"
                            text: qsTr("Додаткові дії з PDF"); iconName: shell.pdfPreparationOptionsOpen ? "chevron-down" : "chevron-right"
                            subtle: true; implicitHeight: 30; leftPadding: 0; enabled: !backend.busy && !shell.reviewLocked
                            onClicked: shell.pdfPreparationOptionsOpen = !shell.pdfPreparationOptionsOpen
                        }
                        Item { Layout.fillWidth: true }
                        PageControls.Action {
                            objectName: "importAbbyyResultButton"
                            text: shell.currentAbbyyHandoff.continueTranslation ? qsTr("Імпортувати й продовжити") : qsTr("Імпортувати DOCX…")
                            iconName: "import"; implicitHeight: 32
                            visible: shell.currentAbbyyHandoff.sourceId === page.document.id && !!shell.currentAbbyyHandoff.sourceId
                            enabled: shell.canImportAbbyyResult; onClicked: shell.showAbbyyHandoffDialog()
                        }
                    }
                    ColumnLayout {
                        visible: shell.pdfPreparationOptionsOpen && !backend.recognizingScan
                        Layout.fillWidth: true; spacing: 6
                        PageControls.Caption { text: qsTr("Спосіб обробки цього PDF") }
                        AppSelect {
                            objectName: "documentPdfModeSelector"; Layout.fillWidth: true
                            model: ["auto", "ocr", "direct"]
                            optionLabels: [qsTr("Автоматично"), qsTr("Розпізнати через OCR перед перекладом"), qsTr("Прямий переклад PDF")]
                            currentIndex: Math.max(0, model.indexOf(page.document.pdfMode || "auto"))
                            enabled: !backend.busy && !shell.reviewLocked
                            onActivated: function(index) { backend.setDocumentPdfMode(page.document.id, model[index]) }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            PageControls.Action {
                                objectName: "recognizePdfButton"
                                text: shell.manualAbbyy ? qsTr("Відкрити в ABBYY…") : qsTr("Скан PDF → DOCX"); iconName: "document"; implicitHeight: 34
                                enabled: !backend.busy && !shell.reviewLocked && page.document.status !== "loading"
                                onClicked: backend.recognizeSelectedPdf(shell.sourceCodes[sourceLanguage.currentIndex])
                            }
                            Item { Layout.fillWidth: true }
                            PageControls.Action { objectName: "scanSettingsButton"; text: qsTr("Налаштувати OCR"); iconName: "settings"; subtle: true; implicitHeight: 32; enabled: !backend.busy; onClicked: shell.openScanSettings() }
                        }
                        PageControls.Caption {
                            text: shell.manualAbbyy ? qsTr("Збережіть розпізнаний документ як DOCX у FineReader, перевірте його та поверніть у чергу.") : qsTr("Створює DOCX локально й замінює PDF у черзі. Перевірте текст і таблиці перед перекладом.")
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                        }
                    }
                }
            }
            DocumentPreview {
                objectName: "documentPagePreview"
                visible: page.hasPagePreview
                active: visible && backend.workspaceMode === "translation" && shell.page === 0
                document: page.document
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0
            }
            RowLayout {
                visible: page.hasPreview; Layout.fillWidth: true; spacing: 24
                PageControls.Caption { text: qsTr("Оригінал"); Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold }
                PageControls.Caption { text: qsTr("Переклад"); visible: page.hasTranslation; Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold }
            }
            PageControls.Divider { visible: page.hasPreview }
            ListView {
                id: previewList; objectName: "documentPreviewList"
                visible: page.hasPreview; Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; spacing: 18; model: backend.segments
                ScrollBar.vertical: PageControls.ReadingScrollBar { }
                delegate: ColumnLayout {
                    id: segmentRow; required property var modelData
                    width: ListView.view.width - 12; spacing: 6
                    PageControls.Caption { text: segmentRow.modelData.location; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 24
                        PageControls.ReadOnlyText { text: segmentRow.modelData.source; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; Accessible.name: qsTr("Оригінал") }
                        PageControls.ReadOnlyText { text: segmentRow.modelData.translation; visible: page.hasTranslation; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; Accessible.name: qsTr("Переклад") }
                    }
                }
            }
            Item {
                visible: !page.hasPagePreview && !page.hasPreview && !page.hasPdfTools; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 24
                ColumnLayout {
                    anchors.centerIn: parent; width: Math.max(0, Math.min(440, parent.width - 24)); spacing: 10
                    BusyIndicator { visible: page.document.status === "loading"; running: visible; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                    PageControls.Caption {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                        text: !page.hasDocument ? qsTr("Додайте документи до сесії, щоб почати переклад.") : page.document.status === "loading" ? qsTr("Завантажуємо текст документа…") : page.isPdf ? qsTr("Перегляд PDF доступний у файлі оригіналу.") : qsTr("Попередній перегляд тексту недоступний.")
                    }
                    PageControls.Action {
                        objectName: "addSessionDocumentsButton"; text: qsTr("Додати файли"); iconName: "add"; primary: true
                        visible: !page.hasDocument; Layout.alignment: Qt.AlignHCenter
                        enabled: backend.canAddDocuments && !shell.reviewLocked; onClicked: backend.pickDocuments()
                    }
                }
            }
            PageControls.Caption {
                text: qsTr("З пам’яті: %1 · повтори: %2 · адаптовано: %3").arg(page.document.memorySegments || 0).arg(page.document.repeatedSegments || 0).arg(page.document.adaptedSegments || 0)
                visible: (page.document.memorySegments || 0) > 0 || (page.document.repeatedSegments || 0) > 0 || (page.document.adaptedSegments || 0) > 0
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
        }
    }

    Popup {
        id: translationOptions; objectName: "translationOptionsDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(600, parent.width - 40); height: Math.min(640, parent.height - 40)
        padding: 20; modal: true; focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        visible: shell.translationOptionsOpen
        onOpened: shell.translationOptionsOpen = true
        onClosed: shell.translationOptionsOpen = false
        background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
        Overlay.modal: Rectangle { color: Theme.overlay }
        contentItem: ColumnLayout {
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                PageControls.Heading { text: qsTr("Параметри перекладу"); Layout.fillWidth: true }
                PageControls.Action { objectName: "closeTranslationOptionsButton"; text: ""; iconName: "close"; hint: qsTr("Закрити"); subtle: true; implicitWidth: 32; implicitHeight: 32; onClicked: translationOptions.close(); Accessible.name: hint }
            }
            ScrollView {
                id: optionsScroll; Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; contentWidth: availableWidth
                ScrollBar.vertical: PageControls.ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: optionsScroll.availableWidth - 12; spacing: 10
                    PageControls.Caption { text: qsTr("Пам’ять проєкту") }
                    PageControls.Select {
                        objectName: "memoryProjectSelector"; Layout.fillWidth: true
                        model: backend.memoryProjects.map(function(project) { return project.id })
                        optionLabels: backend.memoryProjects.map(function(project) { return project.name })
                        currentIndex: Math.max(0, model.indexOf(backend.memoryProject.id || ""))
                        enabled: !backend.busy && !shell.reviewLocked
                        onActivated: function(index) { backend.selectMemoryProject(model[index]); shell.syncMemoryLanguages(); page.saveWorkspaceOptions() }
                        Accessible.name: qsTr("Пам’ять проєкту")
                    }
                    PageControls.Action {
                        text: backend.memoryProject.id ? qsTr("Еталони й глосарій…") : qsTr("Створити проєкт…")
                        objectName: "manageMemoryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; implicitHeight: 32
                        Layout.fillWidth: true; enabled: !backend.busy && !shell.reviewLocked
                        onClicked: { translationOptions.close(); shell.showMemoryDialog() }
                        hint: qsTr("Схожі фрагменти потребують платного запиту редактора; точні збіги використовують готовий переклад.")
                    }
                    PageControls.Caption { text: qsTr("Пам’ять поповнюється після перекладу автоматично. Глосарій та еталони необов’язкові."); visible: !!backend.memoryProject.id; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    PageControls.Action {
                        text: backend.glossaryCount ? qsTr("Глосарій: %1 термінів…").arg(backend.glossaryCount) : qsTr("Глосарій (необов’язково)…")
                        objectName: "editGlossaryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; Layout.fillWidth: true; enabled: !backend.busy
                        onClicked: { translationOptions.close(); shell.openGlossary() }
                        hint: qsTr("Додайте власні терміни або створіть пропозиції з документів. Готовий CSV не потрібен.")
                    }
                    PageControls.Divider { Layout.topMargin: 4; Layout.bottomMargin: 4 }
                    PageControls.Caption { text: qsTr("Тематика") }
                    PageControls.Select { id: domain; objectName: "documentDomainSelector"; model: shell.domainValues; optionLabels: shell.domainLabels; Layout.fillWidth: true; enabled: !backend.busy; onActivated: page.saveWorkspaceOptions() }
                    PageControls.Toggle { id: review; objectName: "reviewEnabled"; text: qsTr("Редакторська перевірка"); checked: true; enabled: !backend.busy; onToggled: page.saveWorkspaceOptions() }
                    PageControls.Toggle { id: autoGlossary; objectName: "autoGlossaryEnabled"; text: qsTr("Автоматично добирати терміни для документа"); checked: true; enabled: !backend.busy && !backend.memoryProject.id; onToggled: page.saveWorkspaceOptions() }
                    PageControls.Caption { text: backend.memoryProject.id ? qsTr("У проєкті діє ваш спільний список термінів. Порожній список також дозволений.") : qsTr("Автоматичний добір виконує додатковий API-запит під час перекладу. Ваші власні терміни мають пріоритет."); Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                    PageControls.Action {
                        text: qsTr("Власні інструкції") + (backend.translationInstructions.trim().length ? " •" : "")
                        objectName: "documentInstructionsButton"; iconName: "edit"; subtle: true; leftAligned: true; leftPadding: 0
                        Layout.fillWidth: true; enabled: !backend.busy
                        onClicked: { translationOptions.close(); shell.showInstructionsDialog() }
                    }
                    PageControls.Caption { text: qsTr("PDF потребує візуальної перевірки. Редакторський прохід доступний для інших підтримуваних документів."); visible: shell.hasPdf; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
            }
        }
    }
}
