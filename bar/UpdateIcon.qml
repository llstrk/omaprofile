import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// The update icon: hidden while omaprofile is up to date, shown when the
// daily check (run by the overlay) found a newer version. Left click opens
// `omaprofile update` in Omarchy's floating terminal; right click hides the
// icon until the next version.
//
// What to show comes from `omaprofile update --status`, read again whenever
// the check's state file changes and every ten minutes. Bar widgets are not
// told their plugin folder, so this runs the ~/.local/bin launcher, which
// exists once omaprofile is set up; without it the icon stays hidden.
//
// The widget also runs the daily check, like the overlay does. After an
// update the shell keeps the overlay it compiled before (until it restarts),
// while this widget, new in its own folder, is loaded fresh; so checks start
// right away. The check runs at most once a day however often it is asked.

BarWidget {
  id: root
  moduleName: "dry.omaprofile"

  property bool available: false
  property string tip: ""
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string cli: root.home + "/.local/bin/omaprofile"
  readonly property string stateFile:
    (Quickshell.env("XDG_STATE_HOME") || root.home + "/.local/state") + "/omaprofile/update.json"

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  visible: root.available
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: statusProc
    command: ["sh", "-c", '[ -x "$1" ] && exec "$1" update --status', "omaprofile", root.cli]
    stdout: StdioCollector {
      onStreamFinished: {
        var s = {}
        try { s = JSON.parse(text) || {} } catch (e) { s = {} }
        root.available = s.icon === true
        root.tip = String(s.tooltip || "")
      }
    }
  }

  FileView {
    path: root.stateFile
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
  }

  Timer {
    interval: 600000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  function checkForUpdate() {
    Quickshell.execDetached(["sh", "-c", '[ -x "$1" ] && exec "$1" update --check --scheduled',
                             "omaprofile", root.cli])
  }

  Timer {
    interval: 90000  // after login settles and setup has had its turn
    running: true
    repeat: false
    onTriggered: root.checkForUpdate()
  }

  Timer {
    interval: 3600000
    running: true
    repeat: true
    onTriggered: root.checkForUpdate()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    slotSize: Style.bar.statusSlot
    tooltipText: root.tip
    onPressed: function(b) {
      if (b === Qt.RightButton) {
        root.available = false
        Quickshell.execDetached([root.cli, "update", "--dismiss"])
      } else {
        Quickshell.execDetached([root.cli, "update", "--open"])
      }
    }
  }
}
