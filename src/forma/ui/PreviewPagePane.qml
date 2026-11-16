import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: pane
    property string heading: ""
    property var snapshot: ({})
    property string filePath: ""
    property int page: 1
    property real paperWidth: width
    property bool inViewport: true
    readonly property bool ready: snapshot.status === "ready" && !!snapshot.imageUrl
    readonly property bool hasPage: snapshot.width > 0 && snapshot.height > 0
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        width: pane.paperWidth
        height: pane.hasPage ? width * pane.snapshot.height / pane.snapshot.width : parent.height
        color: pane.hasPage ? "white" : "transparent"
        border.color: pane.hasPage ? Theme.line : "transparent"
        Image {
            objectName: pane.objectName + "Image"
            readonly property int captureStatus: status
            readonly property bool captureRelevant: pane.inViewport && pane.hasPage && !["error", "unavailable"].includes(pane.snapshot.status)
            anchors.fill: parent; source: pane.snapshot.imageUrl || ""
            asynchronous: true; cache: false; fillMode: Image.PreserveAspectFit; smooth: true
            Accessible.role: Accessible.Graphic
            Accessible.name: pane.heading + ", " + qsTr("Сторінка %1").arg(pane.page)
        }
        ColumnLayout {
            anchors.centerIn: parent; width: Math.max(0, Math.min(360, parent.width - 32))
            spacing: 8; visible: !pane.ready
            BusyIndicator {
                running: pane.inViewport && pane.hasPage && ["idle", "loading"].includes(pane.snapshot.status)
                visible: running; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 24; Layout.preferredHeight: 24
            }
            PageControls.Caption { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; text: pane.snapshot.message || qsTr("Готуємо перегляд…") }
            PageControls.Action {
                text: qsTr("Відкрити файл"); iconName: "external"; subtle: true
                visible: !!pane.filePath && pane.snapshot.status === "error"; Layout.alignment: Qt.AlignHCenter
                onClicked: backend.openPath(pane.filePath)
            }
        }
    }
}
