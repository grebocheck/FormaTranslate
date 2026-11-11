import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: control
    property var optionLabels: []
    displayText: currentIndex >= 0 && currentIndex < optionLabels.length ? optionLabels[currentIndex] : currentText
    implicitHeight: Theme.controlHeight
    font.family: Theme.fontFamily; font.pixelSize: 14
    leftPadding: 12; rightPadding: 34
    background: Rectangle {
        radius: Theme.radius
        color: control.enabled ? Theme.surface : Theme.disabled
        border.color: control.visualFocus || control.popup.visible ? Theme.accent : Theme.lineStrong
        border.width: control.visualFocus || control.popup.visible ? 2 : 1
    }
    contentItem: Text {
        text: control.displayText; color: control.enabled ? Theme.ink : Theme.disabledInk
        font.family: Theme.fontFamily; font.pixelSize: 14
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        textFormat: Text.PlainText
    }
    indicator: Icon {
        name: "chevron-down"; color: control.enabled ? Theme.muted : Theme.disabledInk
        x: control.width - width - 12; y: (control.height - height) / 2
        width: 14; height: 14
    }
    delegate: ItemDelegate {
        id: option
        required property var modelData
        required property int index
        width: control.width - 8; height: 38
        leftPadding: 10; rightPadding: 10
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: option.index < control.optionLabels.length ? control.optionLabels[option.index] : option.modelData
            color: Theme.ink; font.family: Theme.fontFamily; font.pixelSize: 13
            font.weight: control.currentIndex === option.index ? Font.Medium : Font.Normal
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; textFormat: Text.PlainText
        }
        background: Rectangle { radius: 4; color: option.highlighted || option.hovered ? Theme.hover : "transparent" }
    }
    popup: Popup {
        y: control.height + 5; width: control.width; padding: 4
        topMargin: 8; bottomMargin: 8
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        background: Rectangle { color: Theme.surfaceRaised; radius: Theme.radius; border.color: Theme.lineStrong }
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollBar.vertical: ScrollBar { }
        }
    }
}
