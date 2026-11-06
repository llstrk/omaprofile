"""Summon the dry.omaprofile overlay and wait for its answer.

Round trip is file based, like omarchy.image-picker: the summon payload names
a selection file and a done file; the overlay writes the chosen id to the
first and touches the second (only the second on cancel).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass

from . import PLUGIN_ID


class PickerError(Exception):
    pass


@dataclass
class Choice:
    id: str
    label: str = ""
    color: str = ""
    glyph: str = ""
    detail: str = ""
    command: str = ""  # used by contexts; the overlay ignores it

    def __post_init__(self):
        self.label = self.label or self.id

    @classmethod
    def from_spec(cls, spec: str) -> "Choice":
        """CLI form ``id|label|color|glyph|detail``."""
        return cls(*(spec.split("|") + [""] * 4)[:5])


def _ipc(*args: str) -> str:
    try:
        proc = subprocess.run(["omarchy-shell", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return "omarchy-shell not found"
    return (proc.stdout + proc.stderr).strip()


def pick(title: str, subtitle: str, choices: list[Choice], timeout: float = 600) -> str | None:
    """Show the overlay; return the chosen id, or None when cancelled."""
    sel_fd, sel_path = tempfile.mkstemp(prefix="omaprofile.sel.")
    os.close(sel_fd)
    done_path = sel_path + ".done"
    try:
        payload = json.dumps({
            "title": title,
            "subtitle": subtitle,
            "choices": [asdict(c) for c in choices],
            "selectionFile": sel_path,
            "doneFile": done_path,
        })
        reply = _ipc("shell", "summon", PLUGIN_ID, payload)
        if reply != "ok":
            raise PickerError(f"picker unavailable ({reply}); is the {PLUGIN_ID} plugin enabled?")

        deadline = time.monotonic() + timeout
        while not os.path.exists(done_path):
            if time.monotonic() > deadline:
                _ipc("-q", "shell", "hide", PLUGIN_ID)
                raise PickerError("pick timed out")
            time.sleep(0.03)
        return open(sel_path).read().strip() or None
    finally:
        for path in (sel_path, done_path):
            _unlink(path)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def detached(argv: list[str]) -> None:
    """Replace this process with argv in its own session, silenced, under uwsm when present."""
    if shutil.which("uwsm-app"):
        argv = ["uwsm-app", "--", *argv]
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.execvp("setsid", ["setsid", *argv])
