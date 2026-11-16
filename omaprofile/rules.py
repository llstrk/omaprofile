"""The rules editor: `omaprofile` in a terminal, or `omaprofile rules`.

Editor holds the state and turns keys into changes; run() draws it in the
terminal with the current Omarchy theme's colours. Every change re-reads the
config file, applies itself to the rules found there and writes the file back,
so a rule added meanwhile (Shift+Enter in the picker) is kept. With
OMAPROFILE_SCREEN_FILE set, what is on screen is also written there as JSON
after each key (for the feature steps; see tests/steps_rules_editor.py).
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import sys
import termios
import tomllib
import tty
from pathlib import Path

from . import config as cfgmod
from .browser import Browser, BrowserError, Profile, check_rules, display_path
from .picker import detached

THEME_COLORS = cfgmod.STATE_HOME / "omarchy" / "current" / "theme" / "colors.toml"
NO_RULES = "No rules yet. Shift+Enter in the picker adds one, or press a."
NO_PROFILES = "Chromium has no profiles yet. Open it once, then add rules."
HINTS = {
    "list": "j/k move · Enter change · a add · d delete · J/K reorder · q quit",
    "pattern": "Enter next · Esc cancel · Ctrl+U clear",
    "profile": "j/k move · Enter save · Esc cancel",
    "confirm": "y delete · any other key keeps it",
}


# ---------------------------------------------------------------- rules in the config file

def pattern_of(rule: dict) -> str:
    return rule.get("host") or rule.get("url") or ""


def kind_of(pattern: str) -> str:
    return "url" if "://" in pattern else "host"


def check_pattern(text: str) -> tuple[str, str]:
    """(kind, pattern) from what was typed, or ValueError saying what to type instead."""
    text = text.strip()
    if not text:
        raise ValueError("Type a host like github.com, or a URL pattern like https://x.com/*")
    if any(c.isspace() for c in text):
        raise ValueError("A pattern has no spaces.")
    if kind_of(text) == "url":
        return "url", text
    if "/" in text:
        raise ValueError("A host has no path. For part of a site use a URL pattern, like "
                         f"https://{text.rstrip('/*')}/*")
    return "host", text.lower()


def read_rules(path: Path) -> tuple[dict, list[dict]]:
    cfg = cfgmod.load(path) if path.exists() else json.loads(json.dumps(cfgmod.DEFAULT_CONFIG))
    rules = check_rules(cfg.setdefault("browser", {}).setdefault("rules", []))
    return cfg, rules


def change_rules(change, path: Path) -> list[dict]:
    """Apply change(rules) to the rules in the file now and write it back; returns the rules."""
    cfg, rules = read_rules(path)
    change(rules)
    cfg["browser"]["rules"] = rules
    cfgmod.save(cfg, path)
    return rules


class Gone(Exception):
    """The rule is no longer in the file (changed or deleted elsewhere)."""


def find(rules: list[dict], rule: dict) -> int:
    for i, r in enumerate(rules):
        if r == rule:
            return i
    raise Gone()


# ---------------------------------------------------------------- the editor

class Editor:
    def __init__(self, cfg: dict, path: Path = cfgmod.CONFIG_FILE):
        self.path = path
        self.browser = Browser(cfg)
        self.mode = "list"
        self.cursor = 0
        self.input = ""
        self.choice = 0
        self.message = ""
        self.message_kind = ""  # "", "ok", "error"
        self.editing: dict | None = None  # the rule Enter is changing; None while adding
        self.pending: tuple[str, str] | None = None  # (kind, pattern) once the pattern is typed
        self.done = False
        self.reload()

    # -------------------------------------------------------------- state

    def reload(self) -> None:
        try:
            self.profiles: list[Profile] = self.browser.profiles()
        except BrowserError:
            self.profiles = []
        _, self.rules = read_rules(self.path)
        self.cursor = min(self.cursor, len(self.rules))

    def profile_label(self, profile: Profile) -> str:
        """What a rule stores: the display name, or the directory when two profiles share it."""
        same = [p for p in self.profiles if p.name == profile.name]
        return profile.name if profile.name and len(same) == 1 else profile.directory

    def resolve(self, wanted: str) -> Profile | None:
        for p in self.profiles:
            if wanted in (p.directory, p.name):
                return p
        return None

    def note(self, rule: dict) -> str:
        notes = []
        if "url" in rule and not rule.get("host"):
            notes.append("url")
        if self.profiles and not self.resolve(rule["profile"]):
            notes.append("profile gone")
        return ", ".join(notes)

    def rows(self) -> list[dict]:
        return [{"pattern": pattern_of(r), "profile": r["profile"], "note": self.note(r)} for r in self.rules]

    def status(self) -> str:
        if self.message:
            return self.message
        if self.mode == "confirm":
            r = self.rules[self.cursor]
            return f"Delete {pattern_of(r)} → {r['profile']}? y/n"
        if self.mode == "list" and not self.rules:
            return NO_RULES
        return ""

    def say(self, text: str, kind: str = "") -> None:
        self.message, self.message_kind = text, kind

    # -------------------------------------------------------------- keys

    def handle(self, key: str) -> None:
        self.say("")
        getattr(self, "key_" + self.mode)(key)

    def key_list(self, key: str) -> None:
        on_rule = self.cursor < len(self.rules)
        if key in ("j", "down"):
            self.cursor = min(self.cursor + 1, len(self.rules))
        elif key in ("k", "up"):
            self.cursor = max(self.cursor - 1, 0)
        elif key == "home" or key == "g":
            self.cursor = 0
        elif key == "end" or key == "G":
            self.cursor = len(self.rules)
        elif key in ("enter",) and on_rule:
            self.start_typing(self.rules[self.cursor])
        elif key in ("enter", "a"):
            self.start_typing(None)
        elif key in ("d", "delete") and on_rule:
            self.mode = "confirm"
        elif key in ("J", "K") and on_rule:
            self.move(1 if key == "J" else -1)
        elif key in ("q", "esc", "ctrl+c"):
            self.done = True

    def start_typing(self, rule: dict | None) -> None:
        self.reload()
        if not self.profiles:
            self.say(NO_PROFILES, "error")
            return
        self.editing = rule
        self.input = pattern_of(rule) if rule else ""
        self.mode = "pattern"

    def key_pattern(self, key: str) -> None:
        if key in ("esc", "ctrl+c"):
            self.mode = "list"
        elif key == "enter":
            try:
                self.pending = check_pattern(self.input)
            except ValueError as e:
                self.say(str(e), "error")
                return
            self.choices = list(self.profiles)
            current = self.resolve(self.editing["profile"]) if self.editing else None
            self.choice = self.choices.index(current) if current in self.choices else 0
            self.mode = "profile"
        elif key == "backspace":
            self.input = self.input[:-1]
        elif key == "ctrl+u":
            self.input = ""
        elif len(key) == 1 and key.isprintable():
            self.input += key

    def key_profile(self, key: str) -> None:
        if key in ("j", "down"):
            self.choice = min(self.choice + 1, len(self.choices) - 1)
        elif key in ("k", "up"):
            self.choice = max(self.choice - 1, 0)
        elif key in ("esc", "ctrl+c"):
            self.mode = "list"
        elif key.isdigit() and 0 < int(key) <= len(self.choices):
            self.choice = int(key) - 1
            self.save()
        elif key == "enter":
            self.save()

    def key_confirm(self, key: str) -> None:
        rule = self.rules[self.cursor]
        self.mode = "list"
        if key in ("y", "Y"):
            self.apply(lambda rules: rules.pop(find(rules, rule)), f"Deleted {pattern_of(rule)}")

    # -------------------------------------------------------------- changes

    def save(self) -> None:
        kind, pattern = self.pending
        profile = self.choices[self.choice]
        label = self.profile_label(profile)
        old = self.editing

        def change(rules: list[dict]) -> None:
            same = [j for j, r in enumerate(rules) if r.get(kind) == pattern
                    and set(r) <= {"host", "url", "profile"} and len([k for k in ("host", "url") if k in r]) == 1]
            if old is None:
                if same:  # the pattern has a rule already: change that one
                    rules[same[0]] = {**rules[same[0]], "profile": label}
                else:
                    rules.append({kind: pattern, "profile": label})
                return
            i = find(rules, old)
            shown = "host" if old.get("host") else "url"
            rest = {k: v for k, v in old.items() if k not in (shown, kind, "profile")}
            rules[i] = {kind: pattern, **rest, "profile": label}
            for j in reversed(same):  # no second rule for the same pattern
                if j != i:
                    del rules[j]

        self.mode = "list"
        if self.apply(change, f"Saved: {pattern} opens in {profile.name or label}"):
            for i, r in enumerate(self.rules):
                if pattern_of(r) == pattern:
                    self.cursor = i
                    break

    def move(self, step: int) -> None:
        rule = self.rules[self.cursor]

        def change(rules: list[dict]) -> None:
            i = find(rules, rule)
            j = i + step
            if 0 <= j < len(rules):
                rules[i], rules[j] = rules[j], rules[i]

        if self.apply(change, ""):
            self.cursor = next((i for i, r in enumerate(self.rules) if r == rule), self.cursor)

    def apply(self, change, message: str) -> bool:
        try:
            self.rules = change_rules(change, self.path)
        except Gone:
            self.reload()
            self.say("That rule was changed outside the editor; here is the list as it is now.", "error")
            return False
        except (OSError, cfgmod.ConfigError) as e:
            self.say(f"Not saved: {e}", "error")
            return False
        self.cursor = min(self.cursor, len(self.rules))
        self.say(message, "ok")
        return True

    # -------------------------------------------------------------- drawing

    def lines(self, width: int, height: int) -> list[tuple[str, str]]:
        """The screen as (text, style) rows; styles are names in Theme."""
        out: list[tuple[str, str]] = [
            (" omaprofile rules", "title"),
            (f" Links matching a rule open in its profile without the picker. First match wins.", "muted"),
            (f" {display_path(self.path)}", "muted"),
            ("", ""),
        ]
        rows = self.rows()
        pw = max([len(r["pattern"]) for r in rows] + [12])
        fw = max([len(r["profile"]) for r in rows] + [8])
        body: list[tuple[str, str]] = []
        for i, r in enumerate(rows):
            mark = "▌" if i == self.cursor and self.mode in ("list", "confirm") else " "
            text = f" {mark} {i + 1:>2}  {r['pattern']:<{pw}}  →  {r['profile']:<{fw}}  {r['note']}".rstrip()
            style = "cursor" if mark != " " else ("warn" if "profile gone" in r["note"] else "")
            body.append((text, style))
        add_mark = "▌" if self.cursor == len(rows) and self.mode == "list" else " "
        body.append((f" {add_mark}  +  Add a rule", "cursor" if add_mark != " " else "accent"))
        footer: list[tuple[str, str]] = [("", "")]
        if self.mode == "pattern":
            title = f"Change {pattern_of(self.editing)}" if self.editing else "Add a rule"
            footer += [(f" {title}", "accent"),
                       (f" Host or URL pattern › {self.input}▏", "input")]
        elif self.mode == "profile":
            footer += [(f" Open {self.pending[1]} in", "accent")]
            for i, p in enumerate(self.choices):
                mark = "▌" if i == self.choice else " "
                footer.append((f" {mark} {i + 1}  ● {p.name or p.directory}", f"profile:{i}"))
        status = self.status()
        footer += [("", ""), (f" {status}", {"ok": "ok", "error": "error"}.get(self.message_kind, "status")
                               if self.message else ("warn" if self.mode == "confirm" else "muted"))]
        room = max(height - len(out) - len(footer) - 2, 3)
        if len(body) > room:  # scroll so the cursor stays in view
            top = min(max(self.cursor - room // 2, 0), len(body) - room)
            body = body[top:top + room]
        screen = out + body + footer
        screen += [("", "")] * max(height - len(screen) - 1, 0)
        screen.append((" " + HINTS[self.mode], "muted"))
        return [(text[:width], style) for text, style in screen[:height]]

    def snapshot(self, keys: int, lines: list[tuple[str, str]]) -> dict:
        snap = {"keys": keys, "mode": self.mode, "lines": [t for t, _ in lines], "rows": self.rows(),
                "cursor": self.cursor, "status": self.status(), "input": self.input}
        if self.mode == "profile":
            snap.update(choices=[p.name or p.directory for p in self.choices], choice=self.choice)
        return snap


# ---------------------------------------------------------------- the terminal

def hex_rgb(value: str) -> tuple[int, int, int] | None:
    value = value.strip().lstrip("#")
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return None


class Theme:
    """ANSI styles from Omarchy's current theme (colors.toml), or plain ones without it."""

    def __init__(self, path: Path = THEME_COLORS):
        try:
            colors = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            colors = {}
        self.colors = colors
        fg = lambda name, fallback: self.fg(colors.get(name), fallback)  # noqa: E731
        sel = self.bg(colors.get("selection"), "7")
        self.styles = {
            "": "",
            "title": "\x1b[1m" + fg("accent", "34"),
            "accent": fg("accent", "34"),
            "muted": fg("dark_foreground", "2") if colors.get("dark_foreground") else fg("muted", "2"),
            "cursor": sel + fg("bright_foreground", "1"),
            "warn": fg("yellow", "33"),
            "error": fg("red", "31"),
            "ok": fg("green", "32"),
            "status": "",
            "input": fg("bright_foreground", "1"),
        }

    @staticmethod
    def fg(value: str | None, fallback: str) -> str:
        rgb = hex_rgb(value) if value else None
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if rgb else f"\x1b[{fallback}m"

    @staticmethod
    def bg(value: str | None, fallback: str) -> str:
        rgb = hex_rgb(value) if value else None
        return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if rgb else f"\x1b[{fallback}m"

    def paint(self, text: str, style: str, editor: Editor, width: int) -> str:
        if style.startswith("profile:"):
            i = int(style.split(":")[1])
            profile = editor.choices[i]
            dot = text.index("●")
            base = self.styles["cursor"] if i == editor.choice else ""
            color = self.fg(profile.color, "39") if profile.color else ""
            line = base + text[:dot] + color + "●" + "\x1b[39m" + base + text[dot + 1:]
            return line + (" " * (width - len(text)) if i == editor.choice else "") + "\x1b[0m"
        code = self.styles.get(style, "")
        pad = " " * (width - len(text)) if style == "cursor" else ""
        return f"{code}{text}{pad}\x1b[0m" if code else text


