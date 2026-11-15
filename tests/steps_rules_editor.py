"""Step definitions for features/rules-editor.feature.

Uses the scenario Context of steps_updates and the steps of steps_rules. The
editor is the real omaprofile from the plugin folder, run in a pseudo-terminal
(class Terminal). With OMAPROFILE_SCREEN_FILE set, the editor writes what it
shows to that file as JSON each time it has drawn the screen, so the steps can
wait for a key to be handled and read the result:

  keys     how many keys it has handled
  mode     "list", "pattern" (typing a pattern), "profile" (choosing one) or "confirm"
  lines    the screen as plain text, one string per row
  rows     the rule rows of the list: {"pattern", "profile", "note"}
  cursor   the rule row under the cursor, from 0 ("+ Add a rule" is len(rows))
  status   the message line ("" when there is none)
  input    the pattern being typed
  choices  the profiles to choose from, and choice, the one under the cursor
"""

import fcntl
import json
import os
import struct
import subprocess
import termios
import threading
import time

from gherkin import step
from steps_rules import rules_table, write_config
from steps_updates import CONFIG_HOME, Context

KEYS = {"Enter": "\r", "Esc": "\x1b", "Up": "\x1b[A", "Down": "\x1b[B", "Backspace": "\x7f", "Ctrl+U": "\x15"}
TIMEOUT = 10


class Terminal:
    """omaprofile running in a 100x30 pseudo-terminal."""

    def __init__(self, ctx, args: list[str]):
        self.screen_file = ctx.tmp / "screen.json"
        self.screen_file.unlink(missing_ok=True)
        self.sent = 0
        self.output = b""
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        env = {**ctx.env, "OMAPROFILE_NOW": str(ctx.now), "OMAPROFILE_SCREEN_FILE": str(self.screen_file),
               "TERM": "xterm-256color", "ESCDELAY": "25"}
        self.proc = subprocess.Popen([str(ctx.cli), *args], stdin=slave, stdout=slave, stderr=slave,
                                     env=env, start_new_session=True)
        os.close(slave)
        self.master = master
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.output += chunk

    def screen(self) -> dict | None:
        try:
            return json.loads(self.screen_file.read_text())
        except (OSError, ValueError):
            return None

    def wait(self, keys: int | None = None) -> dict | None:
        """The screen once `keys` keys (default: all sent) are handled; None if the program exited first."""
        keys = self.sent if keys is None else keys
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            screen = self.screen()
            if screen and screen.get("keys", -1) >= keys:
                return screen
            if self.proc.poll() is not None:
                return None
            time.sleep(0.02)
        raise AssertionError(f"the editor did not handle key {keys} in {TIMEOUT}s; screen: {self.screen()}")

    def press(self, key: str) -> dict | None:
        os.write(self.master, KEYS.get(key, key).encode())
        self.sent += 1
        return self.wait()

    def type(self, text: str) -> dict | None:
        screen = None
        for ch in text:
            screen = self.press(ch)
        return screen

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        try:
            os.close(self.master)
        except OSError:
            pass


_close = Context.close


def close(self):
    if getattr(self, "terminal", None):
        self.terminal.close()
    _close(self)


Context.close = close


def start(ctx, command: str) -> dict | None:
    args = command.split()[1:]  # drop "omaprofile"
    ctx.begin_action()
    if getattr(ctx, "terminal", None):
        ctx.terminal.close()
    ctx.terminal = Terminal(ctx, args)
    return ctx.terminal.wait(0)


def term(ctx) -> Terminal:
    assert getattr(ctx, "terminal", None), "the editor was not started"
    return ctx.terminal


def screen(ctx) -> dict:
    s = term(ctx).wait()
    assert s is not None, f"the editor is not running; it printed:\n{output(ctx)}"
    return s


def output(ctx) -> str:
    return term(ctx).output.decode(errors="replace")


def move_to(ctx, row: int) -> dict:
    s = screen(ctx)
    while s["cursor"] != row:
        s = term(ctx).press("Down" if s["cursor"] < row else "Up")
        assert s is not None, "the editor quit"
    return s


