"""Config file and well-known paths."""

import json
import os
from pathlib import Path

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))

CONFIG_FILE = CONFIG_HOME / "omaprofile" / "config.json"
PLUGIN_DIR = CONFIG_HOME / "omarchy" / "plugins" / "dry.omaprofile"
DESKTOP_ID = "omaprofile.desktop"
DESKTOP_FILE = DATA_HOME / "applications" / DESKTOP_ID
BIN_LINK = HOME / ".local" / "bin" / "omaprofile"

DEFAULT_CONFIG = {
    "browser": {
        "command": "chromium",
        "rules": [],
    },
    "contexts": {
        "terminal": {
            "title": "Open terminal",
            "choices": [
                {"id": "shell", "label": "Shell", "color": "#cacccc", "command": "omarchy launch terminal"},
                {"id": "tmux", "label": "tmux", "color": "#1bb91f", "command": "omarchy launch terminal tmux"},
                {"id": "herdr", "label": "herdr", "color": "#f3b2be", "command": "omarchy launch terminal herdr"},
            ],
        }
    },
}


class ConfigError(Exception):
    pass


def load(path: Path = CONFIG_FILE) -> dict:
    """The config file, or the defaults when there is none."""
    if not path.exists():
        return DEFAULT_CONFIG
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e


def write_default(path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
