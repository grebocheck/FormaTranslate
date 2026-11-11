import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: dialog
    required property var controller
    objectName: "instructionsDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(700, parent.width - 32)
    height: Math.min(560, parent.height - 32)
    padding: 22
    modal: true; focus: true
    closePolicy: Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }
    onOpened: {
        editor.text = controller.translationInstructions
        editor.forceActiveFocus()
    }
    component Copy: Text { font.family: Theme.fontFamily;
        Layout.fillWidth: true
        color: Theme.ink; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
    }
    component Action: AppButton { }
    contentItem: ColumnLayout {
        spacing: 12
        Copy { text: qsTr("Власні інструкції"); font.pixelSize: 21; font.weight: Font.Medium }
        Copy { text: qsTr("Опишіть бажані терміни, стиль і правила перекладу. Інструкції діють для наступних перекладів документів, тексту та фото, включно з редакторською перевіркою."); color: Theme.muted }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 130
            radius: 7; color: Theme.surfaceMuted; border.color: editor.activeFocus ? Theme.accent : Theme.lineStrong
            ScrollView {
                id: scroll
                anchors.fill: parent; anchors.margins: 10; clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                TextEdit {
                    id: editor; objectName: "translationInstructionsEditor"
                    width: scroll.availableWidth; padding: 0; rightPadding: 10
                    wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                    selectByMouse: true; persistentSelection: true
                    color: Theme.ink; font.pixelSize: 14
                    selectionColor: Theme.accent; selectedTextColor: Theme.accentText
                    height: Math.max(contentHeight, placeholder.implicitHeight)
                    Text {
                        id: placeholder
                        width: Math.max(0, editor.width - 10)
                        visible: editor.text.length === 0
                        color: Theme.muted; font: editor.font
                        wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        text: qsTr("Наприклад:\nДля англійської використовуйте британський правопис.\nДОДАТКИ перекладайте як APPENDICES.\nДотримуйтеся формального технічного стилю.")
                    }
                    Accessible.name: qsTr("Власні інструкції для перекладу")
                }
            }
        }
        Copy { text: qsTr("%1 / %2 символів").arg(editor.text.length).arg(dialog.controller.maximumInstructionsLength); color: editor.text.length > dialog.controller.maximumInstructionsLength ? Theme.danger : Theme.muted; horizontalAlignment: Text.AlignRight; font.pixelSize: 12 }
        Copy { text: dialog.controller.instructionsError; visible: text.length > 0; color: Theme.danger }
        Copy { text: qsTr("Зберігаються на цьому пристрої та надсилаються вибраному AI-сервісу разом із запитами. Порожнє поле вимикає власні інструкції. Числа, коди й форматування залишаються захищеними."); color: Theme.muted; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Item { Layout.fillWidth: true }
            Action { text: qsTr("Скасувати"); objectName: "cancelInstructionsButton"; onClicked: dialog.close() }
            Action {
                text: qsTr("Зберегти"); primary: true; objectName: "saveInstructionsButton"
                enabled: !dialog.controller.busy && editor.text.length <= dialog.controller.maximumInstructionsLength
                onClicked: if (dialog.controller.saveTranslationInstructions(editor.text)) dialog.close()
            }
        }
    }
}
