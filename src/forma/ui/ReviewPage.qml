import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    property bool ready: false
    readonly property string navigationKey: backend.reviewDocumentId + "\n" + backend.reviewFilter + "\n" + backend.reviewSearch
    spacing: 10

    function locationLabel(entry) {
        var label = entry.location || qsTr("Документ")
        if (entry.id && entry.id.indexOf("word/document.xml#") === 0 && label.indexOf("document · ") === 0)
            return qsTr("Документ") + label.substring("document".length)
        return label
    }

    function revealCurrent() {
        if (ready && reviewList.currentIndex >= 0 && !backend.reviewEditing)
            reviewList.positionViewAtIndex(reviewList.currentIndex, ListView.Contain)
    }
    onNavigationKeyChanged: if (ready) Qt.callLater(revealCurrent)
    Component.onCompleted: { ready = true; Qt.callLater(revealCurrent) }
    Connections {
        target: backend
        function onRecoveryStarted() { backend.setWorkspaceMode("translation") }
    }
    Shortcut {
        sequences: ["Ctrl+Return", "Ctrl+Enter"]
        enabled: page.visible && backend.reviewEditing && reviewList.currentItem && reviewList.currentItem.editorHasFocus && !backend.reviewSaving && !backend.memoryWorking
        onActivated: backend.applyReviewEdit()
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        TabBar {
            id: reviewModes; objectName: "reviewModes"; enabled: !shell.reviewLocked
            Layout.preferredWidth: Math.max(220, reviewAllMode.implicitWidth + reviewIssuesMode.implicitWidth)
            implicitHeight: 36; currentIndex: backend.reviewFilter === "all" ? 0 : 1
            background: Rectangle { color: Theme.disabled; radius: 7 }
            PageControls.NavigationTab {
                id: reviewAllMode; text: qsTr("Увесь текст"); objectName: "reviewAllFilter"; implicitHeight: 36; implicitWidth: contentItem.implicitWidth + 20
                onClicked: backend.setReviewFilter("all")
                contentItem: PageControls.LabelText { text: reviewAllMode.text; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: reviewAllMode.checked ? Theme.accent : Theme.muted }
            }
            PageControls.NavigationTab {
                id: reviewIssuesMode; text: qsTr("Зауваження"); objectName: "reviewIssuesFilter"; implicitHeight: 36; implicitWidth: contentItem.implicitWidth + 20
                onClicked: backend.setReviewFilter("issues")
                contentItem: PageControls.LabelText { text: reviewIssuesMode.text + (backend.reviewSummary.attention > 0 ? " · " + backend.reviewSummary.attention : ""); font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: reviewIssuesMode.checked ? Theme.accent : Theme.muted }
            }
        }
        PageControls.Field {
            id: reviewSearchField; objectName: "reviewSearchField"; enabled: !shell.reviewLocked
            Layout.fillWidth: true; Layout.minimumWidth: 80; implicitHeight: 36
            placeholderText: qsTr("Пошук у документі"); text: backend.reviewSearch
            onTextEdited: backend.setReviewSearch(text)
            Accessible.name: qsTr("Пошук у документі"); rightPadding: 34
            PageControls.Action { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: 2; text: ""; iconName: "close"; hint: qsTr("Очистити пошук"); subtle: true; implicitHeight: 28; implicitWidth: 28; visible: reviewSearchField.text.length > 0; onClicked: backend.setReviewSearch(""); Accessible.name: hint }
        }
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 7
        PageControls.Caption {
            text: backend.reviewEditing ? qsTr("Редагування") + " · " + page.locationLabel(backend.selectedReviewEntry) : qsTr("Фрагментів: %1").arg(backend.reviewSummary.segments || 0) + (backend.reviewSummary.attention > 0 ? " · " + qsTr("Потребують уваги: %1").arg(backend.reviewSummary.attention) : "")
            Layout.fillWidth: true; elide: Text.ElideRight; color: backend.reviewEditing ? Theme.accent : Theme.muted
        }
        PageControls.Action { text: ""; iconName: "chevron-left"; hint: qsTr("Попередній запис"); objectName: "reviewPreviousButton"; subtle: true; implicitWidth: 30; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !shell.reviewLocked && shell.reviewEntryIndex > 0; onClicked: backend.stepReviewEntry(-1); Accessible.name: hint }
        PageControls.Action { text: ""; iconName: "chevron-right"; hint: qsTr("Наступний запис"); objectName: "reviewNextButton"; subtle: true; implicitWidth: 30; implicitHeight: 32; visible: !backend.reviewEditing; enabled: !shell.reviewLocked && shell.reviewEntryIndex >= 0 && shell.reviewEntryIndex < backend.reviewEntries.length - 1; onClicked: backend.stepReviewEntry(1); Accessible.name: hint }
        PageControls.Action { text: qsTr("Зберегти правку"); iconName: "check"; objectName: "reviewApplyButton"; primary: true; implicitHeight: 34; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.applyReviewEdit(); hint: qsTr("Зберегти правку в чернетці") + " (Ctrl+Enter)" }
        PageControls.Action { text: page.width >= 700 ? qsTr("Скасувати") : ""; hint: qsTr("Скасувати"); Accessible.name: hint; iconName: "close"; objectName: "reviewCancelEditButton"; implicitHeight: 34; visible: backend.reviewEditing; enabled: !backend.reviewSaving; onClicked: backend.cancelReviewEdit() }
        PageControls.Caption { text: qsTr("Правок: %1").arg(backend.reviewSummary.edited || 0); color: Theme.accent; visible: backend.reviewHasEdits && !backend.reviewEditing }
        PageControls.Action {
            text: backend.reviewSaving ? qsTr("Зберігаємо…") : backend.reviewDocument.recovery ? qsTr("Продовжити переклад") : qsTr("Зберегти копію")
            iconName: backend.reviewDocument.recovery ? "play" : "save"; objectName: "reviewExportButton"; primary: true; implicitHeight: 34
            visible: !backend.reviewEditing && (backend.reviewHasEdits || !!backend.reviewDocument.recovery || backend.reviewSaving)
            enabled: (backend.reviewDocument.recovery ? backend.reviewDocument.canResume : backend.reviewHasEdits) && !shell.reviewLocked
            onClicked: backend.exportReviewEdits()
            hint: backend.reviewDocument.recovery ? qsTr("Перевірити правки й продовжити переклад цього документа") : qsTr("Створити виправлену копію. Попередній переклад залишиться без змін")
        }
    }
    PageControls.Caption {
        text: backend.reviewDocument.recoveryReason || qsTr("Виправте проблемні фрагменти та продовжіть переклад. Готові фрагменти буде використано повторно.")
        visible: !!backend.reviewDocument.recovery; Layout.fillWidth: true; wrapMode: Text.WordWrap
    }
    PageControls.Caption { text: backend.reviewActionError; objectName: "reviewActionError"; visible: text.length > 0; color: Theme.danger; Layout.fillWidth: true; wrapMode: Text.WordWrap }

    Item {
        Layout.fillWidth: true; Layout.fillHeight: true
        ListView {
            id: reviewList; objectName: "reviewEntryList"
            anchors.fill: parent
            visible: count > 0; clip: true; spacing: 10; cacheBuffer: Math.max(0, height)
            model: backend.reviewEntryModel; currentIndex: shell.reviewEntryIndex
            highlightFollowsCurrentItem: false; keyNavigationEnabled: false; reuseItems: false
            boundsBehavior: Flickable.StopAtBounds
            onCurrentIndexChanged: if (page.ready) Qt.callLater(function() {
                if (!reviewList.currentItem || backend.reviewEditing) return
                var top = reviewList.currentItem.y
                if (top + reviewList.currentItem.height < reviewList.contentY || top > reviewList.contentY + reviewList.height)
                    page.revealCurrent()
            })
            ScrollBar.vertical: PageControls.ReadingScrollBar { }
            delegate: PageControls.Panel {
                id: card
                required property var modelData
                required property int index
                readonly property bool selected: modelData.id === backend.selectedReviewId
                readonly property bool editing: selected && backend.reviewEditing
                readonly property var entry: selected ? backend.selectedReviewEntry : modelData
                readonly property bool editorHasFocus: translationText.activeFocus
                readonly property color statusColor: entry.accepted ? Theme.success : entry.severity === "error" ? Theme.danger : entry.issueCount > 0 ? Theme.warning : entry.edited ? Theme.accent : Theme.muted
                objectName: "reviewCard_" + modelData.id
                width: ListView.view.width - 16; height: contents.implicitHeight + 24
                border.color: editing || selected ? Theme.accent : Theme.line
                border.width: editing ? 2 : 1
                color: Theme.surface
                function select() { if (!shell.reviewLocked) backend.selectReviewEntry(modelData.id) }
                onEditingChanged: if (editing) Qt.callLater(function() {
                    reviewList.positionViewAtIndex(card.index, ListView.Beginning)
                    translationText.forceActiveFocus()
                })
                TapHandler { acceptedButtons: Qt.LeftButton; onTapped: card.select() }
                ColumnLayout {
                    id: contents
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 12; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        PageControls.Caption { objectName: "reviewLocation_" + card.modelData.id; text: page.locationLabel(card.entry); Layout.fillWidth: true; elide: Text.ElideMiddle; font.weight: Font.DemiBold; color: Theme.ink }
                        Icon { name: card.entry.accepted ? "check" : card.entry.severity === "error" || card.entry.issueCount > 0 ? "attention" : card.entry.edited ? "edit" : "check"; color: card.statusColor; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
                        PageControls.Caption {
                            text: card.entry.accepted ? qsTr("Позначено як коректне") : card.entry.severity === "error" ? qsTr("Потрібне виправлення") : card.entry.issueCount > 0 ? qsTr("Зауважень: %1").arg(card.entry.issueCount) : card.entry.edited ? qsTr("Правку збережено") : qsTr("Без автоматичних зауважень")
                            color: card.statusColor; font.pixelSize: 11; Layout.maximumWidth: card.width * 0.52; elide: Text.ElideRight
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; Layout.preferredHeight: implicitHeight; spacing: 6
                        visible: card.selected && !card.editing
                        PageControls.Action { text: qsTr("Редагувати"); iconName: "edit"; objectName: card.selected ? "reviewEditButton" : "reviewEdit_" + card.modelData.id; subtle: true; implicitHeight: 30; visible: card.entry.kind === "segment"; enabled: !!card.entry.canEdit && !backend.reviewSaving && !backend.memoryWorking; onClicked: backend.beginReviewEdit(); hint: card.entry.editReason || qsTr("Редагувати текст перекладу цього фрагмента") }
                        PageControls.Action { text: qsTr("Це коректно"); iconName: "check"; objectName: card.selected ? "reviewConfirmButton" : "reviewConfirm_" + card.modelData.id; implicitHeight: 30; visible: !!card.entry.canAccept && !card.entry.accepted && card.entry.issueCount > 0; enabled: !shell.reviewLocked; onClicked: backend.confirmReviewEntry(); hint: qsTr("Позначити цей запис як перевірений. Він залишиться у вкладці «Увесь текст»") }
                        PageControls.Action { text: qsTr("Повернути зауваження"); iconName: "undo"; objectName: card.selected ? "reviewReopenButton" : "reviewReopen_" + card.modelData.id; implicitHeight: 30; visible: !!card.entry.accepted; enabled: !shell.reviewLocked; onClicked: backend.reopenReviewEntry() }
                        PageControls.Action {
                            objectName: card.selected ? "reviewMoreActionsButton" : "reviewMoreActions_" + card.modelData.id
                            text: qsTr("Інші дії"); hint: text; iconName: "chevron-down"; subtle: true; implicitHeight: 30
                            visible: (!backend.reviewDocument.recovery && !!backend.memoryProject.id) || !!card.entry.edited || !!card.entry.canAlignReferences
                            enabled: !shell.reviewLocked; onClicked: secondaryActions.open()
                            Menu {
                                id: secondaryActions
                                width: Math.min(shell.width - 40, Math.max(300, alignReferenceAction.implicitWidth + 24))
                                MenuItem { text: qsTr("Запам’ятати формулювання"); objectName: card.selected ? "rememberReviewWordingButton" : "rememberReview_" + card.modelData.id; visible: !backend.reviewDocument.recovery && !!backend.memoryProject.id; enabled: backend.canRememberReviewWording; onClicked: shell.showRememberWordingDialog() }
                                MenuItem { id: alignReferenceAction; text: qsTr("Позначення «%1» для всіх заголовків цього додатка").arg(card.entry.referenceLabel || ""); objectName: card.selected ? "reviewAlignReferencesButton" : "reviewAlign_" + card.modelData.id; visible: !!card.entry.canAlignReferences; enabled: !shell.reviewLocked; onClicked: backend.alignReviewReferences() }
                                MenuItem { text: qsTr("Скасувати збережену правку цього фрагмента"); objectName: card.selected ? "reviewDiscardEditButton" : "reviewDiscard_" + card.modelData.id; visible: !!card.entry.edited; enabled: !shell.reviewLocked; onClicked: backend.discardReviewEdit() }
                            }
                        }
                    }
                    Rectangle {
                        visible: (card.entry.messages || []).length > 0
                        Layout.fillWidth: true; implicitHeight: messages.implicitHeight + 16; radius: 6
                        color: card.entry.accepted ? Theme.successFill : card.entry.severity === "error" ? Theme.dangerFill : card.entry.severity === "warning" ? Theme.warningFill : Theme.surfaceMuted
                        ColumnLayout {
                            id: messages; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                            PageControls.ReadOnlyText {
                                objectName: card.selected ? "reviewExplanationText" : "reviewExplanation_" + card.modelData.id
                                visible: card.selected; Layout.fillWidth: true; font.pixelSize: 12
                                text: (card.entry.messages || []).join("\n\n") + (card.entry.severity === "error" ? "\n\n" + qsTr("Помилку потрібно виправити; позначити її коректною не можна.") : "")
                                Accessible.name: qsTr("Пояснення до вибраного запису")
                            }
                            PageControls.LabelText { visible: !card.selected; Layout.fillWidth: true; font.pixelSize: 12; text: (card.entry.messages || [])[0] || ""; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                        }
                    }
                    GridLayout {
                        id: pairs; columns: width >= 520 ? 2 : 1
                        Layout.fillWidth: true; columnSpacing: 12; rowSpacing: 10
                        visible: card.entry.kind !== "document"
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0; spacing: 5
                            PageControls.Caption { text: qsTr("ОРИГІНАЛ"); font.pixelSize: 11; Layout.fillWidth: true }
                            PageControls.ReadOnlyText {
                                id: sourceText
                                objectName: card.selected ? "reviewSourceText" : "reviewSource_" + card.modelData.id
                                text: card.entry.source || ""; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                                Layout.preferredHeight: Math.max(card.editing ? 140 : 24, contentHeight + topPadding + bottomPadding)
                                padding: 8; rightPadding: 8
                                background: Rectangle { color: Theme.surfaceMuted; radius: 5 }
                                onActiveFocusChanged: if (activeFocus) card.select()
                                Accessible.name: qsTr("ОРИГІНАЛ")
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.minimumWidth: 0; spacing: 5
                            PageControls.Caption { text: card.editing ? qsTr("Редагування") : qsTr("ПЕРЕКЛАД"); font.pixelSize: 11; Layout.fillWidth: true; color: card.editing ? Theme.accent : Theme.muted }
                            PageControls.ReadOnlyText {
                                id: translationText
                                objectName: card.selected ? "reviewTranslationText" : "reviewTranslation_" + card.modelData.id
                                text: card.editing ? backend.reviewDraftText : card.entry.translation || ""
                                readOnly: !card.editing; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                                Layout.preferredHeight: Math.max(card.editing ? 140 : 24, contentHeight + topPadding + bottomPadding)
                                padding: 8; rightPadding: 8
                                background: Rectangle { color: card.editing ? Theme.accentLight : Theme.surfaceMuted; radius: 5; border.color: card.editing ? Theme.accent : "transparent" }
                                function revealCursor() {
                                    if (!card.editing || !activeFocus) return
                                    var point = mapToItem(reviewList, cursorRectangle.x, cursorRectangle.y)
                                    var top = reviewList.contentY
                                    if (point.y < 8) top += point.y - 8
                                    else if (point.y + cursorRectangle.height > reviewList.height - 8)
                                        top += point.y + cursorRectangle.height - reviewList.height + 8
                                    reviewList.contentY = Math.max(reviewList.originY, Math.min(top, reviewList.originY + reviewList.contentHeight - reviewList.height))
                                }
                                onCursorRectangleChanged: if (card.editing && activeFocus) Qt.callLater(revealCursor)
                                onTextChanged: if (card.editing && text !== backend.reviewDraftText) backend.updateReviewDraft(text)
                                onActiveFocusChanged: if (activeFocus) card.select()
                                Accessible.name: qsTr("ПЕРЕКЛАД")
                            }
                        }
                    }
                    PageControls.ReadOnlyText {
                        objectName: card.selected ? "reviewTranslationTextError" : "reviewError_" + card.modelData.id
                        visible: card.editing && backend.reviewEditError.length > 0; text: card.editing ? backend.reviewEditError : ""
                        Layout.fillWidth: true; font.pixelSize: 12; color: Theme.danger
                        Accessible.name: qsTr("Помилка збереження правки")
                    }
                }
            }
        }
        PageControls.Panel {
            anchors.fill: parent
            visible: reviewList.count === 0
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
