import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    spacing: 12
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PageControls.Caption { text: qsTr("Документ") }
        PageControls.Select {
            id: reviewDocumentSelector
            objectName: "reviewDocumentSelector"
            Layout.fillWidth: true
            model: backend.reviewDocuments.map(function(document) { return document.name })
            currentIndex: backend.reviewDocuments.findIndex(function(document) { return document.id === backend.reviewDocumentId })
            displayText: backend.reviewDocument.name || qsTr("Виберіть документ")
            enabled: backend.reviewDocuments.length > 0 && !shell.reviewLocked
            onActivated: function(index) { backend.selectReviewDocument(backend.reviewDocuments[index].id) }
            Accessible.name: qsTr("Документ для перевірки")
        }
        PageControls.Action { text: qsTr("Оригінал"); iconName: "document"; objectName: "reviewOriginalButton"; subtle: true; enabled: !!backend.reviewDocument.path; onClicked: backend.openPath(backend.reviewDocument.path) }
        PageControls.Action { text: qsTr("Переклад"); iconName: "external"; objectName: "reviewOutputButton"; subtle: true; enabled: !!backend.reviewDocument.outputExists; onClicked: backend.openPath(backend.reviewDocument.output) }
        PageControls.Action { text: qsTr("Звіт"); iconName: "review"; objectName: "reviewReportButton"; subtle: true; enabled: !!backend.reviewDocument.reportExists; onClicked: backend.openReviewReport() }
        PageControls.Action { text: backend.reviewSaving ? qsTr("Зберігаємо…") : qsTr("Зберегти копію"); iconName: "save"; objectName: "reviewExportButton"; primary: true; visible: backend.reviewHasEdits || backend.reviewSaving; enabled: backend.reviewHasEdits && !shell.reviewLocked; onClicked: backend.exportReviewEdits(); hint: qsTr("Створити виправлену копію. Попередній переклад залишиться без змін") }
    }
    PageControls.Caption { text: backend.reviewActionError; objectName: "reviewActionError"; visible: text.length > 0; color: Theme.danger; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    PageControls.Caption { text: qsTr("Правки збережено як чернетку. Збережіть копію, щоб отримати виправлений документ."); visible: backend.reviewHasEdits && !backend.reviewEditing; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: shell.accent }
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        TabBar {
            id: reviewModes
            objectName: "reviewModes"
            enabled: !shell.reviewLocked
            implicitWidth: Math.max(316, 2 * Math.max(reviewIssuesMode.implicitWidth, reviewAllMode.implicitWidth) + 2)
            Layout.preferredWidth: implicitWidth; Layout.minimumWidth: implicitWidth; implicitHeight: 38
            currentIndex: backend.reviewFilter === "all" ? 1 : 0
            onCurrentIndexChanged: {
                var filter = currentIndex === 1 ? "all" : "issues"
                if (backend.reviewFilter !== filter) backend.setReviewFilter(filter)
            }
            background: Rectangle { color: Theme.disabled; radius: 7 }
            PageControls.NavigationTab { id: reviewIssuesMode; text: qsTr("Зауваження"); iconName: "attention"; objectName: "reviewIssuesFilter"; implicitHeight: 38 }
            PageControls.NavigationTab { id: reviewAllMode; text: qsTr("Увесь текст"); iconName: "list"; objectName: "reviewAllFilter"; implicitHeight: 38 }
        }
        PageControls.Field {
            id: reviewSearchField
            objectName: "reviewSearchField"
            enabled: !shell.reviewLocked
            Layout.fillWidth: true; implicitHeight: 38
            placeholderText: qsTr("Пошук у тексті та зауваженнях")
            text: backend.reviewSearch
            onTextEdited: backend.setReviewSearch(text)
            Accessible.name: qsTr("Пошук у документі")
            rightPadding: 38
            PageControls.Action { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: 3; text: ""; iconName: "close"; hint: qsTr("Очистити пошук"); subtle: true; implicitHeight: 30; implicitWidth: 30; visible: reviewSearchField.text.length > 0; onClicked: backend.setReviewSearch(""); Accessible.name: hint }
        }
    }
    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
        PageControls.Panel {
            Layout.preferredWidth: reviewModes.width; Layout.minimumWidth: 270; Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 8
                PageControls.LabelText {
                    text: backend.reviewSummary.loading ? qsTr("Завантажуємо…") : backend.reviewFilter === "issues" ? qsTr("Потребують уваги: %1").arg(backend.reviewSummary.attention || 0) : qsTr("Фрагментів: %1").arg(backend.reviewSummary.segments || 0)
                    font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true
                }
                PageControls.Caption { text: qsTr("Приміток до документа: %1").arg(backend.reviewSummary.notes || 0); visible: backend.reviewFilter === "all" && backend.reviewSummary.notes > 0; Layout.fillWidth: true }
                PageControls.Caption { text: qsTr("Коректні: %1 · З правками: %2").arg(backend.reviewSummary.accepted || 0).arg(backend.reviewSummary.edited || 0); visible: backend.reviewSummary.accepted > 0 || backend.reviewSummary.edited > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                PageControls.Caption { text: qsTr("Знайдено: %1").arg(backend.reviewEntries.length); visible: backend.reviewSearch.length > 0; Layout.fillWidth: true }
                PageControls.Divider { }
                ListView {
                    id: reviewEntryList
                    objectName: "reviewEntryList"
                    enabled: !shell.reviewLocked
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                    model: backend.reviewEntries
                    currentIndex: shell.reviewEntryIndex
                    onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                    ScrollBar.vertical: PageControls.ReadingScrollBar { }
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
                            radius: 6; color: reviewEntryRow.highlighted ? shell.accentLight : reviewEntryRow.hovered ? Theme.surfaceMuted : Theme.surface
                            border.color: reviewEntryRow.visualFocus ? shell.accent : reviewEntryRow.highlighted ? Theme.accentLine : "transparent"
                            Rectangle { x: 0; y: 10; width: 3; height: parent.height - 20; radius: 1; visible: reviewEntryRow.modelData.issueCount > 0 && !reviewEntryRow.modelData.accepted; color: reviewEntryRow.modelData.severity === "error" ? Theme.danger : Theme.warning }
                        }
                        contentItem: ColumnLayout {
                            spacing: 5
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                PageControls.Caption { text: reviewEntryRow.modelData.location || qsTr("Документ"); Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; color: shell.ink }
                                Icon { name: "check"; color: Theme.success; visible: !!reviewEntryRow.modelData.accepted; Accessible.name: qsTr("Позначено як коректне") }
                                Icon { name: "edit"; color: shell.accent; visible: !!reviewEntryRow.modelData.edited; Accessible.name: qsTr("Є правка") }
                                PageControls.Caption { text: String(reviewEntryRow.modelData.issueCount || ""); visible: reviewEntryRow.modelData.issueCount > 0 && !reviewEntryRow.modelData.accepted; font.weight: Font.DemiBold; color: reviewEntryRow.modelData.severity === "error" ? Theme.danger : Theme.warning }
                            }
                            PageControls.LabelText {
                                text: (reviewEntryRow.modelData.source || reviewEntryRow.modelData.translation || (reviewEntryRow.modelData.messages || [])[0] || qsTr("Примітка до документа")).replace(/\s+/g, " ")
                                font.pixelSize: 12; Layout.fillWidth: true; Layout.fillHeight: true; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight
                            }
                        }
                        onClicked: backend.selectReviewEntry(modelData.id)
                    }
                    PageControls.Caption { anchors.centerIn: parent; width: parent.width - 24; text: backend.reviewSummary.loading ? qsTr("Завантаження звіту…") : backend.reviewSearch.length > 0 ? qsTr("Збігів немає") : qsTr("Список порожній"); visible: backend.reviewEntries.length === 0; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            visible: !!backend.selectedReviewEntry.id
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                PageControls.LabelText { text: backend.selectedReviewEntry.location || qsTr("Документ"); Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; font.pixelSize: 13 }
                PageControls.Action { text: qsTr("Редагувати"); iconName: "edit"; objectName: "reviewEditButton"; subtle: true; implicitHeight: 32; visible: backend.selectedReviewEntry.kind === "segment" && !backend.reviewEditing; enabled: !!backend.selectedReviewEntry.canEdit && !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.beginReviewEdit(); hint: backend.selectedReviewEntry.editReason || qsTr("Редагувати текст перекладу цього фрагмента") }
                PageControls.Action { text: ""; iconName: "undo"; objectName: "reviewDiscardEditButton"; implicitWidth: 34; implicitHeight: 32; visible: !!backend.selectedReviewEntry.edited && !backend.reviewEditing; enabled: !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.discardReviewEdit(); hint: qsTr("Скасувати збережену правку цього фрагмента"); Accessible.name: hint }
                PageControls.Action { text: qsTr("Застосувати"); iconName: "check"; objectName: "reviewApplyButton"; primary: true; implicitHeight: 32; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.applyReviewEdit(); hint: qsTr("Зберегти правку в чернетці") }
                PageControls.Action { text: qsTr("Скасувати"); iconName: "close"; objectName: "reviewCancelEditButton"; implicitHeight: 32; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.cancelReviewEdit() }
                PageControls.Action { text: ""; iconName: "chevron-left"; hint: qsTr("Попередній запис"); objectName: "reviewPreviousButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !shell.reviewLocked && shell.reviewEntryIndex > 0; onClicked: backend.stepReviewEntry(-1); Accessible.name: hint }
                PageControls.Caption { text: (shell.reviewEntryIndex + 1) + " / " + backend.reviewEntries.length; visible: !backend.reviewEditing; Layout.minimumWidth: 46; horizontalAlignment: Text.AlignHCenter }
                PageControls.Action { text: ""; iconName: "chevron-right"; hint: qsTr("Наступний запис"); objectName: "reviewNextButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !shell.reviewLocked && shell.reviewEntryIndex >= 0 && shell.reviewEntryIndex < backend.reviewEntries.length - 1; onClicked: backend.stepReviewEntry(1); Accessible.name: hint }
            }
            PageControls.Action { text: qsTr("Запам’ятати формулювання"); objectName: "rememberReviewWordingButton"; iconName: "book"; subtle: true; implicitHeight: 30; visible: !!backend.memoryProject.id && backend.selectedReviewEntry.kind === "segment" && !backend.reviewEditing; enabled: backend.canRememberReviewWording; onClicked: shell.showRememberWordingDialog() }
            PageControls.Panel {
                id: explanationsPanel
                Layout.fillWidth: true
                Layout.fillHeight: backend.selectedReviewEntry.kind === "document"
                Layout.preferredHeight: backend.selectedReviewEntry.kind === "document" ? 1 : Math.min(backend.reviewEditing ? 108 : shell.height < 760 ? 112 : 160, explanationsText.implicitHeight + 70)
                Layout.minimumHeight: 86
                visible: (backend.selectedReviewEntry.messages || []).length > 0
                color: backend.selectedReviewEntry.accepted ? Theme.successFill : backend.selectedReviewEntry.severity === "error" ? Theme.dangerFill : backend.selectedReviewEntry.severity === "warning" ? Theme.warningFill : Theme.surface
                border.color: backend.selectedReviewEntry.accepted ? Theme.successLine : backend.selectedReviewEntry.severity === "error" ? Theme.dangerLine : backend.selectedReviewEntry.severity === "warning" ? Theme.warningLine : shell.line
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 9
                    RowLayout {
                        Icon { name: backend.selectedReviewEntry.accepted ? "check" : backend.selectedReviewEntry.severity === "info" ? "info" : "attention"; color: backend.selectedReviewEntry.accepted ? Theme.success : backend.selectedReviewEntry.severity === "error" ? Theme.danger : backend.selectedReviewEntry.severity === "warning" ? Theme.warning : shell.muted }
                        PageControls.LabelText { text: backend.selectedReviewEntry.accepted ? qsTr("Позначено як коректне") : backend.selectedReviewEntry.severity === "info" ? qsTr("Примітка до документа") : ((backend.selectedReviewEntry.messages || []).length > 1 ? qsTr("На що звернути увагу (%1)").arg(backend.selectedReviewEntry.messages.length) : qsTr("На що звернути увагу")); font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        PageControls.Action { text: qsTr("Це коректно"); iconName: "check"; objectName: "reviewConfirmButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.canAccept && !backend.selectedReviewEntry.accepted; enabled: !shell.reviewLocked; onClicked: backend.confirmReviewEntry(); hint: qsTr("Позначити цей запис як перевірений. Він залишиться у вкладці «Увесь текст»") }
                        PageControls.Action { text: qsTr("Повернути зауваження"); iconName: "undo"; objectName: "reviewReopenButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.accepted; enabled: !shell.reviewLocked; onClicked: backend.reopenReviewEntry() }
                    }
                    ScrollView {
                        id: explanationsScroll
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                        ScrollBar.vertical: PageControls.ReadingScrollBar { }
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        PageControls.ReadOnlyText { id: explanationsText; objectName: "reviewExplanationText"; width: explanationsScroll.availableWidth; text: (backend.selectedReviewEntry.messages || []).join("\n\n") + (backend.selectedReviewEntry.severity === "error" ? "\n\n" + qsTr("Помилку потрібно виправити; позначити її коректною не можна.") : ""); onTextChanged: if (explanationsScroll.contentItem) explanationsScroll.contentItem.contentY = 0; font.pixelSize: 13; Accessible.name: qsTr("Пояснення до вибраного запису") }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                visible: backend.selectedReviewEntry.kind !== "document"
                PageControls.ReviewTextPanel { heading: qsTr("ОРИГІНАЛ"); text: backend.selectedReviewEntry.source || ""; textObjectName: "reviewSourceText"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1 }
                PageControls.ReviewTextPanel {
                    heading: qsTr("ПЕРЕКЛАД"); text: backend.reviewEditing ? backend.reviewDraftText : backend.selectedReviewEntry.translation || ""
                    textObjectName: "reviewTranslationText"; editing: backend.reviewEditing; edited: !!backend.selectedReviewEntry.edited
                    editError: backend.reviewEditing ? backend.reviewEditError : ""
                    onDraftEdited: function(value) { if (backend.reviewEditing && value !== backend.reviewDraftText) backend.updateReviewDraft(value) }
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                }
            }
        }
        PageControls.Panel {
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: !backend.selectedReviewEntry.id
            ColumnLayout {
                anchors.centerIn: parent; width: Math.min(400, parent.width - 40); spacing: 14
                BusyIndicator { running: backend.reviewSummary.loading; visible: running; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 36; Layout.preferredHeight: 36 }
                Icon { name: backend.reviewSummary.error ? "attention" : "review"; color: Theme.muted; visible: !backend.reviewSummary.loading; Layout.preferredWidth: 32; Layout.preferredHeight: 32; Layout.alignment: Qt.AlignHCenter }
                PageControls.Heading { text: shell.reviewEmptyTitle(); Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
                PageControls.Caption { text: shell.reviewEmptyDescription(); Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.3 }
                PageControls.Action { text: qsTr("Очистити пошук"); iconName: "close"; visible: backend.reviewSearch.length > 0 && !backend.reviewSummary.loading; Layout.alignment: Qt.AlignHCenter; onClicked: backend.setReviewSearch("") }
                PageControls.Action { text: qsTr("Показати всі записи"); iconName: "list"; objectName: "reviewShowAllButton"; visible: !backend.reviewSummary.loading && !backend.reviewSummary.error && backend.reviewFilter === "issues" && backend.reviewSearch.length === 0 && (backend.reviewSummary.segments > 0 || backend.reviewSummary.notes > 0); Layout.alignment: Qt.AlignHCenter; onClicked: backend.setReviewFilter("all") }
            }
        }
    }
}