KEY_NAMES = {"\r": "enter", "\n": "enter", "\x1b": "esc", "\x7f": "backspace", "\x08": "backspace",
             "\x15": "ctrl+u", "\x03": "ctrl+c", "\x04": "ctrl+c", "\t": "tab"}
ESCAPES = {"[A": "up", "[B": "down", "OA": "up", "OB": "down", "[3~": "delete", "[H": "home", "[F": "end",
           "OH": "home", "OF": "end", "[1~": "home", "[4~": "end", "[C": "right", "[D": "left"}


def parse_keys(data: str) -> list[str]:
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b" and i + 1 < len(data) and data[i + 1] in "[O":
            j = i + 2
            while j < len(data) and not (data[j].isalpha() or data[j] == "~"):
                j += 1
            seq = data[i + 1:j + 1]
            keys.append(ESCAPES.get(seq, "unknown"))
            i = j + 1
            continue
        keys.append(KEY_NAMES.get(ch, ch))
        i += 1
    return keys


def run(cfg: dict) -> None:
    editor = Editor(cfg)
    theme = Theme()
    fd_in, out = sys.stdin.fileno(), sys.stdout
    screen_file = os.environ.get("OMAPROFILE_SCREEN_FILE")
    esc_delay = int(os.environ.get("ESCDELAY", "25")) / 1000
    saved = termios.tcgetattr(fd_in)
    keys = 0
    resized = [False]
    old_winch = signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))

    def draw() -> None:
        size = os.get_terminal_size(out.fileno())
        lines = editor.lines(size.columns, size.lines)
        buf = ["\x1b[H"]
        for row, (text, style) in enumerate(lines):
            buf.append(f"\x1b[{row + 1};1H" + theme.paint(text, style, editor, size.columns) + "\x1b[K")
        buf.append("\x1b[J")
        out.write("".join(buf))
        out.flush()
        if screen_file:
            tmp = screen_file + ".tmp"
            Path(tmp).write_text(json.dumps(editor.snapshot(keys, lines), ensure_ascii=False))
            os.replace(tmp, screen_file)

    try:
        tty.setraw(fd_in)
        out.write("\x1b[?1049h\x1b[?25l")
        draw()
        while not editor.done:
            ready, _, _ = select.select([fd_in], [], [], 0.5)
            if not ready:
                if resized[0]:
                    resized[0] = False
                    draw()
                continue
            data = os.read(fd_in, 1024)
            if not data:
                break
            text = data.decode(errors="ignore")
            if text == "\x1b":  # Esc alone, or the start of an arrow key split across reads
                more, _, _ = select.select([fd_in], [], [], esc_delay)
                if more:
                    text += os.read(fd_in, 1024).decode(errors="ignore")
            for key in parse_keys(text):
                editor.handle(key)
                keys += 1
                if editor.done:
                    break
            draw()
    finally:
        termios.tcsetattr(fd_in, termios.TCSADRAIN, saved)
        signal.signal(signal.SIGWINCH, old_winch)
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()


def open_in_terminal() -> None:
    """The rules editor in a floating terminal, the way Omarchy's own TUI launchers do it
    (omarchy-tui-install: xdg-terminal-exec --app-id=TUI.float). Replaces this process."""
    from .install import LAUNCHER  # install imports browser, which this module needs first
    if not shutil.which("xdg-terminal-exec"):
        raise BrowserError("xdg-terminal-exec is not installed; run omaprofile rules in a terminal")
    detached(["xdg-terminal-exec", "--app-id=TUI.float", "--title=Omaprofile rules", "-e", str(LAUNCHER), "rules"])


def list_rules(cfg: dict) -> str:
    editor = Editor(cfg)
    rows = editor.rows()
    if not rows:
        return "no rules (Shift+Enter in the picker adds one; `omaprofile rules` edits them)"
    width = max(len(r["pattern"]) for r in rows)
    lines = []
    for i, r in enumerate(rows, 1):
        gone = "  (profile gone)" if "profile gone" in r["note"] else ""
        lines.append(f"{i}  {r['pattern']:<{width}}  {r['profile']}{gone}")
    return "\n".join(lines)
