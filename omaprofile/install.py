"""Deploy the shell plugin, link the CLI, register as default browser."""

import shutil
import subprocess
from pathlib import Path

from . import PLUGIN_ID
from . import config as cfgmod
from .browser import register, unregister

SRC_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = SRC_ROOT / "bin" / "omaprofile"
HYPR_HOOK = cfgmod.CONFIG_HOME / "hypr" / "omaprofile.lua"

OMARCHY_WEBAPP_EXEC = "Exec=omarchy-launch-webapp "
OMAPROFILE_WEBAPP_EXEC = f"Exec={LAUNCHER} webapp open "


def rewrite_webapps(old: str, new: str) -> list[Path]:
    """Point Omarchy web app launchers (Exec lines) at `new` instead of `old`."""
    changed = []
    for desktop in cfgmod.DESKTOP_FILE.parent.glob("*.desktop"):
        text = desktop.read_text()
        if old in text:
            desktop.write_text(text.replace(old, new))
            changed.append(desktop)
    return changed


def install() -> None:
    if not cfgmod.CONFIG_FILE.exists():
        cfgmod.write_default()

    cfgmod.BIN_LINK.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.BIN_LINK.unlink(missing_ok=True)
    cfgmod.BIN_LINK.symlink_to(LAUNCHER)

    cfgmod.PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "Omaprofile.qml"):
        shutil.copyfile(SRC_ROOT / "plugin" / name, cfgmod.PLUGIN_DIR / name)
    subprocess.run(["omarchy-shell", "-q", "shell", "rescanPlugins"])
    subprocess.run(["omarchy", "plugin", "enable", PLUGIN_ID], capture_output=True)

    register(LAUNCHER)
    rewrite_webapps(OMARCHY_WEBAPP_EXEC, OMAPROFILE_WEBAPP_EXEC)
    shutil.copyfile(SRC_ROOT / "hypr" / "omaprofile.lua", HYPR_HOOK)


def uninstall(fallback: str = "chromium.desktop") -> None:
    unregister(fallback)
    rewrite_webapps(OMAPROFILE_WEBAPP_EXEC, OMARCHY_WEBAPP_EXEC)
    HYPR_HOOK.unlink(missing_ok=True)
