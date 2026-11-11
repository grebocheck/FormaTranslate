import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: control
    implicitHeight: Theme.controlHeight
    font.family: Theme.fontFamily; font.pixelSize: 14
    leftPadding: 12; rightPadding: 12
    color: enabled ? Theme.ink : Theme.disabledInk
    placeholderTextColor: Theme.muted
    selectionColor: Theme.accent
    selectedTextColor: Theme.accentText
    selectByMouse: true
    background: Rectangle {
        radius: Theme.radius
        color: control.enabled ? Theme.surface : Theme.disabled
        border.color: control.activeFocus ? Theme.accent : Theme.lineStrong
        border.width: control.activeFocus ? 2 : 1
    }
}
