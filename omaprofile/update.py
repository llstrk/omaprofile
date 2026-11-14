"""Knowing about new versions and updating: the daily check, the bar icon's status, `omaprofile update`.

A plugin added with `omarchy plugin add` is a git clone, so a check is a
`git fetch` of its origin and a look at the fetched manifest.json; nothing
else is contacted. Updating hands over to `omarchy plugin update`, which
fast-forwards, validates and rolls back on failure, then reloads the plugin.

What the check found lives in ~/.local/state/omaprofile/update.json:

  checked      when the last check reached the repository (seconds)
  available    the newest version found, when it is newer than this one
  notified     the version a notification was shown for (once per version)
  dismissed    the version whose bar icon was hidden with a right click
  installed    the version that last ran, to say what's new after an update
  iconOffered  the bar icon was placed once; never again, so removing it sticks

OMAPROFILE_NOW (seconds since the epoch) replaces the clock, for tests.
"""

import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import PLUGIN_ID
from . import config as cfgmod
from .picker import detached, notify

SRC_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = SRC_ROOT / "bin" / "omaprofile"
STATE = cfgmod.STATE_HOME / "omaprofile" / "update.json"
SHELL_CONFIG = cfgmod.CONFIG_HOME / "omarchy" / "shell.json"
CHECK_EVERY = 24 * 3600
FETCH_TIMEOUT = 30
FLOATING_TERMINAL = "omarchy-launch-floating-terminal-with-presentation"


class UpdateError(Exception):
    pass


# ------------------------------------------------------------------ state

def now() -> int:
    return int(os.environ.get("OMAPROFILE_NOW") or time.time())


def load_state() -> dict:
    try:
        state = json.loads(STATE.read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, STATE)


def installed_version() -> str:
    try:
        return str(json.loads((SRC_ROOT / "manifest.json").read_text())["version"])
    except (OSError, ValueError, KeyError):
        return "0"


def version_key(version: str) -> tuple:
    return tuple(int(n) if n.isdigit() else -1 for n in re.findall(r"\d+|[^\d.]+", version or "0"))


def newer(candidate: str | None, than: str) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(than)


def managed() -> bool:
    """Installed by `omarchy plugin add`: this checkout is the git clone in the plugins folder."""
    try:
        return SRC_ROOT.resolve() == cfgmod.PLUGIN_DIR.resolve() and (SRC_ROOT / ".git").exists()
    except OSError:
        return False


def checks_enabled(cfg: dict) -> bool:
    updates = cfg.get("updates", {})
    return not (isinstance(updates, dict) and updates.get("check") is False)


# ------------------------------------------------------------------ the repository

def _git(*args: str, timeout: float = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")}
    return subprocess.run(["git", "-C", str(SRC_ROOT), *args], capture_output=True, text=True,
                          env=env, timeout=timeout)


def origin() -> str:
    try:
        return _git("remote", "get-url", "origin", timeout=5).stdout.strip() or "origin"
    except (OSError, subprocess.SubprocessError):
        return "origin"


def fetch() -> None:
    """Fetch the origin's HEAD into FETCH_HEAD. The plugins folder watcher ignores .git."""
    try:
        proc = _git("fetch", "--quiet", "origin", "HEAD")
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"could not reach {origin()}: {e}") from e
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise UpdateError(f"could not reach {origin()}" + (f": {detail[-1]}" if detail else ""))


def fetched_file(name: str) -> str:
    proc = _git("show", f"FETCH_HEAD:{name}", timeout=10)
    return proc.stdout if proc.returncode == 0 else ""


def fetched_version() -> str:
    try:
        return str(json.loads(fetched_file("manifest.json"))["version"])
    except (ValueError, KeyError) as e:
        raise UpdateError(f"the repository has no readable manifest.json: {e}") from e


def changes(changelog: str, after: str, upto: str) -> list[tuple[str, list[str]]]:
    """CHANGELOG.md entries newer than `after`, up to `upto`, newest first."""
    out, current = [], None
    for line in changelog.splitlines():
        m = re.match(r"^##\s+\[?v?([0-9][^\]\s]*)", line)
        if m:
            version = m.group(1)
            current = (version, []) if newer(version, after) and not newer(version, upto) else None
            if current:
                out.append(current)
        elif current and line.strip().startswith(("- ", "* ")):
            current[1].append(line.strip()[2:].strip())
    return out


# ------------------------------------------------------------------ checking

def check(cfg: dict, scheduled: bool = False) -> dict:
    """Fetch and record what is available. Scheduled checks run at most once a day."""
    state = load_state()
    if scheduled:
        if not managed() or not checks_enabled(cfg):
            return state
        since = now() - int(state.get("checked", 0) or 0)
        if 0 <= since < CHECK_EVERY:
            return state
    fetch()
    latest = fetched_version()
    state["checked"] = now()
    state["available"] = latest if newer(latest, installed_version()) else None
    save_state(state)
    return state


def status(cfg: dict) -> dict:
    """What the bar icon shows. Reads the state only: never touches the network."""
    state = load_state()
    current = installed_version()
    available = state.get("available")
    waiting = available if newer(available, current) and managed() and checks_enabled(cfg) else None
    show = bool(waiting) and state.get("dismissed") != waiting
    return {
        "icon": show,
        "tooltip": f"omaprofile {waiting} available" if show else "",
        "installed": current,
        "available": waiting,
    }


