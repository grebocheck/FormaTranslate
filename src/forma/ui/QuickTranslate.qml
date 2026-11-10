import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    required property var controller
    required property var sourceCodes
    required property var targetCodes
    required property var languageLabels
    property string modelName: ""
    readonly property bool imageMode: controller.mode === "image"
    property bool showOriginal: false
    property real imageZoom: 1
    readonly property color ink: "#182230"
    readonly property color muted: "#596779"
    readonly property color accent: "#2859c5"
    readonly property color line: "#dce2ea"
    signal configureRequested()

    function zoomImage(factor) {
        var previous = imageZoom
        var centerX = imageViewport.contentX + imageViewport.width / 2
        var centerY = imageViewport.contentY + imageViewport.height / 2
        imageZoom = Math.max(1, Math.min(6, imageZoom * factor))
        var ratio = imageZoom / previous
        imageViewport.contentX = Math.max(0, Math.min(imageViewport.contentWidth - imageViewport.width, centerX * ratio - imageViewport.width / 2))
        imageViewport.contentY = Math.max(0, Math.min(imageViewport.contentHeight - imageViewport.height, centerY * ratio - imageViewport.height / 2))
    }

    Shortcut {
        sequences: [StandardKey.Open]
        enabled: page.visible && page.imageMode && !page.controller.busy
        onActivated: page.controller.pickImage()
    }
    Shortcut {
        sequences: [StandardKey.Paste]
        enabled: page.visible && page.imageMode && !page.controller.busy
                 && !recognizedText.activeFocus && !imageTranslatedText.activeFocus
        onActivated: page.controller.pasteImage()
    }
    Connections {
        target: page.controller
        function onChanged() {
            // Do not rewrite an active editor on progress or language notifications.
            if (sourceEditor.text !== page.controller.sourceText)
                sourceEditor.text = page.controller.sourceText
        }
        function onDraftChanged() {
            if (sourceEditor.text !== page.controller.sourceText)
                sourceEditor.text = page.controller.sourceText
        }
    }
    Component.onCompleted: sourceEditor.text = controller.sourceText

    component Label: Text {
        color: page.ink; font.pixelSize: 14
        textFormat: Text.PlainText; renderType: Text.NativeRendering
    }
    component Caption: Label { color: page.muted; font.pixelSize: 12 }
    component Card: Rectangle { color: "#ffffff"; border.color: page.line; radius: 10 }
    component Button: AbstractButton {
        id: button
        property string iconName: ""
        property string hint: ""
        property bool primary: false
        property bool subtle: false
        readonly property color foreground: !enabled ? "#5b6777" : primary ? "#ffffff" : checked ? page.accent : page.ink
        implicitWidth: Math.ceil(buttonRow.implicitWidth + 24)
        implicitHeight: 40
        Layout.minimumWidth: implicitWidth
        hoverEnabled: true
        activeFocusOnTab: true
        Accessible.name: text || hint
        ToolTip.visible: hovered && hint.length > 0
        ToolTip.text: hint
        ToolTip.delay: 600
        background: Rectangle {
            radius: 7
            color: !button.enabled ? "#edf0f4" : button.primary ? (button.down ? "#173e94" : button.hovered ? "#204eaf" : page.accent) : button.checked ? "#eaf1ff" : button.hovered ? "#edf2fa" : button.subtle ? "transparent" : "#ffffff"
            border.color: button.visualFocus ? page.accent : !button.enabled ? "#d9dfe7" : button.primary || button.subtle || button.checked ? "transparent" : "#bdc9d9"
            border.width: button.visualFocus ? 2 : 1
        }
        contentItem: Item {
            Row {
                id: buttonRow
                anchors.centerIn: parent; spacing: 8
                Icon { name: button.iconName; color: button.foreground; visible: button.iconName.length > 0; anchors.verticalCenter: parent.verticalCenter }
                Label { text: button.text; visible: text.length > 0; color: button.foreground; font.pixelSize: 13; font.weight: Font.DemiBold; anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }
    component LanguageSelect: ComboBox {
        id: select
        property var labels: []
        implicitHeight: 40
        leftPadding: 12; rightPadding: 32
        font.pixelSize: 14
        displayText: currentIndex >= 0 && currentIndex < labels.length ? labels[currentIndex] : ""
        background: Rectangle {
            radius: 7; color: select.enabled ? "#ffffff" : "#f0f2f6"
            border.color: select.visualFocus || select.popup.visible ? page.accent : "#c7d0dd"
            border.width: select.visualFocus ? 2 : 1
        }
        contentItem: Label { text: select.displayText; color: select.enabled ? page.ink : page.muted; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        indicator: Icon { name: "chevron-down"; color: page.muted; x: select.width - width - 10; y: (select.height - height) / 2; width: 14; height: 14 }
        delegate: ItemDelegate {
            id: option
            required property int index
            required property var modelData
            width: select.width - 8; height: 38
            highlighted: select.highlightedIndex === index
            contentItem: Label { text: select.labels[option.index]; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
            background: Rectangle { radius: 4; color: option.highlighted ? "#e8f0ff" : "#ffffff" }
        }
        popup: Popup {
            y: select.height + 4; width: select.width; padding: 4
            topMargin: 8; bottomMargin: 8
            implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
            background: Rectangle { color: "#ffffff"; radius: 7; border.color: "#b8c4d4" }
            contentItem: ListView {
                clip: true; implicitHeight: contentHeight
                model: select.popup.visible ? select.delegateModel : null
                currentIndex: select.highlightedIndex
                ScrollBar.vertical: ScrollBar { }
            }
        }
    }
    component ReadingText: TextArea {
        readOnly: true; selectByMouse: true; persistentSelection: true
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        color: page.ink; selectionColor: page.accent; selectedTextColor: "#ffffff"
        placeholderTextColor: "#69778a"; font.pixelSize: 15
        padding: 0; rightPadding: 12; background: null
    }
    component TextScroll: ScrollView {
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: ScrollBar {
            active: true; policy: ScrollBar.AsNeeded; minimumSize: 0.08
            anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
            width: 7; padding: 1
            contentItem: Rectangle { implicitWidth: 5; implicitHeight: 28; radius: 3; color: "#a8b5c7" }
            background: null
        }
    }

    ColumnLayout {
        anchors.fill: parent; spacing: 12
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Button { objectName: "quickTextMode"; text: qsTr("Текст"); iconName: "text"; checked: !page.imageMode; subtle: true; enabled: !page.controller.busy; onClicked: page.controller.setMode("text") }
            Button { objectName: "quickImageMode"; text: qsTr("Фото"); iconName: "image"; checked: page.imageMode; subtle: true; enabled: !page.controller.busy; onClicked: page.controller.setMode("image") }
            Item { Layout.fillWidth: true }
            Caption { text: page.modelName; elide: Text.ElideMiddle; Layout.maximumWidth: 240; visible: page.modelName.length > 0 }
            Button {
                text: page.modelName ? "" : qsTr("Налаштувати підключення"); iconName: "settings"
                hint: qsTr("Модель і підключення"); subtle: true
                objectName: "quickSettingsButton"; enabled: !page.controller.busy
                onClicked: page.configureRequested()
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 6
                Caption { text: qsTr("Мова оригіналу") }
                LanguageSelect {
                    id: imageSourceLanguage; objectName: "quickSourceLanguage"
                    Layout.fillWidth: true; model: page.sourceCodes
                    labels: [qsTr("Автовизначення")].concat(page.languageLabels)
                    currentIndex: Math.max(0, page.sourceCodes.indexOf(page.controller.sourceLanguage))
                    enabled: !page.controller.busy
                    Accessible.name: qsTr("Мова оригіналу")
                    onActivated: page.controller.setSourceLanguage(page.sourceCodes[currentIndex])
                }
            }
            Button {
                objectName: "quickSwapLanguages"; iconName: "swap"; hint: qsTr("Поміняти мови місцями")
                Layout.alignment: Qt.AlignBottom; subtle: true
                enabled: !page.controller.busy && page.controller.sourceLanguage !== "auto"
                onClicked: page.controller.swapLanguages()
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 6
                Caption { text: qsTr("Перекласти на") }
                LanguageSelect {
                    id: imageTargetLanguage; objectName: "quickTargetLanguage"
                    Layout.fillWidth: true; model: page.targetCodes; labels: page.languageLabels
                    currentIndex: Math.max(0, page.targetCodes.indexOf(page.controller.targetLanguage))
                    enabled: !page.controller.busy
                    Accessible.name: qsTr("Перекласти на")
                    onActivated: page.controller.setTargetLanguage(page.targetCodes[currentIndex])
                }
            }
        }
        StackLayout {
            currentIndex: page.imageMode ? 1 : 0
            Layout.fillWidth: true; Layout.fillHeight: true
            RowLayout {
                spacing: 12
                Card {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                    border.color: sourceEditor.activeFocus ? page.accent : page.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 16; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: qsTr("Оригінал"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Button { iconName: "paste"; hint: qsTr("Вставити текст"); objectName: "quickPasteText"; subtle: true; implicitHeight: 30; enabled: !page.controller.busy; onClicked: page.controller.pasteText() }
                            Button { iconName: "close"; hint: qsTr("Очистити текст"); objectName: "quickClearText"; subtle: true; implicitHeight: 30; enabled: !page.controller.busy && sourceEditor.text.length > 0; onClicked: page.controller.setSourceText("") }
                        }
                        TextScroll {
                            id: sourceScroll
                            Layout.fillWidth: true; Layout.fillHeight: true
                            ReadingText {
                                id: sourceEditor; objectName: "quickSourceText"
                                width: sourceScroll.availableWidth
                                readOnly: page.controller.busy
                                placeholderText: qsTr("Введіть або вставте текст для перекладу")
                                Accessible.name: qsTr("Текст оригіналу")
                                onTextChanged: if (text !== page.controller.sourceText) page.controller.setSourceText(text)
                            }
                        }
                        Caption {
                            Layout.alignment: Qt.AlignRight
                            text: qsTr("%1 / %2 символів").arg(page.controller.sourceCount).arg(page.controller.maximumTextLength)
                            color: page.controller.sourceCount > page.controller.maximumTextLength ? "#a13434" : page.muted
                        }
                    }
                }
                Card {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 16; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: qsTr("Переклад"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Button { text: qsTr("Копіювати"); iconName: "copy"; objectName: "quickCopyText"; subtle: true; implicitHeight: 30; enabled: page.controller.translatedText.length > 0; onClicked: page.controller.copyText() }
                        }
                        TextScroll {
                            id: resultScroll
                            Layout.fillWidth: true; Layout.fillHeight: true
                            ReadingText { objectName: "quickResultText"; width: resultScroll.availableWidth; text: page.controller.translatedText; placeholderText: qsTr("Тут з’явиться переклад"); Accessible.name: qsTr("Переклад тексту") }
                        }
                    }
                }
            }
            ColumnLayout {
                spacing: 10
                Card {
                    id: imageCard
                    Layout.fillWidth: true; Layout.fillHeight: true
                    border.color: imageDrop.containsDrag ? page.accent : page.line
                    DropArea {
                        id: imageDrop; objectName: "quickImageDrop"
                        anchors.fill: parent; enabled: page.visible && page.imageMode && !page.controller.busy
                        onEntered: function(event) { event.accepted = event.hasUrls && event.urls.length === 1 }
                        onDropped: function(event) {
                            if (event.hasUrls && event.urls.length === 1) {
                                page.controller.loadImage(event.urls[0])
                                event.acceptProposedAction()
                            }
                        }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; width: Math.min(520, parent.width - 48)
                        spacing: 12; visible: !page.controller.hasImage
                        Icon { name: "image"; color: "#7b8da7"; Layout.preferredWidth: 42; Layout.preferredHeight: 42; Layout.alignment: Qt.AlignHCenter }
                        Label { text: qsTr("Перекладіть текст на фото"); font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
                        Caption { text: qsTr("Перетягніть зображення сюди або вставте його з буфера обміну."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter; spacing: 10
                            Button { text: qsTr("Вибрати фото"); iconName: "folder"; objectName: "quickPickImage"; enabled: !page.controller.busy; onClicked: page.controller.pickImage() }
                            Button { text: qsTr("Вставити фото"); iconName: "paste"; objectName: "quickPasteImage"; enabled: !page.controller.busy; onClicked: page.controller.pasteImage() }
                        }
                        Caption { text: qsTr("Для фото потрібна модель із підтримкою зображень."); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                    }
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 8
                        visible: page.controller.hasImage
                        RowLayout {
                            Layout.fillWidth: true; spacing: 4
                            Button { text: qsTr("Оригінал"); objectName: "quickShowOriginal"; checked: page.showOriginal || !page.controller.hasImageResult; subtle: true; implicitHeight: 32; onClicked: page.showOriginal = true }
                            Button { text: qsTr("Переклад"); objectName: "quickShowTranslated"; checked: !page.showOriginal && page.controller.hasImageResult; enabled: page.controller.hasImageResult; subtle: true; implicitHeight: 32; onClicked: page.showOriginal = false }
                            Caption { text: page.controller.imageName; Layout.fillWidth: true; elide: Text.ElideMiddle; Layout.leftMargin: 8 }
                            Button { iconName: "zoom-in"; hint: qsTr("Збільшити зображення"); objectName: "quickZoomImage"; subtle: true; implicitHeight: 32; enabled: page.imageZoom < 6; onClicked: page.zoomImage(1.5) }
                            Button { iconName: "fit"; hint: qsTr("Показати все зображення"); objectName: "quickFitImage"; subtle: true; implicitHeight: 32; enabled: page.imageZoom > 1; onClicked: page.zoomImage(1 / page.imageZoom) }
                            Button { iconName: "folder"; hint: qsTr("Вибрати інше фото"); subtle: true; implicitHeight: 32; enabled: !page.controller.busy; onClicked: page.controller.pickImage() }
                            Button { iconName: "paste"; hint: qsTr("Вставити фото"); subtle: true; implicitHeight: 32; enabled: !page.controller.busy; onClicked: page.controller.pasteImage() }
                            Button { iconName: "close"; hint: qsTr("Прибрати фото"); objectName: "quickClearImage"; subtle: true; implicitHeight: 32; enabled: !page.controller.busy; onClicked: page.controller.clearImage() }
                        }
                        RowLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                            Rectangle {
                                color: "#f4f6fa"; radius: 6
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 3
                                Flickable {
                                    id: imageViewport; objectName: "quickImageViewport"
                                    anchors.fill: parent; anchors.margins: 8; clip: true
                                    contentWidth: Math.max(width, imagePreview.width)
                                    contentHeight: Math.max(height, imagePreview.height)
                                    boundsBehavior: Flickable.StopAtBounds
                                    interactive: page.imageZoom > 1
                                    Image {
                                        id: imagePreview; objectName: "quickImagePreview"
                                        readonly property real aspectRatio: sourceSize.height > 0 ? sourceSize.width / sourceSize.height : 1
                                        width: Math.min(imageViewport.width, imageViewport.height * aspectRatio) * page.imageZoom
                                        height: width / aspectRatio
                                        x: (imageViewport.contentWidth - width) / 2
                                        y: (imageViewport.contentHeight - height) / 2
                                        source: !page.showOriginal && page.controller.hasImageResult ? page.controller.translatedImageUrl : page.controller.originalImageUrl
                                        onSourceChanged: page.imageZoom = 1
                                        fillMode: Image.PreserveAspectFit; smooth: true; cache: false
                                        Accessible.role: Accessible.Graphic
                                        Accessible.name: !page.showOriginal && page.controller.hasImageResult ? qsTr("Фото з перекладом") : qsTr("Оригінальне фото")
                                    }
                                    MouseArea {
                                        width: imageViewport.contentWidth; height: imageViewport.contentHeight
                                        cursorShape: page.imageZoom > 1 ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor) : Qt.ArrowCursor
                                        onDoubleClicked: page.zoomImage(page.imageZoom > 1 ? 1 / page.imageZoom : 2)
                                        onWheel: function(wheel) {
                                            var delta = wheel.angleDelta.y || wheel.pixelDelta.y
                                            if (delta) page.zoomImage(delta > 0 ? 1.2 : 1 / 1.2)
                                            wheel.accepted = delta !== 0
                                        }
                                    }
                                    ScrollBar.vertical: ScrollBar { }
                                    ScrollBar.horizontal: ScrollBar { }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 2; spacing: 8
                                visible: page.controller.imageSourceText.length > 0 || page.controller.imageTranslatedText.length > 0
                                Caption { text: qsTr("Розпізнаний текст"); font.weight: Font.DemiBold }
                                TextScroll {
                                    id: recognizedScroll
                                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredHeight: 1
                                    ReadingText { id: recognizedText; objectName: "quickRecognizedText"; width: recognizedScroll.availableWidth; text: page.controller.imageSourceText; font.pixelSize: 13; Accessible.name: qsTr("Розпізнаний текст") }
                                }
                                Rectangle { color: page.line; Layout.fillWidth: true; implicitHeight: 1 }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Caption { text: qsTr("Переклад"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                    Button { iconName: "copy"; hint: qsTr("Копіювати переклад тексту"); objectName: "quickCopyImageText"; subtle: true; implicitHeight: 28; enabled: page.controller.imageTranslatedText.length > 0; onClicked: page.controller.copyText() }
                                }
                                TextScroll {
                                    id: imageTextScroll
                                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredHeight: 1
                                    ReadingText { id: imageTranslatedText; objectName: "quickImageTranslatedText"; width: imageTextScroll.availableWidth; text: page.controller.imageTranslatedText; font.pixelSize: 13; Accessible.name: qsTr("Переклад тексту з фото") }
                                }
                            }
                        }
                    }
                }
                Caption {
                    visible: page.controller.hasImage
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: page.controller.hasImageResult ? qsTr("Перевірте розпізнаний текст і розміщення перекладу.") : qsTr("Переклад з’явиться на зображенні. Оригінальне фото залишиться без змін.")
                }
            }
        }
        Rectangle {
            visible: page.controller.error.length > 0 || page.controller.warnings.length > 0
            Layout.fillWidth: true
            implicitHeight: Math.min(70, feedbackText.implicitHeight + 20)
            radius: 7; color: page.controller.error.length ? "#fff0ef" : "#fff5e8"
            TextScroll {
                id: feedbackScroll
                anchors.fill: parent; anchors.margins: 10
                ReadingText {
                    id: feedbackText; objectName: "quickFeedback"
                    width: feedbackScroll.availableWidth; font.pixelSize: 12
                    text: page.controller.error || page.controller.warnings.join("\n")
                    color: page.controller.error.length ? "#962d29" : "#8b4b1d"
                    Accessible.name: qsTr("Повідомлення перекладача")
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Button {
                objectName: "quickTranslateButton"
                text: page.controller.busy ? qsTr("Скасувати") : qsTr("Перекласти")
                iconName: page.controller.busy ? "stop" : "translate"
                primary: !page.controller.busy
                enabled: page.controller.busy || page.controller.canTranslate
                onClicked: {
                    if (page.controller.busy) page.controller.cancel()
                    else { page.showOriginal = false; page.controller.translate() }
                }
            }
            BusyIndicator { running: page.controller.busy; visible: running; Layout.preferredWidth: 24; Layout.preferredHeight: 24; palette.dark: page.accent }
            Caption { text: page.controller.status; color: page.controller.busy ? page.accent : page.muted; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
            Button { text: qsTr("Копіювати фото"); iconName: "copy"; objectName: "quickCopyImage"; visible: page.imageMode && page.controller.hasImageResult; enabled: !page.controller.busy; onClicked: page.controller.copyImage() }
            Button { text: qsTr("Зберегти PNG"); iconName: "save"; objectName: "quickSaveImage"; visible: page.imageMode && page.controller.hasImageResult; enabled: !page.controller.busy; onClicked: page.controller.saveImage() }
        }
    }
}
