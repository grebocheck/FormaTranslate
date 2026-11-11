import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: page
    required property var shell
    spacing: 12
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PageControls.Caption { text: qsTr("Записів: %1").arg(backend.historyEntries.length); Layout.fillWidth: true }
        PageControls.Action { text: backend.historyLoading ? qsTr("Шукаємо переклади…") : qsTr("Імпортувати з папки"); iconName: "import"; objectName: "importHistoryButton"; enabled: !backend.historyLoading; onClicked: backend.importHistory(); hint: qsTr("Знайти попередні переклади в поточній папці результатів") }
        PageControls.Action { text: qsTr("Папка результатів"); iconName: "folder"; onClicked: backend.openOutputFolder() }
    }
    PageControls.Caption { text: qsTr("Історія зберігається на цьому комп’ютері без вмісту документів. Повторний переклад використовує поточні налаштування."); Layout.fillWidth: true; wrapMode: Text.WordWrap }
    ListView {
        id: historyList
        objectName: "historyList"
        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10
        model: backend.historyEntries
        ScrollBar.vertical: ScrollBar { }
        delegate: PageControls.Panel {
            id: historyRow
            required property var modelData
            width: ListView.view.width
            height: historyBody.implicitHeight + 32
            ColumnLayout {
                id: historyBody
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16; spacing: 10
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Icon { name: "document"; color: shell.muted }
                    PageControls.LabelText { text: historyRow.modelData.name; Layout.fillWidth: true; font.weight: Font.DemiBold; elide: Text.ElideMiddle }
                    PageControls.Caption { text: historyRow.modelData.format || ""; font.pixelSize: 11 }
                    Rectangle {
                        implicitWidth: historyStatusText.implicitWidth + 18; implicitHeight: 26; radius: 5
                        color: shell.historyColor(historyRow.modelData, true)
                        PageControls.Caption { id: historyStatusText; anchors.centerIn: parent; text: shell.historyStatus(historyRow.modelData); color: shell.historyColor(historyRow.modelData, false); font.weight: Font.DemiBold }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    PageControls.Caption { text: shell.historyDate(historyRow.modelData.finishedAt || historyRow.modelData.startedAt) }
                    PageControls.Caption { text: shell.languageName(historyRow.modelData.sourceLanguage) + " → " + shell.languageName(historyRow.modelData.targetLanguage) }
                    PageControls.Caption { text: historyRow.modelData.model || qsTr("Модель не вказано"); Layout.fillWidth: true; elide: Text.ElideRight }
                    PageControls.Caption { text: shell.duration(historyRow.modelData.elapsedSeconds || 0) }
                }
                PageControls.Caption {
                    visible: !!historyRow.modelData.error || historyRow.modelData.warningCount > 0 || (historyRow.modelData.status === "done" && !historyRow.modelData.outputExists)
                    text: historyRow.modelData.error || (historyRow.modelData.warningCount > 0 ? qsTr("Зауважень у звіті: %1").arg(historyRow.modelData.warningCount) : qsTr("Файл результату переміщено або видалено."))
                    color: historyRow.modelData.error ? Theme.danger : Theme.warning; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    PageControls.Action { text: qsTr("Відкрити переклад"); iconName: "external"; objectName: "historyOpenButton"; implicitHeight: 34; enabled: !!historyRow.modelData.outputExists; onClicked: backend.openHistoryOutput(historyRow.modelData.id) }
                    PageControls.Action { text: qsTr("Перевірити"); iconName: "eye"; objectName: "historyReviewButton"; implicitHeight: 34; enabled: !!historyRow.modelData.reviewAvailable && !shell.reviewLocked; onClicked: { backend.reviewHistory(historyRow.modelData.id); shell.page = 1 } }
                    PageControls.Action { text: qsTr("Папка"); iconName: "folder"; objectName: "historyFolderButton"; implicitHeight: 34; enabled: !!historyRow.modelData.output; onClicked: backend.openHistoryFolder(historyRow.modelData.id) }
                    PageControls.Action { text: qsTr("Звіт"); iconName: "review"; objectName: "historyReportButton"; implicitHeight: 34; enabled: !!historyRow.modelData.reportExists; onClicked: backend.openHistoryReport(historyRow.modelData.id) }
                    Item { Layout.fillWidth: true }
                    PageControls.Action { text: qsTr("У чергу"); iconName: "refresh"; objectName: "historyRequeueButton"; implicitHeight: 34; enabled: backend.canAddDocuments && !!historyRow.modelData.sourceExists; onClicked: backend.requeueHistory(historyRow.modelData.id); hint: historyRow.modelData.sourceExists ? qsTr("Додати оригінал до черги з поточними налаштуваннями") : qsTr("Оригінальний файл переміщено або видалено") }
                    PageControls.Action { text: qsTr("Прибрати"); iconName: "trash"; objectName: "historyForgetButton"; subtle: true; implicitHeight: 34; enabled: !backend.historyLoading; onClicked: backend.forgetHistory(historyRow.modelData.id); hint: qsTr("Прибрати запис з історії. Файли залишаться на диску") }
                }
            }
        }
        ColumnLayout {
            anchors.centerIn: parent; width: Math.min(430, parent.width - 40); spacing: 12
            visible: backend.historyEntries.length === 0
            Icon { name: "history"; Layout.preferredWidth: 38; Layout.preferredHeight: 38; color: Theme.muted; Layout.alignment: Qt.AlignHCenter }
            PageControls.Heading { text: backend.historyLoading ? qsTr("Шукаємо попередні переклади…") : qsTr("Тут будуть ваші переклади"); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
            PageControls.Caption { text: qsTr("Завершені переклади з’являються тут автоматично. Попередні результати можна імпортувати з вибраної папки."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 1.3 }
        }
    }
}