def footer(cfg: dict) -> str:
    """The picker's footer line: shown while the bar icon would be."""
    st = status(cfg)
    return f"{st['available']} available · U to update" if st["icon"] else ""


def dismiss() -> None:
    state = load_state()
    if state.get("available"):
        state["dismissed"] = state["available"]
        save_state(state)


def icon_on_bar() -> bool:
    try:
        layout = json.loads(SHELL_CONFIG.read_text())["bar"]["layout"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    entries = [e for section in layout.values() if isinstance(section, list) for e in section]
    return any((e.get("id") if isinstance(e, dict) else e) == PLUGIN_ID for e in entries)


def notify_with_buttons(summary: str, body: str, buttons: list[tuple[str, str]]) -> str:
    """A notification with buttons; returns the clicked button's name ('' when closed)."""
    if not shutil.which("notify-send"):
        return ""
    argv = ["notify-send", "-a", "omaprofile", "-i", "chromium"]
    for name, label in buttons:
        argv += ["-A", f"{name}={label}"]
    proc = subprocess.run([*argv, summary, body], capture_output=True, text=True)
    return proc.stdout.strip()


def scheduled_check(cfg: dict) -> None:
    """The hourly call from the overlay and the bar icon. Quiet: an offline or
    failed check waits for the next one."""
    if not cfgmod.STATE_FILE.exists():  # not set up (or declined): nothing to announce
        return
    # The overlay and each bar (one per monitor) ask; one at a time, so a
    # version is announced once.
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        try:
            state = check(cfg, scheduled=True)
        except Exception:  # noqa: BLE001 - offline, a broken repository: try again later
            return
        waiting = status(cfg)["available"]
        if not waiting or state.get("notified") == waiting or icon_on_bar():
            return
        state["notified"] = waiting
        save_state(state)
    choice = notify_with_buttons(f"omaprofile {waiting} is available",
                                 "Update to see what's new and install it.",
                                 [("update", "Update"), ("later", "Not now")])
    if choice == "update":
        open_updater()


# ------------------------------------------------------------------ updating

def open_updater() -> None:
    """Run `omaprofile update` in Omarchy's floating terminal. Replaces this process."""
    detached([FLOATING_TERMINAL, f"{shlex.quote(str(LAUNCHER))} update"])


def run(cfg: dict, stdin=sys.stdin) -> int:
    """`omaprofile update`: show what's new, ask, then update through Omarchy."""
    current = installed_version()
    if not managed():
        print(f"omaprofile {current} runs from the checkout at {SRC_ROOT}, not from a plugin "
              f"Omarchy installed.\nUpdate it with: cd {shlex.quote(str(SRC_ROOT))} && git pull")
        return 0
    try:
        state = check(cfg)
    except UpdateError as e:
        print(f"omaprofile: {e}", file=sys.stderr)
        return 1
    latest = state.get("available")
    if not newer(latest, current):
        print(f"omaprofile {current} is up to date")
        return 0

    print(f"omaprofile {current} → {latest}\n")
    for version, lines in changes(fetched_file("CHANGELOG.md"), current, latest):
        print(version)
        for line in lines:
            print(f"  • {line}")
        print()
    print("Update? [Y/n] ", end="", flush=True)
    answer = stdin.readline().strip().lower()
    if answer not in ("", "y", "yes"):
        print("Not updated.")
        return 0

    proc = subprocess.run(["omarchy", "plugin", "update", PLUGIN_ID, "--yes"], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(f"Update failed: {detail[-1] if detail else f'exit {proc.returncode}'}", file=sys.stderr)
        print(f"Still on {current}.", file=sys.stderr)
        return 1
    updated = installed_version()
    state = load_state()
    state["installed"] = updated  # said here; the reload need not say it again
    save_state(state)
    print(f"Updated to {updated}")
    return 0


# ------------------------------------------------------------------ after an update

def put_icon_on_bar() -> None:
    """Place the icon on the right of the bar, unless the user has it somewhere already."""
    subprocess.run(["omarchy-shell", "-q", "shell", "putBarWidget", PLUGIN_ID, json.dumps({"section": "right"})],
                   capture_output=True)


def record_install() -> None:
    """A fresh setup: Omarchy placed the icon when it added the plugin; nothing to announce."""
    state = load_state()
    state["installed"] = installed_version()
    state["iconOffered"] = True
    save_state(state)


def after_reload() -> None:
    """The overlay loaded a version that has not run before: say what's new, once.

    An install from before update notices has no record yet; it gets the bar
    icon now, the one time it is offered.
    """
    state = load_state()
    current = installed_version()
    if state.get("installed") == current:
        return
    first = not state.get("iconOffered")
    if first and managed():
        put_icon_on_bar()
        state["iconOffered"] = True
    state["installed"] = current
    save_state(state)
    try:
        news = changes((SRC_ROOT / "CHANGELOG.md").read_text(), "0", current)
    except OSError:
        news = []
    lines = news[0][1][:3] if news and news[0][0] == current else []
    body = " ".join(lines)
    if first:
        body += (" " if body else "") + "An icon appears in the bar when there's an update."
    notify(f"omaprofile {current} installed", body)
