import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: dialog
    required property var controller
    property bool csvOpen: false
    objectName: "glossaryDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(850, parent.width - 32)
    height: Math.min(670, parent.height - 32)
    padding: 22
    modal: true; focus: true
    closePolicy: controller.working ? Popup.NoAutoClose : Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }

    component Copy: Text { font.family: Theme.fontFamily;
        Layout.fillWidth: true; color: Theme.ink; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
    }
    component Action: AppButton { }
    component Field: AppField { }
    contentItem: ColumnLayout {
        spacing: 8
        Copy { text: qsTr("Глосарій (необов’язково)"); font.pixelSize: 21; font.weight: Font.Medium }
        Copy { text: qsTr("Це ваш список термінів і бажаних перекладів. Можна залишити порожнім: переклад працює і без глосарія."); color: Theme.muted }
        Copy { text: dialog.controller.projectName ? qsTr("Спільні терміни проєкту «%1»").arg(dialog.controller.projectName) : qsTr("Терміни для поточної черги документів"); font.weight: Font.DemiBold }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Action { text: dialog.controller.working ? qsTr("Пропонуємо терміни…") : qsTr("Запропонувати з документів"); objectName: "suggestGlossaryButton"; enabled: !dialog.controller.working && dialog.controller.documentCount > 0; onClicked: dialog.controller.suggest() }
            Item { Layout.fillWidth: true }
        }
        Copy { text: qsTr("Надсилає уривки DOCX/XLSX налаштованому AI-провайдеру. Запит оплачується за його тарифом."); color: Theme.muted; font.pixelSize: 12 }
        Copy { text: qsTr("Додайте DOCX/XLSX до черги для пропозицій термінів."); visible: dialog.controller.documentCount === 0; color: Theme.muted; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 20; spacing: 10
            Copy { text: qsTr("Термін оригіналу"); Layout.preferredWidth: 1; font.weight: Font.DemiBold }
            Copy { text: qsTr("Бажаний переклад"); Layout.preferredWidth: 1; font.weight: Font.DemiBold }
            Item { Layout.preferredWidth: 40 }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 90
            color: Theme.surfaceMuted; radius: 6; border.color: Theme.line
            ListView {
                id: rows
                objectName: "glossaryTermList"
                anchors.fill: parent; anchors.margins: 8; clip: true; spacing: 8
                model: dialog.controller.rows
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: RowLayout {
                    required property int index
                    required property string sourceTerm
                    required property string targetTerm
                    width: rows.width - 12; height: 40; spacing: 10
                    Field { objectName: "glossarySourceTerm"; Layout.fillWidth: true; Layout.preferredWidth: 1; text: sourceTerm; placeholderText: qsTr("Термін мовою оригіналу"); enabled: !dialog.controller.working; onTextEdited: dialog.controller.setTerm(index, "source", text); Accessible.name: qsTr("Термін оригіналу") }
                    Field { objectName: "glossaryTargetTerm"; Layout.fillWidth: true; Layout.preferredWidth: 1; text: targetTerm; placeholderText: qsTr("Як його перекладати"); enabled: !dialog.controller.working; onTextEdited: dialog.controller.setTerm(index, "target", text); Accessible.name: qsTr("Бажаний переклад") }
                    Action { text: ""; iconName: "trash"; subtle: true; danger: true; objectName: "removeGlossaryTermButton"; Layout.preferredWidth: 40; enabled: !dialog.controller.working; onClicked: dialog.controller.removeTerm(index); Accessible.name: qsTr("Видалити термін") }
                }
            }
            Copy {
                anchors.centerIn: parent; width: Math.max(0, parent.width - 32)
                visible: dialog.controller.rows.count === 0
                text: qsTr("Готовий файл не потрібен. Натисніть «Додати термін» або «Запропонувати з документів».")
                horizontalAlignment: Text.AlignHCenter; color: Theme.muted
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Action { text: qsTr("Додати термін"); iconName: "add"; objectName: "addGlossaryTermButton"; enabled: !dialog.controller.working; onClicked: { dialog.controller.addTerm(); Qt.callLater(function() { rows.positionViewAtEnd() }) } }
            Item { Layout.fillWidth: true }
            Action { text: qsTr("CSV…"); subtle: true; objectName: "glossaryCsvOptionsButton"; enabled: !dialog.controller.working; onClicked: dialog.csvOpen = !dialog.csvOpen }
        }
        RowLayout {
            visible: dialog.csvOpen; Layout.fillWidth: true; spacing: 10
            Action { text: qsTr("Імпортувати CSV"); objectName: "importGlossaryCsvButton"; enabled: !dialog.controller.working; onClicked: dialog.controller.pickImport() }
            Action { text: qsTr("Експортувати CSV"); objectName: "exportGlossaryCsvButton"; enabled: !dialog.controller.working && dialog.controller.rows.count > 0; onClicked: dialog.controller.pickExport() }
            Item { Layout.fillWidth: true }
        }
        Copy { text: qsTr("CSV — необов’язковий спосіб перенести список. Імпорт не замінює наявні переклади."); visible: dialog.csvOpen; color: Theme.muted; font.pixelSize: 12 }
        Copy { text: dialog.controller.message; visible: text.length > 0; color: dialog.controller.error ? Theme.danger : Theme.success; objectName: "glossaryMessage" }
        Copy { text: qsTr("Чернетку ще не застосовано. Закриття вікна збереже її до наступного відкриття в цій сесії."); visible: dialog.controller.dirty; color: Theme.warning; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Action { text: qsTr("Скасувати правки"); objectName: "discardGlossaryButton"; enabled: !dialog.controller.working && dialog.controller.dirty; onClicked: dialog.controller.discard() }
            Item { Layout.fillWidth: true }
            Action { text: dialog.controller.working ? qsTr("Зупинити") : qsTr("Закрити"); objectName: "closeGlossaryButton"; onClicked: dialog.controller.working ? dialog.controller.cancel() : dialog.close() }
            Action { text: qsTr("Застосувати глосарій"); objectName: "applyGlossaryButton"; primary: true; enabled: !dialog.controller.working && dialog.controller.dirty; onClicked: dialog.controller.apply() }
        }
    }
}
