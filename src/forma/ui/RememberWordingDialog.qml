import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: dialog
    required property var controller
    property bool submitted: false
    objectName: "rememberWordingDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent.width - 32)
    padding: 22
    modal: true; focus: true
    closePolicy: controller.memoryWorking ? Popup.NoAutoClose : Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }
    onOpened: submitted = false
    onAboutToHide: if (!submitted) dialog.controller.clearMemoryConflict()
    component Action: AppButton { }
    component Wording: TextArea {
        readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        color: Theme.ink; font.pixelSize: 13; padding: 10
        selectionColor: Theme.accent; selectedTextColor: Theme.accentText
        background: Rectangle { color: Theme.surfaceMuted; border.color: Theme.line; radius: 6 }
    }
    contentItem: ColumnLayout {
        spacing: 14
        Text { font.family: Theme.fontFamily; text: dialog.controller.memoryConflict.active ? qsTr("Замінити бажане формулювання?") : qsTr("Запам’ятати формулювання"); Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 21; font.weight: Font.DemiBold }
        Text { font.family: Theme.fontFamily; text: dialog.controller.memoryConflict.active ? qsTr("У проєкті вже є інший схвалений переклад. Замініть його, лише якщо новий варіант має діяти в наступних запусках.") : qsTr("Використовувати цей переклад для точних повторів у проєкті «%1»? Він матиме пріоритет над старими машинними варіантами в наступних запусках.").arg(dialog.controller.memoryProject.name || ""); Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 13 }
        Text { font.family: Theme.fontFamily; text: qsTr("Попереднє формулювання"); visible: !!dialog.controller.memoryConflict.active; Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
        ScrollView { id: previousScroll; visible: !!dialog.controller.memoryConflict.active; Layout.fillWidth: true; Layout.preferredHeight: 110; clip: true; contentWidth: availableWidth; Wording { objectName: "previousMemoryWordingText"; width: previousScroll.availableWidth; height: Math.max(implicitHeight, previousScroll.availableHeight); text: dialog.controller.memoryConflict.previous || "" } }
        Text { font.family: Theme.fontFamily; text: qsTr("Нове формулювання"); visible: !!dialog.controller.memoryConflict.active; Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
        ScrollView { id: replacementScroll; visible: !!dialog.controller.memoryConflict.active; Layout.fillWidth: true; Layout.preferredHeight: 110; clip: true; contentWidth: availableWidth; Wording { objectName: "replacementMemoryWordingText"; width: replacementScroll.availableWidth; height: Math.max(implicitHeight, replacementScroll.availableHeight); text: dialog.controller.memoryConflict.replacement || "" } }
        Text { font.family: Theme.fontFamily; text: qsTr("Поточний DOCX і позначки перевірки не зміняться. Застосовані правки чернетки враховуються у формулюванні."); visible: !dialog.controller.memoryConflict.active; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 13 }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Item { Layout.fillWidth: true }
            Action { text: qsTr("Скасувати"); objectName: "cancelRememberWordingButton"; enabled: !dialog.controller.memoryWorking; onClicked: dialog.close() }
            Action { text: dialog.controller.memoryConflict.active ? qsTr("Замінити формулювання") : qsTr("Використовувати в проєкті"); objectName: "confirmRememberWordingButton"; primary: true; enabled: (dialog.controller.memoryConflict.active || dialog.controller.canRememberReviewWording) && !dialog.controller.memoryWorking; onClicked: { dialog.submitted = true; if (dialog.controller.memoryConflict.active) dialog.controller.replaceReviewWording(); else dialog.controller.rememberReviewWording(); dialog.close() } }
        }
    }
}
