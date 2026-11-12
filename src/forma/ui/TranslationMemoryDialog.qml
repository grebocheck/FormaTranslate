import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: dialog
    required property var controller
    signal editGlossaryRequested()
    property string sourceLanguageCode: "auto"
    property string targetLanguageCode: "en"
    property string approvalReferenceKey: JSON.stringify([controller.memoryProject.id || "", controller.memoryReference.source || "", controller.memoryReference.output || "", controller.memoryReference.report || ""])
    onApprovalReferenceKeyChanged: preferredReference.checked = false
    objectName: "translationMemoryDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(720, parent.width - 32)
    height: Math.min(600, parent.height - 32)
    padding: 22
    modal: true; focus: true
    closePolicy: controller.memoryWorking ? Popup.NoAutoClose : Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }
    onOpened: controller.prepareMemoryReference()

    component Copy: Text { font.family: Theme.fontFamily;
        Layout.fillWidth: true
        color: Theme.ink; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
    }
    component Field: AppField { }
    component Action: AppButton { }
    contentItem: ColumnLayout {
        spacing: 12
        Copy { text: qsTr("Пам’ять перекладів"); font.pixelSize: 21; font.weight: Font.Medium }
        ScrollView {
            id: scroll
            objectName: "translationMemoryScroll"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded; active: true; minimumSize: 0.08
                orientation: Qt.Vertical
                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                width: 8; implicitWidth: 8; padding: 1
                contentItem: Rectangle { implicitWidth: 6; implicitHeight: 28; radius: 3; color: Theme.scrollbar }
            }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: Math.max(0, scroll.availableWidth - 12); spacing: 10
                Copy { text: qsTr("Точні збіги з відповідним контекстом і правилами використовують готовий переклад. Глосарій спільний для документів проєкту."); color: Theme.muted }
                Copy { text: qsTr("Для початку достатньо назви проєкту та мов. Пам’ять поповнюється після перекладу автоматично; терміни й еталони можна додати пізніше."); color: Theme.muted }
                Copy { text: qsTr("Коли ввімкнено редакторську перевірку, схожі фрагменти адаптуються за зразком через API. Редакторський запит оплачується за тарифом провайдера."); color: Theme.muted; font.pixelSize: 12 }
                Copy { text: qsTr("Новий проєкт"); font.weight: Font.DemiBold; Layout.topMargin: 6 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Field { id: projectName; objectName: "memoryProjectNameField"; Layout.fillWidth: true; placeholderText: qsTr("Назва проєкту"); enabled: !dialog.controller.busy; maximumLength: 120; Accessible.name: qsTr("Назва проєкту") }
                    Action { text: qsTr("Створити"); objectName: "createMemoryProjectButton"; enabled: !dialog.controller.busy && projectName.text.trim().length > 0; onClicked: { if (dialog.controller.createMemoryProject(projectName.text, dialog.sourceLanguageCode, dialog.targetLanguageCode)) projectName.clear() } }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Copy { text: dialog.controller.memoryProject.name ? qsTr("Вибрано: %1").arg(dialog.controller.memoryProject.name) : qsTr("Спочатку створіть або виберіть проєкт у документах."); color: Theme.muted; Layout.minimumWidth: 0 }
                    Action {
                        text: qsTr("Видалити проєкт…"); objectName: "deleteMemoryProjectButton"; iconName: "trash"; danger: true; subtle: true
                        visible: !!dialog.controller.memoryProject.id
                        enabled: !dialog.controller.busy
                        onClicked: {
                            deleteConfirmation.projectId = dialog.controller.memoryProject.id
                            deleteConfirmation.projectName = dialog.controller.memoryProject.name
                            deleteConfirmation.open()
                        }
                    }
                }
                Copy { text: qsTr("Спільний глосарій"); font.weight: Font.DemiBold; Layout.topMargin: 6 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Copy { text: qsTr("Термінів: %1").arg(dialog.controller.glossaryCount); color: Theme.muted }
                    Action { text: qsTr("Редагувати терміни…"); enabled: !dialog.controller.busy && !!dialog.controller.memoryProject.id; onClicked: dialog.editGlossaryRequested() }
                }
                Copy { text: qsTr("Зміна глосарія діє для наступних запусків. Попередні результати залишаються без змін."); color: Theme.muted; font.pixelSize: 12 }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                CheckBox { id: showReferences; objectName: "showMemoryReferences"; text: qsTr("У мене вже є перевірений переклад (необов’язково)"); Layout.fillWidth: true; contentItem: Text { font.family: Theme.fontFamily; text: showReferences.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.pixelSize: 13; color: Theme.ink; leftPadding: showReferences.indicator.width + showReferences.spacing } }
                ColumnLayout {
                Layout.fillWidth: true; visible: showReferences.checked; spacing: 10
                Copy { text: qsTr("Додати перевірений переклад"); font.weight: Font.DemiBold }
                Copy { text: qsTr("Звіт .forma.json створюється поруч із перекладеним файлом Forma. Його не потрібно робити вручну."); color: Theme.muted; font.pixelSize: 12 }
                Copy { text: qsTr("Виберіть оригінал, переклад і звіт Forma до нього. Відповідність файлів перевіряється локально, без API."); color: Theme.muted }
                Copy { text: qsTr("Оригінальний DOCX") }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Field { objectName: "memoryReferenceSourceField"; Layout.fillWidth: true; text: dialog.controller.memoryReference.source || ""; enabled: !dialog.controller.busy; onTextEdited: dialog.controller.setMemoryReferencePath("source", text); Accessible.name: qsTr("Оригінальний DOCX") }
                    Action { text: qsTr("Вибрати…"); enabled: !dialog.controller.busy; onClicked: dialog.controller.pickMemoryReference("source") }
                }
                Copy { text: qsTr("Перекладений DOCX") }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Field { objectName: "memoryReferenceOutputField"; Layout.fillWidth: true; text: dialog.controller.memoryReference.output || ""; enabled: !dialog.controller.busy; onTextEdited: dialog.controller.setMemoryReferencePath("output", text); Accessible.name: qsTr("Перекладений DOCX") }
                    Action { text: qsTr("Вибрати…"); enabled: !dialog.controller.busy; onClicked: dialog.controller.pickMemoryReference("output") }
                }
                Copy { text: qsTr("Звіт Forma (.forma.json)") }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Field { objectName: "memoryReferenceReportField"; Layout.fillWidth: true; text: dialog.controller.memoryReference.report || ""; enabled: !dialog.controller.busy; onTextEdited: dialog.controller.setMemoryReferencePath("report", text); Accessible.name: qsTr("Звіт Forma (.forma.json)") }
                    Action { text: qsTr("Вибрати…"); enabled: !dialog.controller.busy; onClicked: dialog.controller.pickMemoryReference("report") }
                }
                CheckBox {
                    id: preferredReference; objectName: "memoryReferencePreferred"
                    text: qsTr("Я перевірив цей переклад: використовувати його формулювання в проєкті")
                    Layout.fillWidth: true; enabled: !dialog.controller.busy
                    indicator: Rectangle {
                        width: 18; height: 18; radius: 4
                        x: preferredReference.leftPadding; y: (preferredReference.height - height) / 2
                        color: preferredReference.checked ? Theme.accent : Theme.surface
                        border.color: preferredReference.visualFocus ? Theme.accent : Theme.lineStrong
                        Icon { anchors.centerIn: parent; name: "check"; width: 14; height: 14; visible: preferredReference.checked; color: Theme.accentText }
                    }
                    contentItem: Text { font.family: Theme.fontFamily; text: preferredReference.text; textFormat: Text.PlainText; font.pixelSize: 13; color: Theme.ink; wrapMode: Text.WordWrap; leftPadding: preferredReference.indicator.width + preferredReference.spacing }
                }
                Copy { text: qsTr("Без цієї позначки записи зберігаються лише як довідкові та не підставляються автоматично."); color: Theme.muted; font.pixelSize: 12 }
                Action { text: dialog.controller.memoryWorking ? qsTr("Перевіряємо еталон…") : qsTr("Додати еталон"); objectName: "importMemoryReferenceButton"; primary: true; Layout.alignment: Qt.AlignRight; enabled: !dialog.controller.busy && !!dialog.controller.memoryProject.id && !!dialog.controller.memoryReference.source && !!dialog.controller.memoryReference.output && !!dialog.controller.memoryReference.report; onClicked: dialog.controller.importMemoryReference(preferredReference.checked) }
                }
            }
        }
        Copy { objectName: "memoryDialogMessage"; text: dialog.controller.memoryMessage; visible: text.length > 0; color: dialog.controller.memoryMessageError ? Theme.danger : Theme.success }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Action { text: qsTr("Закрити"); objectName: "closeMemoryDialogButton"; enabled: !dialog.controller.memoryWorking; onClicked: dialog.close() }
        }
    }
    Popup {
        id: deleteConfirmation
        objectName: "deleteMemoryProjectDialog"
        property string projectId: ""
        property string projectName: ""
        property bool submitted: false
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(560, parent.width - 32); padding: 22
        modal: true; focus: true
        closePolicy: dialog.controller.memoryWorking ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
        Overlay.modal: Rectangle { color: Theme.overlay }
        onOpened: submitted = false
        Connections {
            target: dialog.controller
            function onMemoryProjectDeleted(projectId) {
                if (projectId === deleteConfirmation.projectId) deleteConfirmation.close()
            }
        }
        contentItem: ColumnLayout {
            spacing: 14
            Copy { objectName: "deleteMemoryProjectTitle"; text: qsTr("Видалити проєкт «%1»?").arg(deleteConfirmation.projectName); font.pixelSize: 21; font.weight: Font.DemiBold }
            Copy { text: qsTr("Буде назавжди видалено пам’ять перекладів цього проєкту, його глосарій і записи еталонів. Оригінали, перекладені файли та історія документів залишаться."); color: Theme.muted }
            Copy { text: dialog.controller.memoryMessage; visible: deleteConfirmation.submitted && dialog.controller.memoryMessageError; color: Theme.danger }
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                Item { Layout.fillWidth: true }
                Action { text: qsTr("Скасувати"); objectName: "cancelDeleteMemoryProjectButton"; enabled: !dialog.controller.memoryWorking; onClicked: deleteConfirmation.close() }
                Action {
                    text: dialog.controller.memoryWorking ? qsTr("Видаляємо…") : qsTr("Видалити назавжди")
                    objectName: "confirmDeleteMemoryProjectButton"; danger: true; iconName: "trash"
                    enabled: !dialog.controller.busy && !!deleteConfirmation.projectId
                    onClicked: { deleteConfirmation.submitted = true; dialog.controller.deleteMemoryProject(deleteConfirmation.projectId) }
                }
            }
        }
    }
}
