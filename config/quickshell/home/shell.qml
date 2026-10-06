import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland

ShellRoot {
    id: root
    // Copy before sorting: the desktop-entry model belongs to Quickshell.
    readonly property var applications: DesktopEntries.applications.values.slice().sort(
        (a, b) => a.name.localeCompare(b.name))

    Variants {
        model: Quickshell.screens

        PanelWindow {
            id: desktop
            required property var modelData
            screen: modelData
            anchors { top: true; bottom: true; left: true; right: true }
            color: "transparent"
            exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Bottom
            WlrLayershell.namespace: "kwak-home"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

            Rectangle {
                anchors.centerIn: parent
                width: Math.min(672, parent.width - 32)
                height: Math.min(544, parent.height - 32)
                color: "#e6000000"
                border.color: "#252925"

                Column {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Text {
                        text: "Applications"
                        color: "#b8c4b8"
                        font.pixelSize: 16
                        font.family: "DejaVu Sans"
                    }

                    GridView {
                        id: grid
                        width: parent.width
                        height: parent.height - 32
                        // Three rows, ordered top-to-bottom, overflowing sideways.
                        cellWidth: 160
                        cellHeight: height / 3
                        flow: GridView.FlowTopToBottom
                        flickableDirection: Flickable.HorizontalFlick
                        boundsBehavior: Flickable.StopAtBounds
                        clip: true
                        model: root.applications
                        ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }

                        WheelHandler {
                            // Preserve horizontal touchpad scrolling; translate mouse wheels.
                            onWheel: event => {
                                const delta = event.pixelDelta.x || event.pixelDelta.y
                                    || event.angleDelta.x / 120 * 80
                                    || event.angleDelta.y / 120 * 80;
                                grid.contentX = Math.max(0, Math.min(
                                    Math.max(0, grid.contentWidth - grid.width), grid.contentX - delta));
                                event.accepted = true;
                            }
                        }

                        delegate: Rectangle {
                            id: tile
                            required property var modelData
                            width: grid.cellWidth
                            height: grid.cellHeight
                            color: "transparent"

                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 8
                                color: pointer.pressed ? "#252925" : "transparent"
                                border.color: pointer.containsMouse ? "#b8c4b8" : "transparent"
                            }

                            Column {
                                anchors.centerIn: parent
                                width: parent.width - 24
                                spacing: 12
                                Image {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    width: 64
                                    height: 64
                                    source: Quickshell.iconPath(tile.modelData.icon || "application-x-executable", "application-x-executable")
                                    fillMode: Image.PreserveAspectFit
                                }
                                Text {
                                    width: parent.width
                                    text: tile.modelData.name
                                    color: "#eeeeee"
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 16
                                    horizontalAlignment: Text.AlignHCenter
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                id: pointer
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                // GridView steals drags for kinetic touch scrolling.
                                onClicked: Quickshell.execDetached(["kwak-home-launch", tile.modelData.id])
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: grid.count === 0
                            text: "No applications installed"
                            color: "#b8c4b8"
                        }
                    }
                }
            }
        }
    }
}
