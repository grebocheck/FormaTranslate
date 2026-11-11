import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: control
    property bool primary: false
    property bool subtle: false
    property bool danger: false
    property bool leftAligned: false
    property string iconName: ""
    property string hint: ""
    readonly property color foreground: !enabled ? Theme.disabledInk : primary ? Theme.accentText : danger ? Theme.danger : checked ? Theme.accent : Theme.ink
    implicitHeight: Theme.controlHeight
    implicitWidth: Math.ceil(buttonRow.implicitWidth + leftPadding + rightPadding)
    Layout.minimumWidth: implicitWidth
    leftPadding: 14; rightPadding: 14; topPadding: 9; bottomPadding: 9
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: text || hint
    ToolTip.visible: hovered && hint.length > 0
    ToolTip.text: hint
    ToolTip.delay: 650
    font.family: Theme.fontFamily
    background: Rectangle {
        radius: Theme.radius
        color: !control.enabled ? (control.subtle ? "transparent" : Theme.disabled)
             : control.primary ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent)
             : control.down ? Theme.pressed : control.checked ? Theme.accentLight : control.hovered ? Theme.hover : control.subtle ? "transparent" : Theme.surfaceRaised
        border.color: control.primary || control.subtle || control.checked ? "transparent" : Theme.lineStrong
        Rectangle {
            anchors.fill: parent; anchors.margins: -3
            radius: Theme.radius + 3; color: "transparent"
            border.color: Theme.accent; border.width: 2
            visible: control.visualFocus
        }
        Behavior on color { ColorAnimation { duration: 90 } }
    }
    contentItem: Item {
        implicitWidth: buttonRow.implicitWidth
        implicitHeight: buttonRow.implicitHeight
        Row {
            id: buttonRow
            x: control.leftAligned ? 0 : (parent.width - width) / 2
            anchors.verticalCenter: parent.verticalCenter; spacing: 8
            Icon { name: control.iconName; color: control.foreground; visible: control.iconName.length > 0; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: control.text; visible: control.text.length > 0
                font.family: Theme.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                color: control.foreground; textFormat: Text.PlainText
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
