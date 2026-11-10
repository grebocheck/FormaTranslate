import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: panel
    required property var controller
    readonly property var stats: controller.summary
    objectName: "usagePanel"
    width: Math.min(560, parent.width - 32)
    height: Math.min(contentColumn.implicitHeight + 40, parent.height - 32)
    padding: 20
    modal: true
    focus: true
    anchors.centerIn: parent
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: "#ffffff"; radius: 12; border.color: "#d5dfed" }
    Overlay.modal: Rectangle { color: "#55202f45" }
    onOpened: {
        controller.refresh()
        const rates = stats.tariff || []
        inputRate.text = rates[0] || ""
        cacheRate.text = rates[1] || ""
        outputRate.text = rates[2] || ""
        balanceInput.text = ""
        editBalance.checked = false
        editTariff.checked = false
    }
    component Copy: Text {
        color: "#1a2b42"; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
        Layout.fillWidth: true
    }
    component Action: Button {
        id: action
        implicitHeight: 38
        padding: 10
        contentItem: Text {
            text: action.text; color: action.enabled ? "#204eaf" : "#66778c"
            font.pixelSize: 13; font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
        }
        background: Rectangle {
            radius: 6; color: action.down ? "#dfe9fc" : action.hovered ? "#f0f5ff" : "#ffffff"
            border.color: action.visualFocus ? "#2858bd" : "#bdc9d9"
            border.width: action.visualFocus ? 2 : 1
        }
    }
    component Field: TextField {
        Layout.fillWidth: true; Layout.minimumWidth: 50
        implicitHeight: 38; color: "#1a2b42"; font.pixelSize: 14
        selectByMouse: true; inputMethodHints: Qt.ImhFormattedNumbersOnly
        placeholderText: "0.00"; placeholderTextColor: "#66778c"
        selectionColor: "#2858bd"; selectedTextColor: "white"
        maximumLength: 18
        background: Rectangle { color: "#f8faff"; radius: 6; border.color: parent.activeFocus ? "#2858bd" : "#bdc9d9" }
    }
    contentItem: ScrollView {
        id: scroll
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: contentColumn
            width: scroll.availableWidth; spacing: 14
            RowLayout {
                Layout.fillWidth: true
                Copy { text: qsTr("Витрати API"); font.pixelSize: 20; font.weight: Font.DemiBold }
                Action { text: "×"; implicitWidth: 36; Accessible.name: qsTr("Закрити"); onClicked: panel.close() }
            }
            Copy { text: qsTr("Лише запити Forma з поточним ключем і сервером, від моменту оновлення. Суми орієнтовні, у USD."); color: "#66778c" }
            Copy { visible: (panel.stats.error || "").length > 0; text: panel.stats.error || ""; color: "#a13434"; objectName: "usageError" }
            Copy { visible: panel.stats.incomplete === true; text: qsTr("Частину запитів не вдалося записати. Статистика неповна."); color: "#96581b" }
            GridLayout {
                visible: !panel.stats.unavailable
                Layout.fillWidth: true; columns: 2; columnSpacing: 20; rowSpacing: 8
                Copy { text: qsTr("Сьогодні"); color: "#66778c" }
                Copy { text: qsTr("Останні 30 днів"); color: "#66778c" }
                Copy { objectName: "usageToday"; text: panel.stats.today ? panel.stats.today.costLabel : "—"; font.pixelSize: 25; font.weight: Font.DemiBold }
                Copy { text: panel.stats.month ? panel.stats.month.costLabel : "—"; font.pixelSize: 25; font.weight: Font.DemiBold }
                Copy { text: qsTr("Запитів: %1").arg(panel.stats.today ? panel.stats.today.requests : 0) }
                Copy { text: qsTr("Запитів: %1").arg(panel.stats.month ? panel.stats.month.requests : 0) }
            }
            Copy { text: qsTr("Токени за 30 днів · вхід / вихід: %1").arg(panel.stats.month ? panel.stats.month.tokensText : "—"); color: "#66778c" }
            Copy {
                visible: !!panel.stats.month && panel.stats.month.unknown > 0
                text: qsTr("Без оцінки вартості: %1 запитів. Їх не включено в суми вище: API не надав usage або тариф невідомий.").arg(panel.stats.month ? panel.stats.month.unknown : 0)
                color: "#96581b"
            }
            Copy {
                visible: !!panel.stats.month && panel.stats.month.uncertain > 0
                text: qsTr("Неповні дані для %1 запитів: не збережено деталізацію кешу. Сума орієнтовна; точний залишок за нею визначити неможливо.").arg(panel.stats.month ? panel.stats.month.uncertain : 0)
                color: "#96581b"
            }
            Action { id: showModels; objectName: "usageModelsToggle"; checkable: true; text: qsTr("Деталі за моделями"); visible: !!panel.stats.models && panel.stats.models.length > 0 }
            ColumnLayout {
                visible: showModels.checked; Layout.fillWidth: true
                Repeater {
                    model: panel.stats.models || []
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 4
                        Copy { text: modelData.model + " · " + modelData.costLabel; font.weight: Font.DemiBold }
                        Copy { text: qsTr("Запитів: %1 · токени вхід / вихід: %2").arg(modelData.requests).arg(modelData.tokensText); color: "#66778c"; font.pixelSize: 12 }
                        Copy { text: qsTr("Кеш · читання: %1 · запис: %2").arg(modelData.cached).arg(modelData.writeText); color: "#66778c"; font.pixelSize: 12 }
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#d5dfed" }
            Copy { text: qsTr("Баланс"); font.pixelSize: 16; font.weight: Font.DemiBold }
            Copy {
                visible: panel.stats.openai === true
                text: qsTr("OpenAI не надає залишок кредитів через звичайний API-ключ. Актуальний баланс доступний у білінгу.")
                color: "#66778c"
            }
            Action { visible: panel.stats.openai === true; text: qsTr("Відкрити баланс OpenAI ↗"); onClicked: panel.controller.openBilling() }
            Copy {
                visible: !!panel.stats.balance
                text: qsTr("Розрахунковий залишок: %1").arg(panel.stats.balance ? panel.stats.balance.text : "—")
                font.pixelSize: 18; font.weight: Font.DemiBold
            }
            Copy {
                visible: !!panel.stats.balance
                text: qsTr("Від суми, вказаної %1. Витрати інших застосунків, поповнення та строк дії кредитів не враховано.").arg(panel.stats.balance ? panel.stats.balance.date : "")
                color: "#66778c"
            }
            Copy {
                visible: !!panel.stats.balance && panel.stats.balance.unknown > 0
                text: qsTr("Залишок невідомий: після введення балансу є запити без повної оцінки вартості.")
                color: "#96581b"
            }
            Action { id: editBalance; checkable: true; text: qsTr("Вказати поточний баланс вручну"); enabled: !panel.stats.unavailable }
            ColumnLayout {
                visible: editBalance.checked; Layout.fillWidth: true; spacing: 8
                Copy { text: qsTr("Сума у USD. Порожнє поле прибирає розрахунковий баланс."); color: "#66778c" }
                RowLayout {
                    Layout.fillWidth: true
                    Field { id: balanceInput; objectName: "usageBalanceInput"; Accessible.name: qsTr("Поточний баланс у USD") }
                    Action { text: qsTr("Зберегти"); onClicked: if (panel.controller.saveBalance(balanceInput.text)) editBalance.checked = false }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#d5dfed" }
            Action { id: editTariff; checkable: true; text: qsTr("Власний тариф моделі"); enabled: panel.stats.configured === true }
            ColumnLayout {
                visible: editTariff.checked; Layout.fillWidth: true; spacing: 8
                Copy { text: panel.stats.model || ""; font.weight: Font.DemiBold }
                Copy { text: qsTr("USD за 1 млн токенів. Діє лише для нових запитів цієї моделі. Порожні поля відновлюють автоматичний тариф."); color: "#66778c" }
                GridLayout {
                    Layout.fillWidth: true; columns: 3; columnSpacing: 10
                    Copy { text: qsTr("Вхід") }
                    Copy { text: qsTr("Кешований вхід") }
                    Copy { text: qsTr("Вихід") }
                    Field { id: inputRate; objectName: "usageInputRate"; Accessible.name: qsTr("Вхід") }
                    Field { id: cacheRate; Accessible.name: qsTr("Кешований вхід") }
                    Field { id: outputRate; Accessible.name: qsTr("Вихід") }
                }
                Action { text: qsTr("Зберегти тариф"); onClicked: if (panel.controller.saveTariff(inputRate.text, cacheRate.text, outputRate.text)) editTariff.checked = false }
            }
            Copy {
                text: qsTr("Автотарифи OpenAI: %1, стандартна обробка. Враховано читання та запис кешу за даними API. Податки й регіональні доплати не включені. Точні списання перевіряйте у провайдера.").arg(panel.stats.priceDate || "")
                color: "#66778c"; font.pixelSize: 11
            }
        }
    }
}
