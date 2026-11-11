import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    component LabelText: Text { font.family: Theme.fontFamily;
        color: Theme.ink
        font.pixelSize: 14
        textFormat: Text.PlainText
        renderType: Text.NativeRendering
    }
    component Caption: LabelText { color: Theme.muted; font.pixelSize: 12 }
    component Heading: LabelText { font.pixelSize: 16; font.weight: Font.Medium }
    component Panel: Rectangle { color: Theme.surface; border.color: Theme.line; radius: 12 }
    component Divider: Rectangle { color: Theme.line; implicitHeight: 1; Layout.fillWidth: true }
    component ReadingScrollBar: ScrollBar {
        id: control
        policy: ScrollBar.AsNeeded; hoverEnabled: true; minimumSize: 0.08
        orientation: Qt.Vertical; visible: size > 0 && size < 1
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 8; implicitWidth: 8; padding: 1
        contentItem: Rectangle { implicitWidth: 4; implicitHeight: 28; radius: 2; color: control.pressed ? Theme.lineStrong : Theme.scrollbar; opacity: control.active || control.hovered ? 1 : 0.4 }
        background: null
    }
    component ReadOnlyText: TextArea {
        readOnly: true; selectByMouse: true; persistentSelection: true
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        color: Theme.ink; selectionColor: Theme.accent; selectedTextColor: Theme.accentText
        placeholderTextColor: Theme.muted; font.pixelSize: 14; padding: 0; rightPadding: 12
        background: null
    }
    component ReviewTextPanel: Panel {
        id: textPanel
        property string heading: ""
        property alias text: textContent.text
        property alias textObjectName: textContent.objectName
        property bool editing: false
        property bool edited: false
        property string editError: ""
        signal draftEdited(string value)
        onTextChanged: if (!editing && textScroll.contentItem) textScroll.contentItem.contentY = 0
        onEditingChanged: if (editing) Qt.callLater(function() { textContent.forceActiveFocus() })
        border.color: editing ? Theme.accent : Theme.line
        border.width: editing ? 2 : 1
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Caption { text: textPanel.heading; font.weight: Font.DemiBold; Layout.fillWidth: true }
                Caption { text: textPanel.editing ? qsTr("Редагування") : qsTr("Чернетка"); visible: textPanel.editing || textPanel.edited; color: Theme.accent; font.weight: Font.DemiBold }
            }
            Divider { }
            ScrollView {
                id: editErrorScroll
                visible: textPanel.editError.length > 0
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(64, editErrorText.implicitHeight)
                Layout.maximumHeight: 64; clip: true; contentWidth: availableWidth
                ScrollBar.vertical: ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ReadOnlyText { id: editErrorText; objectName: textPanel.textObjectName + "Error"; width: editErrorScroll.availableWidth; text: textPanel.editError; color: Theme.danger; font.pixelSize: 12; Accessible.name: qsTr("Помилка збереження правки") }
            }
            ScrollView {
                id: textScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                ScrollBar.vertical: ReadingScrollBar { }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ReadOnlyText {
                    id: textContent
                    width: textScroll.availableWidth
                    readOnly: !textPanel.editing
                    onTextChanged: if (textPanel.editing) textPanel.draftEdited(text)
                    placeholderText: textPanel.editing ? qsTr("Введіть переклад") : qsTr("Текст відсутній")
                    Accessible.name: textPanel.heading
                }
            }
        }
    }
    component Action: AppButton { }
    component NavigationTab: TabButton {
        id: control
        property string iconName: "document"
        implicitHeight: 46
        implicitWidth: tabContent.implicitWidth + 36
        width: implicitWidth
        contentItem: Item {
            implicitWidth: tabContent.implicitWidth
            implicitHeight: tabContent.implicitHeight
            Row {
                id: tabContent
                anchors.centerIn: parent; spacing: 9
                Icon { name: control.iconName; color: !control.enabled ? Theme.muted : control.checked ? Theme.accent : Theme.muted; width: 20; height: 20; anchors.verticalCenter: parent.verticalCenter }
                LabelText { text: control.text; font.pixelSize: 14; font.weight: Font.Medium; color: !control.enabled ? Theme.muted : control.checked ? Theme.accent : Theme.muted; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        background: Rectangle {
            color: control.hovered ? Theme.hover : "transparent"
            radius: 6
            border.color: control.visualFocus ? Theme.accent : "transparent"
            border.width: 2
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.leftMargin: 12; anchors.rightMargin: 12; height: 2; radius: 1; color: Theme.accent; visible: control.checked }
        }
    }
    component Field: AppField { }
    component Select: AppSelect { }
    component ModelPicker: Item {
        id: control
        property alias text: modelInput.text
        property alias placeholderText: modelInput.placeholderText
        implicitHeight: 42
        implicitWidth: 240
        Field {
            id: modelInput
            objectName: control.objectName + "Input"
            anchors.fill: parent; rightPadding: 42
            Accessible.name: control.Accessible.name
            Keys.onDownPressed: function(event) {
                if (backend.modelChoices.length > 0) {
                    modelPopup.open()
                    modelList.currentIndex = 0
                    modelList.forceActiveFocus()
                }
                event.accepted = true
            }
        }
        Action {
            objectName: control.objectName + "DropdownButton"
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: 4
            text: ""; iconName: "chevron-down"; hint: qsTr("Вибрати зі списку моделей"); subtle: true
            implicitWidth: 34; implicitHeight: 34; enabled: backend.modelChoices.length > 0
            Accessible.name: hint
            onClicked: modelPopup.visible ? modelPopup.close() : modelPopup.open()
        }
        Popup {
            id: modelPopup
            objectName: control.objectName + "Popup"
            y: control.height + 4; width: control.width; padding: 4
            topMargin: 8; bottomMargin: 8
            implicitHeight: Math.min(modelList.contentHeight + 8, 300)
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
            background: Rectangle { color: Theme.surface; radius: 7; border.color: Theme.lineStrong }
            onOpened: modelList.currentIndex = backend.modelChoices.indexOf(control.text)
            contentItem: ListView {
                id: modelList
                clip: true; model: backend.modelChoices; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }
                Keys.onReturnPressed: chooseCurrent()
                Keys.onEnterPressed: chooseCurrent()
                Keys.onEscapePressed: modelPopup.close()
                function chooseCurrent() {
                    if (currentIndex >= 0 && currentIndex < count) {
                        control.text = backend.modelChoices[currentIndex]
                        modelPopup.close()
                        modelInput.forceActiveFocus()
                    }
                }
                delegate: ItemDelegate {
                    id: modelOption
                    required property string modelData
                    required property int index
                    width: modelList.width; height: 38
                    highlighted: modelList.currentIndex === index
                    contentItem: LabelText { text: modelOption.modelData; font.pixelSize: 13; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; font.weight: control.text === modelOption.modelData ? Font.DemiBold : Font.Normal }
                    background: Rectangle { radius: 4; color: modelOption.highlighted || modelOption.hovered ? Theme.accentLight : Theme.surface }
                    onClicked: { modelList.currentIndex = index; modelList.chooseCurrent() }
                }
            }
        }
    }
    component Toggle: CheckBox {
        id: control
        implicitHeight: Math.max(30, contentItem.implicitHeight + topPadding + bottomPadding)
        Layout.fillWidth: true
        spacing: 10
        indicator: Rectangle {
            width: 18; height: 18
            x: control.leftPadding; y: (control.height - height) / 2
            color: control.checked ? Theme.accent : Theme.surface
            radius: 4
            border.color: control.visualFocus ? Theme.accent : control.checked ? Theme.accent : Theme.lineStrong
            border.width: control.visualFocus ? 2 : 1
            Icon { anchors.centerIn: parent; name: "check"; width: 14; height: 14; color: Theme.accentText; visible: control.checked }
        }
        contentItem: LabelText {
            text: control.text
            wrapMode: Text.WordWrap
            font.pixelSize: 13
            color: control.enabled ? Theme.ink : Theme.muted
            leftPadding: control.indicator.width + control.spacing
            verticalAlignment: Text.AlignVCenter
        }
    }

}
