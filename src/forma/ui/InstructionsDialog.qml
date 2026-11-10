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
    background: Rectangle { color: "#ffffff"; radius: 12; border.color: "#d5dfed" }
    Overlay.modal: Rectangle { color: "#55202f45" }
    onOpened: {
        editor.text = controller.translationInstructions
        editor.forceActiveFocus()
    }
    component Copy: Text {
        Layout.fillWidth: true
        color: "#1a2b42"; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
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
        Copy { text: qsTr("Власні інструкції"); font.pixelSize: 22; font.weight: Font.DemiBold }
        Copy { text: qsTr("Опишіть бажані терміни, стиль і правила перекладу. Інструкції діють для наступних перекладів документів, тексту та фото, включно з редакторською перевіркою."); color: "#596779" }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 130
            radius: 7; color: "#f8faff"; border.color: editor.activeFocus ? "#2859c5" : "#bdc9d9"
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
                    color: "#1a2b42"; font.pixelSize: 14
                    selectionColor: "#2859c5"; selectedTextColor: "#ffffff"
                    height: Math.max(contentHeight, placeholder.implicitHeight)
                    Text {
                        id: placeholder
                        width: Math.max(0, editor.width - 10)
                        visible: editor.text.length === 0
                        color: "#66778c"; font: editor.font
                        wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        text: qsTr("Наприклад:\nДля англійської використовуйте британський правопис.\nДОДАТКИ перекладайте як APPENDICES.\nДотримуйтеся формального технічного стилю.")
                    }
                    Accessible.name: qsTr("Власні інструкції для перекладу")
                }
            }
        }
        Copy { text: qsTr("%1 / %2 символів").arg(editor.text.length).arg(dialog.controller.maximumInstructionsLength); color: editor.text.length > dialog.controller.maximumInstructionsLength ? "#a13434" : "#596779"; horizontalAlignment: Text.AlignRight; font.pixelSize: 12 }
        Copy { text: dialog.controller.instructionsError; visible: text.length > 0; color: "#a13434" }
        Copy { text: qsTr("Зберігаються на цьому пристрої та надсилаються вибраному AI-сервісу разом із запитами. Порожнє поле вимикає власні інструкції. Числа, коди й форматування залишаються захищеними."); color: "#596779"; font.pixelSize: 12 }
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
