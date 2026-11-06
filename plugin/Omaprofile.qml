import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

// Omaprofile picker overlay.
//
// Summoned by the omaprofile CLI:
//   omarchy-shell shell summon dry.omaprofile '<json>'
// with a payload of
//   { "title": "...", "subtitle": "...",
//     "choices": [ { "id": "Profile 1", "label": "Personal", "detail": "Profile 1",
//                    "color": "#f3b2be", "glyph": "P" }, ... ],
//     "selectionFile": "/tmp/x", "doneFile": "/tmp/y" }
//
// The chosen id is written to selectionFile and doneFile is touched. On
// cancel only doneFile is touched. Same round-trip as omarchy.image-picker.

Item {
  id: root

  property var shell: null
  property var manifest: null

  property bool opened: false
  property string title: ""
  property string subtitle: ""
  property string selectionFile: ""
  property string doneFile: ""
  property int selectedIndex: 0
  // Keys are ignored for a moment after opening so a held Enter from the
  // launching keybind, or typing aimed at another window, cannot pick.
  property bool armed: false
  property int armDelayMs: 250

  // Shares the [menu] theme tokens so themes that style the menu style this.
  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color border: Color.menu.border
  property var borderSpec: Border.surfaceSpec("menu", "border", border, Math.max(1, Style.space(2)))
  property color scrim: Color.menu.scrim
  property color selectedBackground: Color.menu.selectedBackground
  property color selectedText: Color.menu.selectedText
  property color accent: Color.accent
  readonly property int cornerRadius: Style.cornerRadius
  property string fontFamily: Style.font.menuFamily
  property int contentMargin: Style.spacing.panelPadding
  property int rowHeight: Style.space(46)
  property int rowSpacing: Style.space(2)
  property int avatarSize: Style.space(30)
  property int headerHeight: Style.font.heading + Style.font.bodySmall + Style.space(10)
  property int cardWidth: Math.min(Style.space(400), panel.width - Style.gapsOut * 2)
  property int cardHeight: Math.min(
    root.contentMargin * 2 + root.headerHeight + Style.spacing.lg
      + choices.count * (root.rowHeight + root.rowSpacing),
    panel.height - Style.gapsOut * 2)

  ListModel { id: choices }

  function pluginId() {
    return (root.manifest && root.manifest.id) || "dry.omaprofile"
  }

  // Lifecycle: shell.summon(id, payload) -> open(payload); shell.hide(id) -> close().
  function open(payload) {
    var args = {}
    if (payload) {
      try { args = JSON.parse(payload) || {} } catch (e) { args = {} }
    }

    // A pick was still pending: release its caller before taking the new one.
    if (root.doneFile) root.finish("")

    root.title = String(args.title || "Choose")
    root.subtitle = String(args.subtitle || "")
    root.selectionFile = String(args.selectionFile || "")
    root.doneFile = String(args.doneFile || "")

    choices.clear()
    var list = Array.isArray(args.choices) ? args.choices : []
    for (var i = 0; i < list.length; i++) {
      var c = list[i] || {}
      var id = String(c.id || "")
      if (!id) continue
      var label = String(c.label || id)
      choices.append({
        choiceId: id,
        label: label,
        detail: String(c.detail || ""),
        choiceColor: String(c.color || ""),
        glyph: String(c.glyph || label.charAt(0).toUpperCase())
      })
    }

    var sel = parseInt(args.selected)
    root.selectedIndex = (isNaN(sel) || sel < 0 || sel >= choices.count) ? 0 : sel
    root.opened = true
    root.armed = false
    armTimer.restart()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Timer {
    id: armTimer
    interval: root.armDelayMs
    repeat: false
    onTriggered: root.armed = true
  }

  function close() {
    root.finish("")
  }

  function dismiss() {
    root.finish("")
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide(root.pluginId())
  }

  // Also reachable over IPC for scripting/tests:
  //   omarchy-shell shell call dry.omaprofile choose 1
  function choose(index) {
    var i = parseInt(index)
    if (isNaN(i) || i < 0 || i >= choices.count) return "invalid"
    var id = choices.get(i).choiceId
    root.finish(id)
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide(root.pluginId())
    return id
  }

  function finish(value) {
    var sel = root.selectionFile
    var done = root.doneFile
    root.selectionFile = ""
    root.doneFile = ""
    root.opened = false
    if (!done) return

    var cmd = ""
    if (value && sel)
      cmd += "printf '%s\\n' " + Util.shellQuote(value) + " > " + Util.shellQuote(sel) + "; "
    cmd += ": > " + Util.shellQuote(done)
    Quickshell.execDetached(["bash", "-c", cmd])
  }

  function move(delta) {
    if (choices.count === 0) return
    root.selectedIndex = (root.selectedIndex + delta + choices.count) % choices.count
  }

  function glyphColor(fill) {
    var c = Qt.color(fill)
    var lum = 0.299 * c.r + 0.587 * c.g + 0.114 * c.b
    return lum > 0.55 ? "#1b1d1f" : "#f4f4f4"
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omaprofile"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.opened ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    BorderSurface {
      id: card
      width: root.cardWidth
      height: root.cardHeight
      radius: root.cornerRadius
      anchors.centerIn: parent
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
          var k = event.key
          if (!root.armed) {
            event.accepted = true
            return
          }
          if (k === Qt.Key_Escape) {
            root.dismiss()
          } else if (k === Qt.Key_Down || k === Qt.Key_J || k === Qt.Key_Tab) {
            root.move(1)
          } else if (k === Qt.Key_Up || k === Qt.Key_K || k === Qt.Key_Backtab) {
            root.move(-1)
          } else if (k === Qt.Key_Home) {
            root.selectedIndex = 0
          } else if (k === Qt.Key_End) {
            root.selectedIndex = Math.max(0, choices.count - 1)
          } else if (k === Qt.Key_Return || k === Qt.Key_Enter || k === Qt.Key_Space) {
            root.choose(root.selectedIndex)
          } else if (k >= Qt.Key_1 && k <= Qt.Key_9) {
            var n = k - Qt.Key_1
            if (n < choices.count) root.choose(n)
          } else {
            // First letter of a label jumps to and picks that choice.
            var t = (event.text || "").toLowerCase()
            if (t.length === 1) {
              for (var i = 0; i < choices.count; i++) {
                if (choices.get(i).label.charAt(0).toLowerCase() === t) {
                  root.choose(i)
                  break
                }
              }
            }
          }
          event.accepted = true
        }
      }

      Column {
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: Style.spacing.lg

        Column {
          width: parent.width
          height: root.headerHeight
          spacing: Style.space(4)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.title
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
            elide: Text.ElideRight
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            visible: root.subtitle.length > 0
            text: root.subtitle
            color: root.foreground
            opacity: 0.6
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideMiddle
          }
        }

        ListView {
          id: list
          width: parent.width
          height: parent.height - root.headerHeight - parent.spacing
          model: choices
          clip: true
          spacing: root.rowSpacing
          boundsBehavior: Flickable.StopAtBounds
          currentIndex: root.selectedIndex
          highlightFollowsCurrentItem: true

          delegate: Rectangle {
            id: row
            required property int index
            required property string choiceId
            required property string label
            required property string detail
            required property string choiceColor
            required property string glyph

            readonly property bool hasCursor: index === root.selectedIndex
            readonly property color fill: row.choiceColor.length > 0 ? Qt.color(row.choiceColor) : root.accent

            width: ListView.view.width
            height: root.rowHeight
            radius: root.cornerRadius
            color: hasCursor ? root.selectedBackground : "transparent"

            Rectangle {
              id: avatar
              width: root.avatarSize
              height: root.avatarSize
              radius: width / 2
              color: row.fill
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter

              Text {
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: row.glyph
                color: root.glyphColor(row.fill)
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
              }
            }

            Column {
              anchors.left: avatar.right
              anchors.leftMargin: Style.space(12)
              anchors.right: shortcut.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: row.label
                color: row.hasCursor ? root.selectedText : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                visible: row.detail.length > 0
                text: row.detail
                color: root.foreground
                opacity: 0.55
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Text {
              id: shortcut
              textFormat: Text.PlainText
              visible: row.index < 9
              text: String(row.index + 1)
              color: root.foreground
              opacity: row.hasCursor ? 0.8 : 0.4
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              anchors.right: parent.right
              anchors.rightMargin: Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
            }

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onContainsMouseChanged: if (containsMouse) root.selectedIndex = row.index
              onClicked: root.choose(row.index)
            }
          }
        }
      }
    }
  }
}
