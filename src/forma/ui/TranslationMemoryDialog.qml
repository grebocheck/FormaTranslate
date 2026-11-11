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
    background: Rectangle { color: "#ffffff"; radius: 12; border.color: "#d5dfed" }
    Overlay.modal: Rectangle { color: "#55202f45" }
    onOpened: controller.prepareMemoryReference()

    component Copy: Text {
        Layout.fillWidth: true
        color: "#1a2b42"; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
    }
    component Field: TextField {
        id: field
        implicitHeight: 40
        selectByMouse: true; color: "#1a2b42"; font.pixelSize: 13
        placeholderTextColor: "#66778c"
        leftPadding: 10; rightPadding: 10
        background: Rectangle { radius: 6; color: field.enabled ? "#ffffff" : "#f4f6f9"; border.color: field.activeFocus ? "#2859c5" : "#bdc9d9"; border.width: field.activeFocus ? 2 : 1 }
    }
    component Action: Button {
        id: action
        property bool primary: false
        implicitHeight: 40; padding: 12
        contentItem: Text {
            text: action.text; font.pixelSize: 13; font.weight: Font.DemiBold
            color: !action.enabled ? "#66778c" : action.primary ? "#ffffff" : "#1a2b42"
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText
        }
        background: Rectangle {
            radius: 6
            color: !action.enabled ? "#edf0f4" : action.primary ? (action.down ? "#173e94" : "#2859c5") : action.hovered ? "#edf3ff" : "#ffffff"
            border.color: action.visualFocus ? "#2859c5" : "#bdc9d9"
            border.width: action.visualFocus ? 2 : 1
        }
    }
    contentItem: ColumnLayout {
        spacing: 12
        Copy { text: qsTr("Пам’ять перекладів"); font.pixelSize: 22; font.weight: Font.DemiBold }
        ScrollView {
            id: scroll
            objectName: "translationMemoryScroll"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded; active: true; minimumSize: 0.08
                orientation: Qt.Vertical
                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                width: 8; implicitWidth: 8; padding: 1
                contentItem: Rectangle { implicitWidth: 6; implicitHeight: 28; radius: 3; color: "#a8b5c7" }
            }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: Math.max(0, scroll.availableWidth - 12); spacing: 10
                Copy { text: qsTr("Точні збіги з відповідним контекстом і правилами використовують готовий переклад. Глосарій спільний для документів проєкту."); color: "#596779" }
                Copy { text: qsTr("Для початку достатньо назви проєкту та мов. Пам’ять поповнюється після перекладу автоматично; терміни й еталони можна додати пізніше."); color: "#596779" }
                Copy { text: qsTr("Коли ввімкнено редакторську перевірку, схожі фрагменти адаптуються за зразком через API. Редакторський запит оплачується за тарифом провайдера."); color: "#596779"; font.pixelSize: 12 }
                Copy { text: qsTr("Новий проєкт"); font.weight: Font.DemiBold; Layout.topMargin: 6 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Field { id: projectName; objectName: "memoryProjectNameField"; Layout.fillWidth: true; placeholderText: qsTr("Назва проєкту"); enabled: !dialog.controller.busy; maximumLength: 120; Accessible.name: qsTr("Назва проєкту") }
                    Action { text: qsTr("Створити"); objectName: "createMemoryProjectButton"; enabled: !dialog.controller.busy && projectName.text.trim().length > 0; onClicked: { if (dialog.controller.createMemoryProject(projectName.text, dialog.sourceLanguageCode, dialog.targetLanguageCode)) projectName.clear() } }
                }
                Copy { text: dialog.controller.memoryProject.name ? qsTr("Вибрано: %1").arg(dialog.controller.memoryProject.name) : qsTr("Спочатку створіть або виберіть проєкт у документах."); color: "#596779" }
                Copy { text: qsTr("Спільний глосарій"); font.weight: Font.DemiBold; Layout.topMargin: 6 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Copy { text: qsTr("Термінів: %1").arg(dialog.controller.glossaryCount); color: "#596779" }
                    Action { text: qsTr("Редагувати терміни…"); enabled: !dialog.controller.busy && !!dialog.controller.memoryProject.id; onClicked: dialog.editGlossaryRequested() }
                }
                Copy { text: qsTr("Зміна глосарія діє для наступних запусків. Попередні результати залишаються без змін."); color: "#596779"; font.pixelSize: 12 }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#d5dfed"; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                CheckBox { id: showReferences; objectName: "showMemoryReferences"; text: qsTr("У мене вже є перевірений переклад (необов’язково)"); Layout.fillWidth: true; contentItem: Text { text: showReferences.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.pixelSize: 13; color: "#1a2b42"; leftPadding: showReferences.indicator.width + showReferences.spacing } }
                ColumnLayout {
                Layout.fillWidth: true; visible: showReferences.checked; spacing: 10
                Copy { text: qsTr("Додати перевірений переклад"); font.weight: Font.DemiBold }
                Copy { text: qsTr("Звіт .forma.json створюється поруч із перекладеним файлом Forma. Його не потрібно робити вручну."); color: "#596779"; font.pixelSize: 12 }
                Copy { text: qsTr("Виберіть оригінал, переклад і звіт Forma до нього. Відповідність файлів перевіряється локально, без API."); color: "#596779" }
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
                        color: preferredReference.checked ? "#2859c5" : "#ffffff"
                        border.color: preferredReference.visualFocus ? "#2859c5" : "#9caabe"
                        Text { anchors.centerIn: parent; text: "✓"; visible: preferredReference.checked; color: "#ffffff"; font.pixelSize: 13; font.weight: Font.Bold }
                    }
                    contentItem: Text { text: preferredReference.text; textFormat: Text.PlainText; font.pixelSize: 13; color: "#1a2b42"; wrapMode: Text.WordWrap; leftPadding: preferredReference.indicator.width + preferredReference.spacing }
                }
                Copy { text: qsTr("Без цієї позначки записи зберігаються лише як довідкові та не підставляються автоматично."); color: "#596779"; font.pixelSize: 12 }
                Action { text: dialog.controller.memoryWorking ? qsTr("Перевіряємо еталон…") : qsTr("Додати еталон"); objectName: "importMemoryReferenceButton"; primary: true; Layout.alignment: Qt.AlignRight; enabled: !dialog.controller.busy && !!dialog.controller.memoryProject.id && !!dialog.controller.memoryReference.source && !!dialog.controller.memoryReference.output && !!dialog.controller.memoryReference.report; onClicked: dialog.controller.importMemoryReference(preferredReference.checked) }
                }
            }
        }
        Copy { objectName: "memoryDialogMessage"; text: dialog.controller.memoryMessage; visible: text.length > 0; color: dialog.controller.memoryMessageError ? "#a13434" : "#226241" }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Action { text: qsTr("Закрити"); objectName: "closeMemoryDialogButton"; enabled: !dialog.controller.memoryWorking; onClicked: dialog.close() }
        }
    }
}
