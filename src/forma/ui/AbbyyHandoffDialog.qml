import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: dialog
    required property var controller
    property var handoff: ({})
    readonly property bool continuingTranslation: !!handoff.continueTranslation
    readonly property bool canImport: !controller.busy || (continuingTranslation && !handoff.importing)
    objectName: "abbyyHandoffDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(680, parent.width - 40)
    padding: 24
    modal: true; focus: true
    closePolicy: controller.busy && !continuingTranslation ? Popup.NoAutoClose : Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }
    onOpened: reviewed.checked = false
    onClosed: if (typeof controller.dismissAbbyyHandoff === "function") controller.dismissAbbyyHandoff()

    component Copy: Text {
        Layout.fillWidth: true
        font.family: Theme.fontFamily; font.pixelSize: 13
        color: Theme.ink; wrapMode: Text.WordWrap; textFormat: Text.PlainText
    }
    contentItem: ColumnLayout {
        spacing: 18
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Icon { name: "document"; color: Theme.accent; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
            Copy { text: qsTr("Підготувати DOCX в ABBYY"); font.pixelSize: 21; font.weight: Font.Medium }
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: fileName.implicitHeight + 24
            color: Theme.surfaceMuted; radius: 7
            Copy { id: fileName; anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 12; anchors.verticalCenter: parent.verticalCenter; text: dialog.handoff.name || ""; font.weight: Font.Medium }
        }
        Copy { text: qsTr("У FineReader відкрийте PDF, розпізнайте його та збережіть як документ Word (.docx)."); lineHeight: 1.35 }
        Copy { objectName: "abbyyReviewInstructions"; text: dialog.continuingTranslation ? qsTr("Перевірте текст, числа й таблиці у створеному DOCX. Після імпорту програма перекладе його в межах цього завдання.") : qsTr("Перевірте текст, числа й таблиці у створеному DOCX. Потім імпортуйте його сюди — він замінить PDF у черзі."); color: Theme.muted; lineHeight: 1.35 }
        CheckBox {
            id: reviewed
            objectName: "abbyyResultReviewed"
            Layout.fillWidth: true
            contentItem: Text {
                text: qsTr("Я перевірив розпізнаний текст і таблиці")
                font.family: Theme.fontFamily; font.pixelSize: 13; color: Theme.ink
                wrapMode: Text.WordWrap; textFormat: Text.PlainText
                leftPadding: reviewed.indicator.width + reviewed.spacing
            }
        }
        Copy { objectName: "abbyyContinuationText"; text: dialog.continuingTranslation ? qsTr("Після імпорту переклад продовжиться автоматично.") : qsTr("Переклад почнеться окремо, коли ви натиснете «Перекласти»."); color: Theme.muted; font.pixelSize: 12 }
        Copy { objectName: "abbyyHandoffError"; text: dialog.controller.message; visible: dialog.controller.messageKind === "error" && text.length > 0; color: Theme.danger }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            AppButton { text: qsTr("Копіювати шлях PDF"); iconName: "copy"; subtle: true; objectName: "copyAbbyySourcePathButton"; onClicked: dialog.controller.copyAbbyySourcePath() }
            Item { Layout.fillWidth: true }
            AppButton { text: qsTr("Пізніше"); objectName: "closeAbbyyHandoffButton"; enabled: !dialog.controller.busy || dialog.continuingTranslation; onClicked: dialog.close() }
            AppButton { text: dialog.continuingTranslation ? qsTr("Імпортувати й продовжити") : qsTr("Імпортувати DOCX…"); iconName: "import"; primary: true; objectName: "confirmImportAbbyyResultButton"; enabled: reviewed.checked && dialog.canImport; onClicked: dialog.controller.importAbbyyResult(reviewed.checked) }
        }
    }
}
