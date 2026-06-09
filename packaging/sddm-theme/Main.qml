/***************************************************************************
* Copyright (c) 2013 Abdurrahman AVCI <abdurrahmanavci@gmail.com
* Copyright (c) 2015-2018 Lubuntu Artwork Team
* WarDragon Console status widget overlay added on top of the upstream theme.
*
* Permission is hereby granted, free of charge, to any person
* obtaining a copy of this software and associated documentation
* files (the "Software"), to deal in the Software without restriction,
* including without limitation the rights to use, copy, modify, merge,
* publish, distribute, sublicense, and/or sell copies of the Software,
* and to permit persons to whom the Software is furnished to do so,
* subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included
* in all copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
* OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
* THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
* OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
* ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
* OR OTHER DEALINGS IN THE SOFTWARE.
*
***************************************************************************/

import QtQuick 2.0
import SddmComponents 2.0

Rectangle {
    id: container
    width: 1024
    height: 768

    property int sessionIndex: session.index

    TextConstants { id: textConstants }

    Connections {
        target: sddm
        onLoginSucceeded: {
        }

        onLoginFailed: {
            txtMessage.text = textConstants.loginFailed
            listView.currentItem.password.text = ""
        }
    }

    Repeater {
        model: screenModel
        Background {
            x: geometry.x; y: geometry.y; width: geometry.width; height:geometry.height
            source: config.background
            fillMode: Image.PreserveAspectCrop
            onStatusChanged: {
                if (status == Image.Error && source != config.defaultBackground) {
                    source = config.defaultBackground
                }
            }
        }
    }

    Rectangle {
        property variant geometry: screenModel.geometry(screenModel.primary)
        x: geometry.x; y: geometry.y; width: geometry.width; height: geometry.height
        color: "transparent"

        Component {
            id: userDelegate

            PictureBox {
                anchors.verticalCenter: parent.verticalCenter
                name: (model.realName === "") ? model.name : model.realName
                icon: model.icon

                focus: (listView.currentIndex === index) ? true : false
                state: (listView.currentIndex === index) ? "active" : ""

                onLogin: sddm.login(model.name, password, sessionIndex);

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onEntered: listView.currentIndex = index
                    onClicked: listView.focus = true
                }
            }
        }

        Row {
            anchors.fill: parent

                Text {
                    id: txtMessage
                    anchors.top: usersContainer.bottom;
                    anchors.margins: 20
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: "white"
                    text: textConstants.promptSelectUser
                    font.pixelSize: 16
                    font.family: "Ubuntu"
                }

		Item {
			id: usersContainer
			width: parent.width; height: 300
			anchors.verticalCenter: parent.verticalCenter

                    ImageButton {
                        id: prevUser
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 10
                        source: "angle-left.png"
                        onClicked: listView.decrementCurrentIndex()

                        KeyNavigation.backtab: btnShutdown; KeyNavigation.tab: listView
                    }



                    ListView {
                        id: listView
                        height: parent.height
                        anchors.left: prevUser.right; anchors.right: nextUser.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 10

                        clip: true
                        focus: true

                        spacing: 5

                        model: userModel
                        delegate: userDelegate
                        orientation: ListView.Horizontal
                        currentIndex: userModel.lastIndex

                        KeyNavigation.backtab: prevUser; KeyNavigation.tab: nextUser
                    }

                    ImageButton {
                        id: nextUser
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 10
                        source: "angle-right.png"
                        onClicked: listView.incrementCurrentIndex()
                        KeyNavigation.backtab: listView; KeyNavigation.tab: session
                    }
                }

            }
        }

        Rectangle {
            id: actionBar
            anchors.top: parent.top;
            anchors.horizontalCenter: parent.horizontalCenter
            width: parent.width; height: 64
            color: "#44ffffff"

            Row {
                anchors.left: parent.left
                anchors.margins: 5
                height: parent.height
                spacing: 5

                Text {
			height: parent.height
			anchors.verticalCenter: parent.verticalCenter
			text: textConstants.session
			font.pixelSize: 14
			color: "white"
			verticalAlignment: Text.AlignVCenter
                }

                ComboBox {
			id: session
			width: 245
			anchors.verticalCenter: parent.verticalCenter
			arrowIcon: "angle-down.png"
			model: sessionModel
			index: sessionModel.lastIndex
			font.pixelSize: 14
			color: "#44ffffff"
			KeyNavigation.backtab: nextUser; KeyNavigation.tab: layoutBox
                }

                Text {
                    height: parent.height
                    anchors.verticalCenter: parent.verticalCenter
                    text: textConstants.layout
                    font.pixelSize: 14
                    color: "white"
                    verticalAlignment: Text.AlignVCenter
                }

                LayoutBox {
                    id: layoutBox
                    width: 90
                    anchors.verticalCenter: parent.verticalCenter
                    font.pixelSize: 14
                    arrowIcon: "angle-down.png"
                    KeyNavigation.backtab: session; KeyNavigation.tab: btnShutdown
                }
            }

            Row {
                height: parent.height
                anchors.right: parent.right
                anchors.margins: 5
                spacing: 5

            Clock {
			id: clock
			color: "white"
			timeFont.family: "Ubuntu"
			timeFont.bold: true
			timeFont.pixelSize: 28
			dateFont.pixelSize: 12
        	}

			ImageButton {
			id: btnSuspend
			height: parent.height
			source: "suspend.png"
			visible: sddm.canSuspend
			onClicked: sddm.suspend()
			KeyNavigation.backtab: layoutBox; KeyNavigation.tab: btnReboot
			}

			ImageButton {
			id: btnReboot
			height: parent.height
			source: "reboot.png"
			visible: sddm.canReboot
			onClicked: sddm.reboot()
			KeyNavigation.backtab: btnSuspend; KeyNavigation.tab: btnShutdown
			}

			ImageButton {
			id: btnShutdown
			height: parent.height
			source: "shutdown.png"
			visible: sddm.canPowerOff
			onClicked: sddm.powerOff()
			KeyNavigation.backtab: btnReboot; KeyNavigation.tab: prevUser
			}

	}


    }

    // ============================================================
    //  WarDragon Console status panel — added by the wardragon theme.
    //  Reads /api/snapshot from the local console every few seconds
    //  and surfaces kit health at the login screen. Failure modes
    //  (console down, network refused, JSON malformed) just leave the
    //  panel showing the last-known state; nothing in here can block
    //  the login form.
    // ============================================================
    Rectangle {
        id: wdStatus
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.bottomMargin: 24
        anchors.rightMargin: 24
        width: 360
        height: 320
        color: "#cc0f1416"
        radius: 8
        border.color: "#2a3038"
        border.width: 1

        property var snap: ({})
        property var summary: ({})
        property var tether: ({})
        property var sources: []
        property var dragonsig: ({})
        property bool reachable: false

        function prettyName(name) {
            var nm = (name || "").toLowerCase();
            if (nm === "wifi") return "WiFi";
            if (nm === "ble") return "BLE";
            if (nm === "dji") return "DJI";
            if (nm === "uart") return "UART";
            if (nm.length > 0) return nm.charAt(0).toUpperCase() + nm.slice(1);
            return name;
        }

        function sourceColor(s) {
            if (!s.enabled) return "#6b7280";
            if (s.state === "connected") return "#22c55e";
            if (s.state === "connecting" || s.state === "reconnecting") return "#eab308";
            if (s.state === "error" || s.state === "dead") return "#ef4444";
            return "#6b7280";
        }

        function sourceTrailing(s) {
            if (!s.enabled) return "disabled";
            if (s.state && s.state !== "connected") return s.state;
            return s.rate.toFixed(1) + "/s";
        }

        function rebuildSources(d) {
            // Whitelist: only the three receivers a customer cares about.
            // UART and legacy sniffle are intentionally hidden.
            var keep = { "wifi": 0, "ble": 1, "dji": 2 };
            var raw = ((d.droneid || {}).payload || {}).sources || {};
            var arr = [];
            for (var key in raw) {
                var s = raw[key] || {};
                var nm = (s.name || key).toLowerCase();
                if (!(nm in keep)) continue;
                arr.push({
                    key: key,
                    name: prettyName(nm),
                    sortKey: keep[nm],
                    enabled: s.enabled === true,
                    state: s.state_str || "",
                    rate: s.messages_per_sec || 0,
                    total: s.messages_total || 0
                });
            }
            arr.sort(function(a, b) { return a.sortKey - b.sortKey; });
            return arr;
        }

        function dragonsigSummary(p) {
            if (!p) return "DragonSig: no data";
            var parts = ["DragonSig:"];
            parts.push(p.sdr_ok ? "SDR ok" : "SDR down");
            if (p.phase) parts.push(p.phase + (p.mode ? "/" + p.mode : ""));
            if (typeof p.noise_floor_db === "number") parts.push(p.noise_floor_db.toFixed(1) + " dBm");
            return parts.join(" · ");
        }

        function fetchStatus() {
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "http://127.0.0.1:4280/api/snapshot");
            xhr.timeout = 2000;
            xhr.onreadystatechange = function() {
                if (xhr.readyState === XMLHttpRequest.DONE) {
                    if (xhr.status === 200) {
                        try {
                            var d = JSON.parse(xhr.responseText);
                            wdStatus.snap = d;
                            wdStatus.summary = d.summary || {};
                            wdStatus.tether = (d.access || {}).tether || {};
                            wdStatus.sources = wdStatus.rebuildSources(d);
                            wdStatus.dragonsig = (d.dragonsig || {}).payload || {};
                            wdStatus.reachable = true;
                        } catch(e) {
                            wdStatus.reachable = false;
                        }
                    } else {
                        wdStatus.reachable = false;
                    }
                }
            };
            xhr.send();
        }

        Component.onCompleted: fetchStatus()

        Timer {
            interval: 3000
            running: true
            repeat: true
            onTriggered: wdStatus.fetchStatus()
        }

        Column {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 5

            Row {
                spacing: 8
                Text {
                    text: "WarDragon Kit"
                    color: "white"
                    font.family: "Ubuntu"
                    font.pixelSize: 14
                    font.bold: true
                }
                Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: wdStatus.reachable ? "#22c55e" : "#6b7280"
                }
                Text {
                    text: wdStatus.reachable ? "console online" : "console unreachable"
                    color: "#94a3b8"
                    font.family: "Ubuntu"
                    font.pixelSize: 10
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Text {
                text: "Receivers (droneid-go)"
                color: "#94a3b8"
                font.family: "Ubuntu"
                font.pixelSize: 10
            }

            Repeater {
                model: wdStatus.sources
                Row {
                    spacing: 8
                    Rectangle {
                        width: 9; height: 9; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: wdStatus.sourceColor(modelData)
                    }
                    Text {
                        text: modelData.name
                        color: "#d4d4d8"
                        font.family: "Ubuntu"
                        font.pixelSize: 12
                        width: 80
                    }
                    Text {
                        text: wdStatus.sourceTrailing(modelData)
                        color: "#94a3b8"
                        font.family: "Ubuntu"
                        font.pixelSize: 11
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }

            Item { width: 1; height: 2 }

            Text {
                text: wdStatus.dragonsigSummary(wdStatus.dragonsig)
                color: "#d4d4d8"
                font.family: "Ubuntu"
                font.pixelSize: 11
                wrapMode: Text.WrapAnywhere
                width: 332
            }

            Item { width: 1; height: 2 }

            Text {
                text: "Drones: " + (wdStatus.summary.drone_count || 0) + "    Signals: " + (wdStatus.summary.signal_count || 0)
                color: "#d4d4d8"
                font.family: "Ubuntu"
                font.pixelSize: 12
            }

            Text {
                // Only render a URL when the snapshot says a tether is
                // actually bound (tether.active). Belt-and-braces against
                // stale state and against any future misclassification by
                // the tether watcher — if active is false, the panel says
                // "not connected" regardless of what url fields contain.
                text: wdStatus.tether.active === true
                      ? (wdStatus.tether.stable_url || wdStatus.tether.url || "Tether: bound (no URL)")
                      : "Tether: not connected"
                color: wdStatus.tether.active === true ? "#22c55e" : "#6b7280"
                font.family: "Ubuntu"
                font.pixelSize: 11
                wrapMode: Text.WrapAnywhere
                width: 332
            }
        }
    }
}
