import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    property string heading: ""
    property var snapshot: ({})
    property string filePath: ""
    property int page: 1
    property real zoom: 1
    property bool fitPage: false
    readonly property bool ready: snapshot.status === "ready" && !!snapshot.imageUrl
    signal scrollFractionChanged(real fraction)
    spacing: 6

    function resetScroll() { viewport.contentX = 0; viewport.contentY = 0 }
    function setScrollFraction(fraction) {
        viewport.contentY = Math.max(0, viewport.contentHeight - viewport.height) * fraction
    }
    PageControls.Caption {
        text: pane.heading + (pane.snapshot.pageCount ? " · " + pane.page + " / " + pane.snapshot.pageCount : "")
        font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight
    }
    Rectangle {
        Layout.fillWidth: true; Layout.fillHeight: true
        color: Theme.surfaceMuted; radius: 6; border.color: Theme.line
        clip: true
        Flickable {
            id: viewport; objectName: pane.objectName + "Viewport"
            anchors.fill: parent; anchors.margins: 1
            clip: true; visible: pane.ready
            boundsBehavior: Flickable.StopAtBounds
            contentWidth: Math.max(width, paper.width + 24)
            contentHeight: Math.max(height, paper.height + 24)
            onContentYChanged: pane.scrollFractionChanged(Math.max(0, Math.min(1, contentY / Math.max(1, contentHeight - height))))
            ScrollBar.vertical: PageControls.ReadingScrollBar { }
            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
            Rectangle {
                id: paper
                x: Math.max(12, (viewport.width - width) / 2); y: 12
                width: Math.max(40, Math.min(viewport.width - 24, pane.fitPage && pane.snapshot.height > 0 ? (viewport.height - 24) * pane.snapshot.width / pane.snapshot.height : viewport.width)) * pane.zoom
                height: pane.snapshot.width > 0 ? width * pane.snapshot.height / pane.snapshot.width : width * 1.414
                color: "white"
                Image {
                    objectName: pane.objectName + "Image"
                    readonly property int captureStatus: status
                    anchors.fill: parent; source: pane.snapshot.imageUrl || ""
                    asynchronous: true; cache: false
                    fillMode: Image.PreserveAspectFit; smooth: true
                    Accessible.role: Accessible.Graphic
                    Accessible.name: pane.heading + ", " + qsTr("Сторінка %1").arg(pane.page)
                }
            }
        }
        ColumnLayout {
            anchors.centerIn: parent; width: Math.max(0, Math.min(420, parent.width - 32))
            spacing: 12; visible: !pane.ready
            BusyIndicator {
                running: pane.snapshot.status === "loading" || !pane.snapshot.status
                visible: running; Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 28; Layout.preferredHeight: 28
            }
            PageControls.Caption {
                Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: pane.snapshot.message || qsTr("Готуємо перегляд…")
            }
            PageControls.Action {
                text: qsTr("Відкрити файл"); iconName: "external"; subtle: true
                visible: !!pane.filePath && pane.snapshot.status !== "loading" && !!pane.snapshot.status
                Layout.alignment: Qt.AlignHCenter
                onClicked: backend.openPath(pane.filePath)
            }
        }
    }
}
