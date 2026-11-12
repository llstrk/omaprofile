"""Config file and well-known paths."""

import json
import os
from pathlib import Path

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state"))

CONFIG_FILE = CONFIG_HOME / "omaprofile" / "config.json"
PLUGIN_DIR = CONFIG_HOME / "omarchy" / "plugins" / "dry.omaprofile"
DESKTOP_ID = "omaprofile.desktop"
DESKTOP_FILE = DATA_HOME / "applications" / DESKTOP_ID
BIN_LINK = HOME / ".local" / "bin" / "omaprofile"
HYPR_DIR = CONFIG_HOME / "hypr"
STATE_FILE = STATE_HOME / "omaprofile" / "install.json"  # handlers to restore on uninstall

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
        cfg = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: expected a JSON object at the top level")
    for key in ("browser", "contexts"):
        if not isinstance(cfg.get(key, {}), dict):
            raise ConfigError(f"{path}: '{key}' must be an object")
    return cfg


def write_default(path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
