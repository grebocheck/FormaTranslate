import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: panel
    required property var controller
    readonly property var stats: controller.summary
    property var loadedRates: ["", "", ""]
    property int balanceEditRevision: 0
    property int tariffEditRevision: 0
    property int pendingBalanceRevision: -1
    property int pendingTariffRevision: -1
    property string balanceContext: ""
    property string tariffContext: ""
    property string pendingBalanceContext: ""
    property string pendingTariffContext: ""
    property var balanceDrafts: ({})
    property var tariffDrafts: ({})
    objectName: "usagePanel"
    width: Math.min(560, parent.width - 32)
    height: Math.min(contentColumn.implicitHeight + 40, parent.height - 32)
    padding: 20
    modal: true
    focus: true
    anchors.centerIn: parent
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.line }
    Overlay.modal: Rectangle { color: Theme.overlay }
    onOpened: {
        balanceEditRevision++
        tariffEditRevision++
        syncContexts()
        controller.refresh()
        if (!editTariff.checked) loadRates()
    }
    function remember(drafts, context, draft) {
        if (!context) return drafts
        const next = Object.assign({}, drafts)
        delete next[context]
        next[context] = draft
        const keys = Object.keys(next)
        if (keys.length > 8) delete next[keys[0]]
        return next
    }
    function syncContexts() {
        const account = controller.accountContext
        if (balanceContext !== account) {
            balanceDrafts = remember(balanceDrafts, balanceContext, {text: balanceInput.text, editing: editBalance.checked})
            balanceContext = account
            balanceEditRevision++
            pendingBalanceRevision = -1
            const draft = balanceDrafts[account]
            balanceInput.text = draft ? draft.text : ""
            editBalance.checked = draft ? draft.editing : false
        }
        const tariff = controller.tariffContext
        if (tariffContext !== tariff) {
            tariffDrafts = remember(tariffDrafts, tariffContext, {
                values: [inputRate.text, cacheRate.text, outputRate.text], loaded: loadedRates, editing: editTariff.checked
            })
            tariffContext = tariff
            tariffEditRevision++
            pendingTariffRevision = -1
            const draft = tariffDrafts[tariff]
            loadedRates = draft ? draft.loaded : ["", "", ""]
            inputRate.text = draft ? draft.values[0] : ""
            cacheRate.text = draft ? draft.values[1] : ""
            outputRate.text = draft ? draft.values[2] : ""
            editTariff.checked = draft ? draft.editing : false
        }
    }
    function saveBalanceDraft() {
        const context = balanceContext
        syncContexts()
        if (context !== balanceContext || !context) return
        if (controller.saveBalance(balanceInput.text)) {
            pendingBalanceRevision = balanceEditRevision
            pendingBalanceContext = balanceContext
        }
    }
    function saveTariffDraft() {
        const context = tariffContext
        syncContexts()
        if (context !== tariffContext || !context) return
        if (controller.saveTariff(inputRate.text, cacheRate.text, outputRate.text)) {
            pendingTariffRevision = tariffEditRevision
            pendingTariffContext = tariffContext
        }
    }
    function loadRates(force) {
        if (!force && (inputRate.text !== loadedRates[0] || cacheRate.text !== loadedRates[1] || outputRate.text !== loadedRates[2]))
            return
        const rates = stats.tariff || []
        loadedRates = [rates[0] || "", rates[1] || "", rates[2] || ""]
        inputRate.text = loadedRates[0]
        cacheRate.text = loadedRates[1]
        outputRate.text = loadedRates[2]
    }
    Connections {
        target: panel.controller
        function onSaved(kind) {
            if (kind === "balance") {
                if (panel.pendingBalanceContext === panel.controller.accountContext && panel.pendingBalanceRevision === panel.balanceEditRevision)
                    editBalance.checked = false
                panel.pendingBalanceRevision = -1
            }
            if (kind === "tariff") {
                if (panel.pendingTariffContext === panel.controller.tariffContext && panel.pendingTariffRevision === panel.tariffEditRevision) {
                    panel.loadedRates = [inputRate.text, cacheRate.text, outputRate.text]
                    editTariff.checked = false
                }
                panel.pendingTariffRevision = -1
            }
        }
        function onChanged() {
            if (panel.visible) {
                panel.syncContexts()
                if (!editTariff.checked) panel.loadRates()
            }
        }
    }
    component Copy: Text { font.family: Theme.fontFamily;
        color: Theme.ink; font.pixelSize: 13
        wrapMode: Text.WordWrap; textFormat: Text.PlainText
        Layout.fillWidth: true
    }
    component Action: AppButton { }
    component Field: AppField { }
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
                Action { text: ""; iconName: "close"; subtle: true; implicitWidth: 36; Accessible.name: qsTr("Закрити"); onClicked: panel.close() }
            }
            Copy { text: qsTr("Лише запити Forma з поточним ключем і сервером, від моменту оновлення. Суми орієнтовні, у USD."); color: Theme.muted }
            Copy { visible: (panel.stats.error || "").length > 0; text: panel.stats.error || ""; color: Theme.danger; objectName: "usageError" }
            Copy { visible: panel.stats.incomplete === true; text: qsTr("Частину запитів не вдалося записати. Статистика неповна."); color: Theme.warning }
            BusyIndicator { visible: panel.controller.working && !panel.stats.today; running: visible; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
            GridLayout {
                visible: !panel.stats.unavailable && !!panel.stats.today
                Layout.fillWidth: true; columns: 2; columnSpacing: 20; rowSpacing: 8
                Copy { text: qsTr("Сьогодні"); color: Theme.muted }
                Copy { text: qsTr("Останні 30 днів"); color: Theme.muted }
                Copy { objectName: "usageToday"; text: panel.stats.today ? panel.stats.today.costLabel : "—"; font.pixelSize: 25; font.weight: Font.DemiBold }
                Copy { text: panel.stats.month ? panel.stats.month.costLabel : "—"; font.pixelSize: 25; font.weight: Font.DemiBold }
                Copy { text: qsTr("Запитів: %1").arg(panel.stats.today ? panel.stats.today.requests : 0) }
                Copy { text: qsTr("Запитів: %1").arg(panel.stats.month ? panel.stats.month.requests : 0) }
            }
            Copy { text: qsTr("Токени за 30 днів · вхід / вихід: %1").arg(panel.stats.month ? panel.stats.month.tokensText : "—"); color: Theme.muted }
            Copy {
                visible: !!panel.stats.month && panel.stats.month.unknown > 0
                text: qsTr("Без оцінки вартості: %1 запитів. Їх не включено в суми вище: API не надав usage або тариф невідомий.").arg(panel.stats.month ? panel.stats.month.unknown : 0)
                color: Theme.warning
            }
            Copy {
                visible: !!panel.stats.month && panel.stats.month.uncertain > 0
                text: qsTr("Неповні дані для %1 запитів: не збережено деталізацію кешу. Сума орієнтовна; точний залишок за нею визначити неможливо.").arg(panel.stats.month ? panel.stats.month.uncertain : 0)
                color: Theme.warning
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
                        Copy { text: qsTr("Запитів: %1 · токени вхід / вихід: %2").arg(modelData.requests).arg(modelData.tokensText); color: Theme.muted; font.pixelSize: 12 }
                        Copy { text: qsTr("Кеш · читання: %1 · запис: %2").arg(modelData.cached).arg(modelData.writeText); color: Theme.muted; font.pixelSize: 12 }
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.line }
            Copy { text: qsTr("Баланс"); font.pixelSize: 16; font.weight: Font.DemiBold }
            Copy {
                visible: panel.stats.openai === true
                text: qsTr("OpenAI не надає залишок кредитів через звичайний API-ключ. Актуальний баланс доступний у білінгу.")
                color: Theme.muted
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
                color: Theme.muted
            }
            Copy {
                visible: !!panel.stats.balance && panel.stats.balance.unknown > 0
                text: qsTr("Залишок невідомий: після введення балансу є запити без повної оцінки вартості.")
                color: Theme.warning
            }
            Action { id: editBalance; objectName: "usageEditBalanceButton"; checkable: true; text: qsTr("Вказати поточний баланс вручну"); enabled: panel.controller.available && !panel.stats.unavailable && !panel.controller.working; onCheckedChanged: if (checked) panel.balanceEditRevision++ }
            ColumnLayout {
                visible: editBalance.checked; Layout.fillWidth: true; spacing: 8
                Copy { text: qsTr("Сума у USD. Порожнє поле прибирає розрахунковий баланс."); color: Theme.muted }
                RowLayout {
                    Layout.fillWidth: true
                    Field { id: balanceInput; objectName: "usageBalanceInput"; Accessible.name: qsTr("Поточний баланс у USD"); onTextEdited: panel.balanceEditRevision++ }
                    Action { text: qsTr("Зберегти"); objectName: "usageSaveBalanceButton"; enabled: panel.controller.available && !panel.controller.working; onClicked: panel.saveBalanceDraft() }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.line }
            Action { id: editTariff; objectName: "usageEditTariffButton"; checkable: true; text: qsTr("Власний тариф моделі"); enabled: panel.controller.available && panel.stats.configured === true && !panel.controller.working; onCheckedChanged: if (checked) panel.tariffEditRevision++ }
            ColumnLayout {
                visible: editTariff.checked; Layout.fillWidth: true; spacing: 8
                Copy { text: panel.stats.model || ""; font.weight: Font.DemiBold }
                Copy { text: qsTr("USD за 1 млн токенів. Діє лише для нових запитів цієї моделі. Порожні поля відновлюють автоматичний тариф."); color: Theme.muted }
                GridLayout {
                    Layout.fillWidth: true; columns: 3; columnSpacing: 10
                    Copy { text: qsTr("Вхід") }
                    Copy { text: qsTr("Кешований вхід") }
                    Copy { text: qsTr("Вихід") }
                    Field { id: inputRate; objectName: "usageInputRate"; Accessible.name: qsTr("Вхід"); onTextEdited: panel.tariffEditRevision++ }
                    Field { id: cacheRate; objectName: "usageCacheRate"; Accessible.name: qsTr("Кешований вхід"); onTextEdited: panel.tariffEditRevision++ }
                    Field { id: outputRate; objectName: "usageOutputRate"; Accessible.name: qsTr("Вихід"); onTextEdited: panel.tariffEditRevision++ }
                }
                Action { text: qsTr("Зберегти тариф"); objectName: "usageSaveTariffButton"; enabled: panel.controller.available && !panel.controller.working; onClicked: panel.saveTariffDraft() }
            }
            Copy {
                text: qsTr("Автотарифи OpenAI: %1, стандартна обробка. Враховано читання та запис кешу за даними API. Податки й регіональні доплати не включені. Точні списання перевіряйте у провайдера.").arg(panel.stats.priceDate || "")
                color: Theme.muted; font.pixelSize: 11
            }
        }
    }
}
