import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    spacing: 10
    Connections {
        target: backend
        function onRecoveryStarted() { shell.page = 0 }
    }
    Shortcut {
        sequences: ["Ctrl+Return", "Ctrl+Enter"]
        enabled: page.visible && backend.reviewEditing && translationPane.editorHasFocus && !backend.reviewSaving && !backend.memoryWorking
        onActivated: backend.applyReviewEdit()
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 10
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
        PageControls.Action { text: ""; hint: qsTr("Оригінал"); iconName: "document"; objectName: "reviewOriginalButton"; implicitWidth: 36; subtle: true; enabled: !!backend.reviewDocument.path; onClicked: backend.openPath(backend.reviewDocument.path) }
        PageControls.Action { text: ""; hint: qsTr("Переклад"); iconName: "external"; objectName: "reviewOutputButton"; implicitWidth: 36; subtle: true; enabled: !!backend.reviewDocument.outputExists; onClicked: backend.openPath(backend.reviewDocument.output) }
        PageControls.Action { text: ""; hint: qsTr("Звіт"); iconName: "review"; objectName: "reviewReportButton"; implicitWidth: 36; subtle: true; enabled: !!backend.reviewDocument.reportExists; onClicked: backend.openReviewReport() }
        PageControls.Caption { text: qsTr("Правок: %1").arg(backend.reviewSummary.edited || 0); color: backend.reviewHasEdits ? shell.accent : shell.muted }
        PageControls.Action {
            text: backend.reviewSaving ? qsTr("Зберігаємо…") : backend.reviewDocument.recovery ? qsTr("Продовжити переклад") : qsTr("Зберегти копію")
            iconName: backend.reviewDocument.recovery ? "play" : "save"; objectName: "reviewExportButton"; primary: true
            enabled: (backend.reviewDocument.recovery ? backend.reviewDocument.canResume : backend.reviewHasEdits) && !shell.reviewLocked
            onClicked: backend.exportReviewEdits()
            hint: backend.reviewDocument.recovery ? qsTr("Перевірити правки й продовжити переклад цього документа") : backend.reviewHasEdits ? qsTr("Створити виправлену копію. Попередній переклад залишиться без змін") : qsTr("Спочатку збережіть правку фрагмента")
        }
    }
    PageControls.Caption {
        text: backend.reviewDocument.recoveryReason || qsTr("Виправте проблемні фрагменти та продовжіть переклад. Готові фрагменти буде використано повторно.")
        visible: !!backend.reviewDocument.recovery; Layout.fillWidth: true; wrapMode: Text.WordWrap
    }
    PageControls.Caption { text: backend.reviewActionError; objectName: "reviewActionError"; visible: text.length > 0; color: Theme.danger; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        TabBar {
            id: reviewModes
            objectName: "reviewModes"
            enabled: !shell.reviewLocked
            implicitWidth: Math.max(240, 2 * Math.max(reviewIssuesMode.implicitWidth, reviewAllMode.implicitWidth) + 2)
            Layout.preferredWidth: implicitWidth; Layout.minimumWidth: implicitWidth; implicitHeight: 38
            currentIndex: backend.reviewFilter === "all" ? 1 : 0
            onCurrentIndexChanged: {
                var filter = currentIndex === 1 ? "all" : "issues"
                if (backend.reviewFilter !== filter) backend.setReviewFilter(filter)
            }
            background: Rectangle { color: Theme.disabled; radius: 7 }
            PageControls.NavigationTab {
                id: reviewIssuesMode; text: qsTr("Зауваження"); objectName: "reviewIssuesFilter"; implicitHeight: 38
                implicitWidth: contentItem.implicitWidth + 24
                contentItem: PageControls.LabelText { text: reviewIssuesMode.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: reviewIssuesMode.checked ? shell.accent : shell.muted }
            }
            PageControls.NavigationTab {
                id: reviewAllMode; text: qsTr("Увесь текст"); objectName: "reviewAllFilter"; implicitHeight: 38
                implicitWidth: contentItem.implicitWidth + 24
                contentItem: PageControls.LabelText { text: reviewAllMode.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: reviewAllMode.checked ? shell.accent : shell.muted }
            }
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
            Layout.preferredWidth: reviewModes.width; Layout.minimumWidth: 240; Layout.fillHeight: true
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
                RowLayout {
                    Layout.fillWidth: true; Layout.alignment: Qt.AlignHCenter; spacing: 4
                    PageControls.Action { text: ""; iconName: "chevron-left"; hint: qsTr("Попередній запис"); objectName: "reviewPreviousButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; enabled: !shell.reviewLocked && shell.reviewEntryIndex > 0; onClicked: backend.stepReviewEntry(-1); Accessible.name: hint }
                    PageControls.Caption { text: (shell.reviewEntryIndex + 1) + " / " + backend.reviewEntries.length; Layout.minimumWidth: 46; horizontalAlignment: Text.AlignHCenter }
                    PageControls.Action { text: ""; iconName: "chevron-right"; hint: qsTr("Наступний запис"); objectName: "reviewNextButton"; subtle: true; implicitWidth: 34; implicitHeight: 32; enabled: !shell.reviewLocked && shell.reviewEntryIndex >= 0 && shell.reviewEntryIndex < backend.reviewEntries.length - 1; onClicked: backend.stepReviewEntry(1); Accessible.name: hint }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            visible: !!backend.selectedReviewEntry.id
            PageControls.Panel {
                id: explanationsPanel
                Layout.fillWidth: true
                Layout.fillHeight: backend.selectedReviewEntry.kind === "document"
                Layout.preferredHeight: backend.selectedReviewEntry.kind === "document" ? 1 : Math.min(112, Math.max(54, explanationsText.implicitHeight + 20))
                Layout.minimumHeight: 54
                visible: (backend.selectedReviewEntry.messages || []).length > 0
                color: backend.selectedReviewEntry.accepted ? Theme.successFill : backend.selectedReviewEntry.severity === "error" ? Theme.dangerFill : backend.selectedReviewEntry.severity === "warning" ? Theme.warningFill : Theme.surface
                border.color: backend.selectedReviewEntry.accepted ? Theme.successLine : backend.selectedReviewEntry.severity === "error" ? Theme.dangerLine : backend.selectedReviewEntry.severity === "warning" ? Theme.warningLine : shell.line
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 10
                    Icon { name: backend.selectedReviewEntry.accepted ? "check" : backend.selectedReviewEntry.severity === "info" ? "info" : "attention"; color: backend.selectedReviewEntry.accepted ? Theme.success : backend.selectedReviewEntry.severity === "error" ? Theme.danger : backend.selectedReviewEntry.severity === "warning" ? Theme.warning : shell.muted; Layout.alignment: Qt.AlignTop }
                    ScrollView {
                        id: explanationsScroll
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                        ScrollBar.vertical: PageControls.ReadingScrollBar { }
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        PageControls.ReadOnlyText { id: explanationsText; objectName: "reviewExplanationText"; width: explanationsScroll.availableWidth; text: (backend.selectedReviewEntry.accepted ? qsTr("Позначено як коректне") + "\n\n" : "") + (backend.selectedReviewEntry.messages || []).join("\n\n") + (backend.selectedReviewEntry.severity === "error" ? "\n\n" + qsTr("Помилку потрібно виправити; позначити її коректною не можна.") : ""); onTextChanged: if (explanationsScroll.contentItem) explanationsScroll.contentItem.contentY = 0; font.pixelSize: 13; Accessible.name: qsTr("Пояснення до вибраного запису") }
                    }
                    PageControls.Action { text: qsTr("Це коректно"); iconName: "check"; objectName: "reviewConfirmButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.canAccept && !backend.selectedReviewEntry.accepted; enabled: !shell.reviewLocked; onClicked: backend.confirmReviewEntry(); hint: qsTr("Позначити цей запис як перевірений. Він залишиться у вкладці «Увесь текст»"); Layout.alignment: Qt.AlignTop }
                    PageControls.Action { text: qsTr("Повернути зауваження"); iconName: "undo"; objectName: "reviewReopenButton"; implicitHeight: 30; visible: !!backend.selectedReviewEntry.accepted; enabled: !shell.reviewLocked; onClicked: backend.reopenReviewEntry(); Layout.alignment: Qt.AlignTop }
                }
            }
            GridLayout {
                id: textPanes
                Layout.fillWidth: true; Layout.fillHeight: true; columnSpacing: 12; rowSpacing: 10
                columns: width >= 520 ? 2 : 1
                visible: backend.selectedReviewEntry.kind !== "document"
                PageControls.ReviewTextPanel { heading: qsTr("ОРИГІНАЛ"); text: backend.selectedReviewEntry.source || ""; textObjectName: "reviewSourceText"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0; Layout.preferredHeight: textPanes.columns === 1 ? 1 : -1 }
                PageControls.ReviewTextPanel {
                    id: translationPane
                    heading: qsTr("ПЕРЕКЛАД"); text: backend.reviewEditing ? backend.reviewDraftText : backend.selectedReviewEntry.translation || ""
                    textObjectName: "reviewTranslationText"; editing: backend.reviewEditing; edited: !!backend.selectedReviewEntry.edited
                    editError: backend.reviewEditing ? backend.reviewEditError : ""
                    onDraftEdited: function(value) { if (backend.reviewEditing && value !== backend.reviewDraftText) backend.updateReviewDraft(value) }
                    footerContent: [
                        PageControls.Action { text: qsTr("Редагувати"); iconName: "edit"; objectName: "reviewEditButton"; subtle: true; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !!backend.selectedReviewEntry.canEdit && !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.beginReviewEdit(); hint: backend.selectedReviewEntry.editReason || qsTr("Редагувати текст перекладу цього фрагмента") },
                        PageControls.Action { text: qsTr("Зберегти правку"); iconName: "check"; objectName: "reviewApplyButton"; primary: true; implicitHeight: 32; leftPadding: 10; rightPadding: 10; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.applyReviewEdit(); hint: qsTr("Зберегти правку в чернетці") + " (Ctrl+Enter)" },
                        PageControls.Action { text: translationPane.width >= 380 ? qsTr("Скасувати") : ""; hint: qsTr("Скасувати"); Accessible.name: hint; iconName: "close"; objectName: "reviewCancelEditButton"; implicitHeight: 32; leftPadding: 10; rightPadding: 10; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.cancelReviewEdit() },
                        PageControls.Action {
                            objectName: "reviewMoreActionsButton"
                            text: translationPane.width >= 380 ? qsTr("Інші дії") : ""; hint: qsTr("Інші дії"); iconName: "chevron-down"; subtle: true; implicitHeight: 32
                            visible: !backend.reviewEditing && ((!backend.reviewDocument.recovery && !!backend.memoryProject.id) || !!backend.selectedReviewEntry.edited)
                            enabled: !shell.reviewLocked
                            onClicked: secondaryActions.open()
                            Menu {
                                id: secondaryActions
                                MenuItem { text: qsTr("Запам’ятати формулювання"); objectName: "rememberReviewWordingButton"; visible: !backend.reviewDocument.recovery && !!backend.memoryProject.id; enabled: backend.canRememberReviewWording; onClicked: shell.showRememberWordingDialog() }
                                MenuItem { text: qsTr("Скасувати збережену правку цього фрагмента"); objectName: "reviewDiscardEditButton"; visible: !!backend.selectedReviewEntry.edited; enabled: !shell.reviewLocked; onClicked: backend.discardReviewEdit() }
                            }
                        }
                    ]
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0
                    Layout.preferredHeight: textPanes.columns === 1 ? 2 : -1
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
