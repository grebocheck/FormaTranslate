import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: page
    required property var shell
    property alias domain: domain
    property alias targetLanguage: targetLanguage
    property alias sourceLanguage: sourceLanguage
    spacing: 18
    ColumnLayout {
        id: documentColumn
        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
        PageControls.Panel {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: shell.previewOpen ? 280 : 240
            border.color: documentDrop.containsDrag ? shell.accent : shell.line
            DropArea { id: documentDrop; anchors.fill: parent; enabled: backend.canAddDocuments; onDropped: function(event) { if (event.hasUrls) backend.addFiles(event.urls) } }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; spacing: 12
                RowLayout {
                    PageControls.Heading { text: qsTr("Документи") }
                    PageControls.Caption { text: backend.documentCount ? String(backend.documentCount) : "" }
                    Item { Layout.fillWidth: true }
                    PageControls.Action { text: ""; iconName: "clear-done"; hint: qsTr("Прибрати завершені з черги. Вони залишаться в історії"); objectName: "clearCompletedButton"; subtle: true; visible: shell.hasCompleted; implicitHeight: 36; implicitWidth: 36; onClicked: backend.clearCompleted(); Accessible.name: qsTr("Прибрати завершені") }
                    PageControls.Action { text: qsTr("Додати файли"); iconName: "add"; objectName: "addDocumentsButton"; visible: backend.documentCount > 0; enabled: backend.canAddDocuments; implicitHeight: 36; onClicked: backend.pickDocuments() }
                }
                PageControls.Caption { text: qsTr("Нові файли перекладатимуться з поточними параметрами цього запуску."); visible: backend.translating; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                PageControls.Divider { }
                ListView {
                    id: fileList
                    Layout.fillWidth: true; Layout.fillHeight: true
                    model: backend.documentModel; clip: true; spacing: 2
                    ScrollBar.vertical: ScrollBar { }
                    delegate: Rectangle {
                        id: fileRow
                        required property var modelData
                        required property int index
                        width: ListView.view.width; height: modelData.status === "translating" ? 90 : 74; radius: 6
                        color: backend.selectedId === modelData.id ? shell.accentLight : rowMouse.containsMouse ? Theme.surfaceMuted : Theme.surface
                        MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; onClicked: backend.selectDocument(fileRow.modelData.id) }
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 12
                            Rectangle {
                                width: 40; height: 46; radius: 5
                                color: fileRow.modelData.format === "DOCX" ? Theme.accentLight : fileRow.modelData.format === "XLSX" ? Theme.successFill : Theme.dangerFill
                                PageControls.LabelText { anchors.centerIn: parent; text: fileRow.modelData.format; font.pixelSize: 10; font.weight: Font.Bold; color: fileRow.modelData.format === "DOCX" ? Theme.accent : fileRow.modelData.format === "XLSX" ? Theme.success : Theme.danger }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    PageControls.LabelText { text: fileRow.modelData.name; Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; font.pixelSize: 13 }
                                    PageControls.Caption { visible: !!fileRow.modelData.startedAt; text: shell.duration(fileRow.modelData.status === "translating" ? (shell.clockNow - fileRow.modelData.startedAt) / 1000 : fileRow.modelData.elapsedSeconds || 0) }
                                }
                                PageControls.Caption { text: fileRow.modelData.size + " · " + fileRow.modelData.statusText; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; color: fileRow.modelData.status === "error" ? Theme.danger : shell.muted }
                                Rectangle {
                                    Layout.fillWidth: true; height: 3; radius: 2; visible: fileRow.modelData.status === "translating"; color: Theme.accentLine
                                    Rectangle { width: parent.width * fileRow.modelData.progress / 100; height: 3; radius: 2; color: shell.accent }
                                }
                            }
                            ColumnLayout {
                                visible: !!fileRow.modelData.canMove; spacing: 0
                                PageControls.Action { text: ""; iconName: "arrow-up"; hint: qsTr("Перекласти раніше"); objectName: "moveUpButton"; subtle: true; implicitHeight: 26; implicitWidth: 30; enabled: shell.canMoveQueue(fileRow.index, -1); onClicked: backend.moveDocument(fileRow.modelData.id, -1); Accessible.name: hint }
                                PageControls.Action { text: ""; iconName: "arrow-down"; hint: qsTr("Перекласти пізніше"); objectName: "moveDownButton"; subtle: true; implicitHeight: 26; implicitWidth: 30; enabled: shell.canMoveQueue(fileRow.index, 1); onClicked: backend.moveDocument(fileRow.modelData.id, 1); Accessible.name: hint }
                            }
                            PageControls.Action { text: qsTr("Відкрити"); iconName: "external"; visible: fileRow.modelData.status === "done"; implicitHeight: 32; onClicked: backend.openPath(fileRow.modelData.output) }
                            PageControls.Action { text: ""; iconName: "trash"; hint: qsTr("Прибрати документ із черги"); objectName: "removeDocumentButton"; subtle: true; implicitHeight: 34; implicitWidth: 34; enabled: !!fileRow.modelData.canRemove; onClicked: backend.removeDocument(fileRow.modelData.id); Accessible.name: hint }
                        }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; width: Math.min(360, parent.width - 24); spacing: 12; visible: backend.documentCount === 0
                        Item {
                            Layout.alignment: Qt.AlignHCenter; width: 56; height: 60
                            Icon { anchors.centerIn: parent; name: "document"; width: 36; height: 36; color: Theme.muted }
                        }
                        PageControls.Heading { text: qsTr("Додайте документи для перекладу"); font.pixelSize: 18; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                        PageControls.Caption { text: qsTr("Перетягніть файли сюди або виберіть їх на комп’ютері."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 1.3 }
                        PageControls.Action { text: qsTr("Вибрати файли"); iconName: "folder"; primary: true; Layout.alignment: Qt.AlignHCenter; enabled: backend.canAddDocuments; onClicked: backend.pickDocuments() }
                        PageControls.Caption { text: "DOCX  ·  XLSX  ·  PDF"; Layout.alignment: Qt.AlignHCenter; font.pixelSize: 11 }
                    }
                }
                PageControls.Divider { visible: !!backend.selectedDocument.path }
                ColumnLayout {
                    visible: backend.selectedDocument.format === "PDF" || backend.recognizingScan
                    Layout.fillWidth: true; spacing: 8
                    RowLayout {
                        objectName: "documentWorkflowInfo"
                        visible: !!backend.selectedDocument.workflowText && !backend.recognizingScan
                        Layout.fillWidth: true; spacing: 9
                        Icon { name: "info"; color: shell.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.alignment: Qt.AlignTop; Layout.topMargin: 1 }
                        PageControls.Caption { text: backend.selectedDocument.workflowText || ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.25 }
                    }
                    RowLayout {
                        visible: backend.recognizingScan
                        Layout.fillWidth: true; spacing: 9
                        BusyIndicator { running: backend.recognizingScan; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                        PageControls.Caption { objectName: "scanStatusText"; text: backend.scanMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                    ProgressBar { objectName: "scanProgressBar"; visible: backend.recognizingScan; from: 0; to: 100; value: backend.scanProgress; Layout.fillWidth: true }
                    RowLayout {
                        visible: !backend.recognizingScan
                        Layout.fillWidth: true
                        PageControls.Action {
                            objectName: "pdfPreparationOptionsButton"
                            text: qsTr("Додаткові дії з PDF"); iconName: shell.pdfPreparationOptionsOpen ? "chevron-down" : "chevron-right"
                            subtle: true; implicitHeight: 30; leftPadding: 0; enabled: !backend.busy
                            onClicked: shell.pdfPreparationOptionsOpen = !shell.pdfPreparationOptionsOpen
                        }
                        Item { Layout.fillWidth: true }
                        PageControls.Action {
                            objectName: "importAbbyyResultButton"; text: shell.currentAbbyyHandoff.continueTranslation ? qsTr("Імпортувати й продовжити") : qsTr("Імпортувати DOCX…"); iconName: "import"; subtle: true; implicitHeight: 30
                            visible: shell.currentAbbyyHandoff.sourceId === backend.selectedDocument.id && !!shell.currentAbbyyHandoff.sourceId
                            enabled: shell.canImportAbbyyResult; onClicked: shell.showAbbyyHandoffDialog()
                        }
                    }
                    ColumnLayout {
                        visible: shell.pdfPreparationOptionsOpen && !backend.recognizingScan
                        Layout.fillWidth: true; spacing: 6
                    PageControls.Caption { text: qsTr("Спосіб обробки цього PDF"); Layout.fillWidth: true }
                    AppSelect {
                        objectName: "documentPdfModeSelector"
                        Layout.fillWidth: true
                        model: ["auto", "ocr", "direct"]
                        optionLabels: [qsTr("Автоматично"), qsTr("Розпізнати через OCR перед перекладом"), qsTr("Прямий переклад PDF")]
                        currentIndex: Math.max(0, model.indexOf(backend.selectedDocument.pdfMode || "auto"))
                        enabled: !backend.busy
                        onActivated: function(index) { backend.setDocumentPdfMode(backend.selectedDocument.id, model[index]) }
                    }
                        RowLayout {
                            Layout.fillWidth: true
                            PageControls.Action {
                                objectName: "recognizePdfButton"
                                text: shell.manualAbbyy ? qsTr("Відкрити в ABBYY…") : qsTr("Скан PDF → DOCX"); iconName: "document"; implicitHeight: 34
                                enabled: !backend.busy && backend.selectedDocument.status !== "loading"
                                onClicked: backend.recognizeSelectedPdf(shell.sourceCodes[sourceLanguage.currentIndex])
                            }
                            Item { Layout.fillWidth: true }
                            PageControls.Action { objectName: "scanSettingsButton"; text: shell.width < 1120 ? "" : qsTr("Налаштувати OCR"); hint: qsTr("Налаштувати OCR"); iconName: "settings"; subtle: true; implicitHeight: 32; enabled: !backend.busy; onClicked: shell.openScanSettings() }
                        }
                        PageControls.Caption {
                            text: shell.manualAbbyy ? qsTr("Збережіть розпізнаний документ як DOCX у FineReader, перевірте його та поверніть у чергу.") : qsTr("Створює DOCX локально й замінює PDF у черзі. Перевірте текст і таблиці перед перекладом.")
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                        }
                    }
                }
                RowLayout {
                    visible: !!backend.selectedDocument.path; Layout.fillWidth: true; spacing: 4
                    PageControls.Action { text: qsTr("Оригінал"); iconName: "document"; subtle: true; implicitHeight: 30; onClicked: backend.openPath(backend.selectedDocument.path) }
                    PageControls.Action { text: shell.previewOpen ? qsTr("Сховати текст") : qsTr("Переглянути текст"); iconName: shell.previewOpen ? "eye-off" : "eye"; subtle: true; implicitHeight: 30; visible: backend.selectedDocument.format !== "PDF"; onClicked: shell.previewOpen = !shell.previewOpen }
                    Item { Layout.fillWidth: true }
                    PageControls.Action { text: backend.selectedDocument.warningCount ? qsTr("Зауваження: %1").arg(backend.selectedDocument.warningCount) : qsTr("Звіт"); iconName: "review"; subtle: true; implicitHeight: 30; visible: !!backend.selectedDocument.report; onClicked: shell.page = 1 }
                    PageControls.Action { text: qsTr("Переглянути помилки"); objectName: "openFailureReportButton"; iconName: "review"; subtle: true; implicitHeight: 30; visible: !!backend.selectedDocument.diagnosticReport; onClicked: backend.openFailureReport() }
                }
                  PageControls.Caption { text: qsTr("З пам’яті: %1 · повтори: %2 · адаптовано: %3").arg(backend.selectedDocument.memorySegments || 0).arg(backend.selectedDocument.repeatedSegments || 0).arg(backend.selectedDocument.adaptedSegments || 0); visible: (backend.selectedDocument.memorySegments || 0) > 0 || (backend.selectedDocument.repeatedSegments || 0) > 0 || (backend.selectedDocument.adaptedSegments || 0) > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                PageControls.Divider { }
                RowLayout {
                    Layout.fillWidth: true
                    PageControls.Caption { text: qsTr("Результати") }
                    PageControls.Caption { text: backend.outputFolder; Layout.fillWidth: true; elide: Text.ElideMiddle; color: shell.ink }
                    PageControls.Action { text: qsTr("Відкрити"); iconName: "folder"; objectName: "openOutputFolderButton"; subtle: true; implicitHeight: 32; onClicked: backend.openOutputFolder(); hint: qsTr("Відкрити папку результатів") }
                    PageControls.Action { text: qsTr("Змінити…"); iconName: "settings"; subtle: true; implicitHeight: 32; enabled: !backend.busy; onClicked: backend.pickOutputFolder() }
                }
            }
        }
        PageControls.Panel {
            visible: shell.previewOpen && !!backend.selectedDocument.path && backend.selectedDocument.format !== "PDF"
            Layout.fillWidth: true; Layout.preferredHeight: Math.min(230, documentColumn.height * 0.3); Layout.minimumHeight: 80
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; spacing: 10
                RowLayout {
                    PageControls.Caption { text: qsTr("ОРИГІНАЛ"); Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold; font.pixelSize: 11 }
                    PageControls.Caption { text: qsTr("ПЕРЕКЛАД"); Layout.fillWidth: true; Layout.preferredWidth: 1; font.weight: Font.DemiBold; font.pixelSize: 11 }
                }
                PageControls.Divider { }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 14; model: backend.segments
                    ScrollBar.vertical: ScrollBar { }
                    delegate: Item {
                        id: segmentRow
                        required property var modelData
                        width: ListView.view.width; height: Math.max(sourceText.implicitHeight, targetText.implicitHeight) + 22
                        PageControls.Caption { text: segmentRow.modelData.location; font.pixelSize: 10; width: parent.width; elide: Text.ElideRight }
                        RowLayout {
                            anchors.top: parent.top; anchors.topMargin: 20; width: parent.width; spacing: 24
                            PageControls.LabelText { id: sourceText; text: segmentRow.modelData.source; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop }
                            PageControls.LabelText { id: targetText; text: segmentRow.modelData.translation || qsTr("Після перекладу"); color: segmentRow.modelData.translation ? shell.ink : shell.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop }
                        }
                    }
                }
            }
        }
    }
    PageControls.Panel {
        Layout.preferredWidth: 320; Layout.minimumWidth: 280; Layout.fillHeight: true
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 20; spacing: 16
            ScrollView {
                id: translationScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                  ScrollBar.vertical: PageControls.ReadingScrollBar { }
                  ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: translationScroll.availableWidth - 12; spacing: 10
                    PageControls.Heading { text: qsTr("Мови перекладу"); Layout.bottomMargin: 6 }
                    PageControls.Caption { text: qsTr("Мова оригіналу") }
                      PageControls.Select { id: sourceLanguage; objectName: "sourceLanguage"; model: shell.sourceCodes; optionLabels: [qsTr("Автовизначення")].concat(shell.languageLabels); Layout.fillWidth: true; enabled: !backend.busy && !backend.memoryProject.id }
                    PageControls.Caption { text: qsTr("Перекласти на"); Layout.topMargin: 7 }
                      PageControls.Select { id: targetLanguage; objectName: "targetLanguage"; model: shell.targetCodes; optionLabels: shell.languageLabels; currentIndex: 0; Layout.fillWidth: true; enabled: !backend.busy && !backend.memoryProject.id }
                    PageControls.Caption {
                        visible: shell.needsSourceLanguage || shell.sameLanguage
                        text: shell.needsSourceLanguage ? qsTr("Для PDF виберіть мову оригіналу.") : qsTr("Виберіть іншу мову перекладу.")
                        color: Theme.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                      PageControls.Caption { text: qsTr("Пам’ять проєкту") }
                      PageControls.Select {
                          objectName: "memoryProjectSelector"
                          Layout.fillWidth: true
                          model: backend.memoryProjects.map(function(project) { return project.id })
                          optionLabels: backend.memoryProjects.map(function(project) { return project.name })
                          currentIndex: Math.max(0, model.indexOf(backend.memoryProject.id || ""))
                          enabled: !backend.busy && !shell.reviewLocked
                          onActivated: function(index) { backend.selectMemoryProject(model[index]); shell.syncMemoryLanguages() }
                          Accessible.name: qsTr("Пам’ять проєкту")
                      }
                      PageControls.Action { text: backend.memoryProject.id ? qsTr("Еталони й глосарій…") : qsTr("Створити проєкт…"); objectName: "manageMemoryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; implicitHeight: 30; Layout.fillWidth: true; enabled: !backend.busy && !shell.reviewLocked; onClicked: shell.showMemoryDialog(); hint: qsTr("Схожі фрагменти потребують платного запиту редактора; точні збіги використовують готовий переклад.") }
                      PageControls.Caption { text: qsTr("Пам’ять поповнюється після перекладу автоматично. Глосарій та еталони необов’язкові."); visible: !!backend.memoryProject.id; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                      PageControls.Action { text: backend.glossaryCount ? qsTr("Глосарій: %1 термінів…").arg(backend.glossaryCount) : qsTr("Глосарій (необов’язково)…"); objectName: "editGlossaryButton"; iconName: "book"; subtle: true; leftAligned: true; leftPadding: 0; Layout.fillWidth: true; enabled: !backend.busy; onClicked: shell.openGlossary(); hint: qsTr("Додайте власні терміни або створіть пропозиції з документів. Готовий CSV не потрібен.") }
                      PageControls.Divider { Layout.topMargin: 10; Layout.bottomMargin: 4 }
                    PageControls.Action { text: qsTr("Параметри перекладу"); iconName: shell.translationOptionsOpen ? "chevron-down" : "chevron-right"; subtle: true; leftAligned: true; leftPadding: 0; Layout.fillWidth: true; implicitHeight: 34; onClicked: shell.translationOptionsOpen = !shell.translationOptionsOpen }
                    ColumnLayout {
                        visible: shell.translationOptionsOpen; Layout.fillWidth: true; spacing: 9
                        PageControls.Caption { text: qsTr("Тематика") }
                        PageControls.Select { id: domain; model: shell.domainValues; optionLabels: shell.domainLabels; Layout.fillWidth: true; enabled: !backend.busy }
                        PageControls.Toggle { id: review; objectName: "reviewEnabled"; text: qsTr("Редакторська перевірка"); checked: true; enabled: !backend.busy }
                          PageControls.Toggle { id: autoGlossary; text: qsTr("Автоматично добирати терміни для документа"); checked: true; enabled: !backend.busy && !backend.memoryProject.id }
                          PageControls.Caption { text: backend.memoryProject.id ? qsTr("У проєкті діє ваш спільний список термінів. Порожній список також дозволений.") : qsTr("Автоматичний добір виконує додатковий API-запит під час перекладу. Ваші власні терміни мають пріоритет."); Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                    }
                    PageControls.Action {
                        text: qsTr("Власні інструкції") + (backend.translationInstructions.trim().length ? " •" : "")
                        objectName: "documentInstructionsButton"; iconName: "edit"; subtle: true; leftAligned: true; leftPadding: 0
                        Layout.fillWidth: true; enabled: !backend.busy; onClicked: shell.showInstructionsDialog()
                    }
                    PageControls.Caption { text: qsTr("Перевіряємо переклад і зберігаємо оригінальний файл."); visible: review.checked && !shell.hasPdf && shell.translationOptionsOpen; wrapMode: Text.WordWrap; Layout.fillWidth: true; lineHeight: 1.4 }
                    PageControls.Caption { text: qsTr("Редакторську перевірку вимкнено."); visible: !review.checked && !shell.hasPdf; color: Theme.warning; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    PageControls.Caption { text: qsTr("PDF потребує візуальної перевірки. Редакторський прохід доступний для DOCX і XLSX."); visible: shell.hasPdf; wrapMode: Text.WordWrap; Layout.fillWidth: true; lineHeight: 1.4 }
                }
            }
            ColumnLayout {
                visible: !backend.settings.model; Layout.fillWidth: true; spacing: 4
                PageControls.Caption { text: qsTr("Спочатку підключіть модель у налаштуваннях."); wrapMode: Text.WordWrap; Layout.fillWidth: true }
                PageControls.Action { text: qsTr("Налаштувати підключення"); iconName: "settings"; subtle: true; implicitHeight: 30; Layout.leftMargin: -10; onClicked: shell.page = 2 }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                PageControls.Action {
                    objectName: "translateButton"
                    iconName: shell.preparingDocuments ? "document" : backend.translating ? (backend.paused ? "play" : "pause") : "translate"
                    text: shell.preparingDocuments ? qsTr("Підготовка…") : backend.translating ? (backend.paused ? qsTr("Продовжити") : qsTr("Пауза")) : qsTr("Перекласти")
                    primary: true; Layout.fillWidth: true; implicitHeight: 44
                    enabled: !shell.preparingDocuments && (backend.translating || (!backend.busy && backend.documentCount > 0 && !!backend.settings.model && !shell.needsSourceLanguage && !shell.sameLanguage))
                    onClicked: backend.translating ? backend.togglePause() : backend.start(shell.sourceCodes[sourceLanguage.currentIndex], shell.targetCodes[targetLanguage.currentIndex], domain.currentText, review.checked, autoGlossary.checked)
                }
                PageControls.Action { text: qsTr("Стоп"); iconName: "stop"; objectName: "cancelButton"; visible: backend.translating || backend.recognizingScan; implicitHeight: 44; onClicked: backend.cancel() }
            }
        }
    }
}