def choose_profile(ctx, name: str) -> None:
    s = screen(ctx)
    assert s["mode"] == "profile", f"not choosing a profile; mode {s['mode']}, status {s['status']!r}"
    assert name in s["choices"], f"no profile {name!r} in {s['choices']}"
    want = s["choices"].index(name)
    while s["choice"] != want:
        s = term(ctx).press("Down" if s["choice"] < want else "Up")
    term(ctx).press("Enter")


def enter_pattern(ctx, pattern: str, profile: str) -> None:
    term(ctx).type(pattern)
    s = term(ctx).press("Enter")
    if s and s["mode"] == "profile":  # a refused pattern stays in "pattern" with a message
        choose_profile(ctx, profile)


# ------------------------------------------------------------------ Given

@step("the config already has the host and URL rules:")
def host_and_url_rules(ctx):
    rules = [{k: v for k, v in r.items() if v} for r in rules_table(ctx)]
    write_config({"browser": {"command": "chromium", "rules": rules}})


@step("Chromium has no profiles yet")
def no_profiles(ctx):
    (CONFIG_HOME / "chromium" / "Local State").unlink(missing_ok=True)


# ------------------------------------------------------------------ When

@step("I run {command} in a terminal")
def run_in_terminal(ctx, command):
    start(ctx, command)


@step("I run {command} without a terminal")
def run_without_terminal(ctx, command):
    ctx.begin_action()
    ctx.run(*command.split()[1:])


@step("I open the rules editor")
def open_editor(ctx):
    assert start(ctx, "omaprofile rules") is not None, f"the editor did not start:\n{output(ctx)}"


@step("I press {key}")
def press(ctx, key):
    term(ctx).press(key)


@step("I type {text}")
def type_text(ctx, text):
    term(ctx).type(text)


@step("I move to rule {n}")
def move_to_rule(ctx, n):
    move_to(ctx, int(n) - 1)


@step("I add a rule for {pattern} with {profile}")
def add_rule(ctx, pattern, profile):
    term(ctx).press("a")
    enter_pattern(ctx, pattern, profile)


@step("I change rule {n} to {pattern} with {profile}")
def change_rule(ctx, n, pattern, profile):
    move_to(ctx, int(n) - 1)
    term(ctx).press("Enter")
    term(ctx).press("Ctrl+U")
    enter_pattern(ctx, pattern, profile)


# ------------------------------------------------------------------ Then

@step("the rules editor is shown")
def editor_shown(ctx):
    s = screen(ctx)
    assert s["mode"] == "list", f"mode {s['mode']}"


@step("the rules editor lists:")
def editor_lists(ctx):
    s = screen(ctx)
    assert s["mode"] == "list", f"mode {s['mode']}"
    assert s["rows"] == rules_table(ctx), "the editor lists:\n" + "\n".join(s["lines"])


@step("the rules editor lists no rules")
def editor_lists_none(ctx):
    s = screen(ctx)
    assert s["rows"] == [], "the editor lists:\n" + "\n".join(s["lines"])


@step("the editor says {text}")
def editor_says(ctx, text):
    s = screen(ctx)
    assert s["status"] == text, f"status {s['status']!r}, expected {text!r}"
    assert any(text in line for line in s["lines"]), "not on the screen:\n" + "\n".join(s["lines"])


@step("the editor has quit")
def editor_quit(ctx):
    proc = term(ctx).proc
    try:
        proc.wait(TIMEOUT)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"still running; screen: {term(ctx).screen()}")
    assert proc.returncode == 0, f"exit {proc.returncode}:\n{output(ctx)}"


@step("the pattern being typed is {text}")
def pattern_typed(ctx, text):
    s = screen(ctx)
    assert s["mode"] == "pattern", f"mode {s['mode']}"
    assert s["input"] == text, f"input {s['input']!r}, expected {text!r}"


@step("the profile under the cursor is {name}")
def profile_under_cursor(ctx, name):
    s = screen(ctx)
    assert s["mode"] == "profile", f"mode {s['mode']}"
    assert s["choices"][s["choice"]] == name, f"cursor on {s['choices'][s['choice']]!r}, expected {name!r}"
