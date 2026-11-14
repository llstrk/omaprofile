"""The throwaway home every test runs in. Import it before omaprofile.

omaprofile's paths are computed from these variables at import time, so a test
that forgets to redirect one still lands here, never in the real ~/.config,
~/.local or the running desktop. Shared by every test module: the first import
sets it up, and it is removed when the test process exits.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="omaprofile-test-home."))
os.environ.update({
    "HOME": str(SANDBOX),
    "XDG_CONFIG_HOME": str(SANDBOX / ".config"),
    "XDG_DATA_HOME": str(SANDBOX / ".local" / "share"),
    "XDG_STATE_HOME": str(SANDBOX / ".local" / "state"),
    "XDG_CACHE_HOME": str(SANDBOX / ".cache"),
    "XDG_RUNTIME_DIR": str(SANDBOX / "run"),  # no omarchy-shell IPC socket here
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(SANDBOX / "no-bus"),
})
for name in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY"):  # no hyprctl reload of the real session
    os.environ.pop(name, None)
(SANDBOX / "run").mkdir(mode=0o700)
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)


def inside(path: Path) -> bool:
    path = Path(path)
    return path == SANDBOX or SANDBOX in path.parents


def wipe() -> None:
    """Empty the home between feature scenarios (keeps the runtime dir)."""
    for child in SANDBOX.iterdir():
        if child.name == "run":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
