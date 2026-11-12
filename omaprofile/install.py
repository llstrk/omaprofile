"""Deploy the shell plugin, link the CLI, register as default browser, hook web apps.

Two ways in. `omarchy plugin add <repo>` clones the whole repository into the
plugins folder, and the overlay runs ``install --auto`` from there when it
loads: until install has run it asks for it in a popup (Omarchy has no install
hook of its own), after that it refreshes the files install wrote. Plugin files stay Omarchy's to
manage and update. Run from any other checkout, install copies the plugin files
into the plugins folder.

``omarchy plugin remove`` without ``uninstall`` first is caught too: as the
overlay unloads it watches its folder, and when the folder goes away it runs
``uninstall --removed`` from a copy of the CLI kept outside the plugin folder,
which asks in a floating terminal whether to undo omaprofile's changes. The
launcher in ~/.local/bin does the same if it finds the plugin gone.

``uninstall`` undoes every step: the previous link handlers come back, the web
app launchers and hyprland.lua are restored, the CLI link goes away and the
plugin is disabled (and removed, unless Omarchy manages it). Only the config
file is kept.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import PLUGIN_ID
from . import config as cfgmod
from .browser import Browser, current_handlers, desktop_exec_arg, register, unregister

SRC_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = SRC_ROOT / "bin" / "omaprofile"
PLUGIN_FILES = ("manifest.json", "plugin/Omaprofile.qml")  # relative to the repository root
OLD_PLUGIN_FILES = ("Omaprofile.qml",)  # flat layout copied by 0.2.0 and earlier
HYPR_HOOK = cfgmod.HYPR_DIR / "omaprofile.lua"
HYPRLAND_LUA = cfgmod.HYPR_DIR / "hyprland.lua"
# "Don't ask again" in the setup popup; install and uninstall clear it.
DECLINED_FILE = cfgmod.STATE_FILE.parent / "setup-declined"
# A copy of the CLI outside the plugin folder, to uninstall with once that is gone.
CLEANUP_DIR = cfgmod.DATA_HOME / "omaprofile" / "cleanup"
CLEANUP_LOCK = cfgmod.STATE_FILE.parent / "cleanup.lock"

OMARCHY_WEBAPP_EXEC = "Exec=omarchy-launch-webapp "
# Any omaprofile launcher, including one from a checkout that has since moved.
OMAPROFILE_WEBAPP_EXEC_RE = re.compile(r'^Exec=(?:"[^"\n]*omaprofile"|\S*omaprofile) webapp open ', re.M)

REQUIRE_LINE = 'require("hypr.omaprofile")'
REQUIRE_BLOCK = (
    "-- omaprofile: route web app bindings through the profile picker. Must come\n"
    "-- before the defaults so their web app bindings pick up the override.\n"
    f"{REQUIRE_LINE}\n"
    "\n"
)
DEFAULTS_LINE = re.compile(r'^\s*require\(\s*["\']default\.hypr\.omarchy["\']\s*\)', re.M)


SHIM_MARK = "# omaprofile launcher, written by `omaprofile install`."
SHIM = SHIM_MARK + """
# Runs the omaprofile below. If that is gone (the plugin was removed before
# `omaprofile uninstall`), links still open in the browser, and the copy kept
# for this asks whether to undo omaprofile's changes; without that copy a
# notification offers the command that cleans up.
target={target}
cleanup={cleanup}
browser={browser}
fix={fix}
if [ -x "$target" ]; then
  exec "$target" "$@"
fi
(
  if [ -x "$cleanup" ]; then
    exec "$cleanup" uninstall --removed
  elif [ -n "$fix" ]; then
    choice=$(notify-send -a omaprofile -i chromium -A copy="Copy fix command" \\
      "omaprofile was removed without uninstall" "Links open in $browser directly until it is cleaned up.")
    [ "$choice" = copy ] && printf '%s' "$fix" | wl-copy
  else
    notify-send -a omaprofile -i chromium "omaprofile is missing" \\
      "Links open in $browser directly. Restore your omaprofile checkout, then run omaprofile uninstall."
  fi
) >/dev/null 2>&1 &
case "$1" in
  webapp) shift; [ "$1" = open ] && shift; url=$1; shift; exec "$browser" --app="$url" "$@" ;;
  browser) shift; [ "$1" = open ] && shift ;;
esac
for a; do shift; [ "$a" = --private ] && a=--incognito; set -- "$@" "$a"; done
exec "$browser" "$@"
"""


class InstallError(Exception):
    pass


def repo_url() -> str:
    proc = subprocess.run(["git", "-C", str(SRC_ROOT), "remote", "get-url", "origin"],
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def shim_text() -> str:
    """~/.local/bin/omaprofile: a script that runs this checkout, with a fallback if it disappears."""
    url = repo_url()
    fix = ""
    if managed_by_omarchy() and url:
        home = str(cfgmod.HOME)
        cli = str(LAUNCHER)
        cli = "~" + cli[len(home):] if cli.startswith(home + "/") else shlex.quote(cli)
        fix = (f"omarchy plugin add {shlex.quote(url)} && {cli} uninstall"
               f" && omarchy plugin remove {PLUGIN_ID}")
    return "#!/bin/sh\n" + SHIM.format(target=shlex.quote(str(LAUNCHER)),
                                       cleanup=shlex.quote(str(CLEANUP_DIR / "bin" / "omaprofile")),
                                       browser=shlex.quote(Browser(cfgmod.load()).command),
                                       fix=shlex.quote(fix))


def write_shim() -> None:
    cfgmod.BIN_LINK.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.BIN_LINK.unlink(missing_ok=True)  # also replaces the symlink older versions made
    cfgmod.BIN_LINK.write_text(shim_text())
    cfgmod.BIN_LINK.chmod(0o755)


def shim_target() -> Path | None:
    """The checkout ~/.local/bin/omaprofile runs, if it is ours (the script, or an older symlink)."""
    link = cfgmod.BIN_LINK
    if link.is_symlink():
        return link.resolve()
    try:
        text = link.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    if SHIM_MARK not in text:
        return None
    for line in text.splitlines():
        if line.startswith("target="):
            try:
                return Path(shlex.split(line[len("target="):])[0])
            except (ValueError, IndexError):
                return None
    return None


def remove_shim() -> None:
    """Remove ~/.local/bin/omaprofile if it is ours (the script, or an older version's symlink)."""
    link = cfgmod.BIN_LINK
    if link.is_symlink():
        if link.resolve() == LAUNCHER.resolve():
            link.unlink()
    elif shim_target() is not None:
        link.unlink()


def cleanup_sources() -> list[Path]:
    return [SRC_ROOT / "bin" / "omaprofile", *sorted((SRC_ROOT / "omaprofile").glob("*.py"))]


def write_cleanup_copy() -> bool:
    """Keep the CLI copy that uninstalls after the plugin folder is gone. True if it changed."""
    if SRC_ROOT.resolve() == CLEANUP_DIR.resolve():
        return False
    changed = False
    for src in cleanup_sources():
        dst = CLEANUP_DIR / src.relative_to(SRC_ROOT)
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        shutil.copymode(src, dst)
        changed = True
    return changed


def remove_cleanup_copy() -> None:
    shutil.rmtree(CLEANUP_DIR, ignore_errors=True)
    try:
        CLEANUP_DIR.parent.rmdir()
    except OSError:
        pass


def plugin_was_removed() -> bool:
    """Installed, but the plugin folder is gone: `omarchy plugin remove` came before uninstall."""
    return cfgmod.STATE_FILE.exists() and not (cfgmod.PLUGIN_DIR / "manifest.json").exists()


def managed_by_omarchy() -> bool:
    """This checkout is the plugin folder itself (cloned by `omarchy plugin add`)."""
    try:
        return SRC_ROOT.resolve() == cfgmod.PLUGIN_DIR.resolve()
    except OSError:
        return False


def copy_plugin() -> None:
    """Copy the overlay into the plugins folder, unless that folder is someone else's clone."""
    if (cfgmod.PLUGIN_DIR / ".git").exists():
        raise InstallError(f"{cfgmod.PLUGIN_DIR} is a git clone (omarchy plugin add); run install from it: "
                           f"{cfgmod.PLUGIN_DIR / 'bin' / 'omaprofile'} install")
    for name in PLUGIN_FILES:
        target = cfgmod.PLUGIN_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC_ROOT / name, target)
    for name in OLD_PLUGIN_FILES:
        (cfgmod.PLUGIN_DIR / name).unlink(missing_ok=True)


def remove_plugin_copy() -> None:
    for name in (*PLUGIN_FILES, *OLD_PLUGIN_FILES):
        (cfgmod.PLUGIN_DIR / name).unlink(missing_ok=True)
    for folder in (cfgmod.PLUGIN_DIR / "plugin", cfgmod.PLUGIN_DIR):
        try:
            folder.rmdir()
        except OSError:
            pass  # holds files that are not ours


def omaprofile_webapp_exec() -> str:
    return f"Exec={desktop_exec_arg(cfgmod.BIN_LINK)} webapp open "


def rewrite_webapps(to_omaprofile: bool) -> list[Path]:
    """Point Omarchy web app launchers (Exec lines) at omaprofile, or back at Omarchy."""
    changed = []
    new = omaprofile_webapp_exec() if to_omaprofile else OMARCHY_WEBAPP_EXEC
    for desktop in sorted(cfgmod.DESKTOP_FILE.parent.glob("*.desktop")):
        if desktop.name == cfgmod.DESKTOP_ID:
            continue
        text = desktop.read_text()
        updated = OMAPROFILE_WEBAPP_EXEC_RE.sub(new, text)
        if to_omaprofile:
            updated = updated.replace(OMARCHY_WEBAPP_EXEC, new)
        if updated != text:
            desktop.write_text(updated)
            changed.append(desktop)
    return changed


# ------------------------------------------------------------ hyprland.lua

def add_hypr_require(path: Path | None = None) -> str:
    """Load the hook before Omarchy's defaults. Returns 'added', 'present' or 'manual'."""
    path = path or HYPRLAND_LUA
    try:
        lines = path.read_text().splitlines(keepends=True)
    except OSError:
        return "manual"
    if any(line.strip() == REQUIRE_LINE for line in lines):
        return "present"
    at = next((i for i, line in enumerate(lines) if DEFAULTS_LINE.match(line)), None)
    if at is None:
        return "manual"
    while at > 0 and lines[at - 1].lstrip().startswith("--"):
        at -= 1  # keep the comment above the defaults line attached to it
    lines.insert(at, REQUIRE_BLOCK)
    path.write_text("".join(lines))
    return "added"


def remove_hypr_require(path: Path | None = None) -> bool:
    """Drop the require line, the '-- omaprofile:' comment above it and one blank line after."""
    path = path or HYPRLAND_LUA
    try:
        lines = path.read_text().splitlines(keepends=True)
    except OSError:
        return False
    out, removed, skip_blank = [], False, False
    for line in lines:
        if line.strip() == REQUIRE_LINE:
            comment = len(out)
            while comment > 0 and out[comment - 1].lstrip().startswith("--"):
                comment -= 1
            ours = next((i for i in range(comment, len(out)) if out[i].lstrip().startswith("-- omaprofile:")), None)
            if ours is not None:
                del out[ours:]
            removed = skip_blank = True
            continue
        if skip_blank:
            skip_blank = False
            if not line.strip():
                continue
        out.append(line)
    if removed:
        path.write_text("".join(out))
    return removed


# ------------------------------------------------------------ state

def save_handlers() -> None:
    """Remember the link handlers omaprofile replaces; a reinstall keeps the first record."""
    if cfgmod.STATE_FILE.exists():
        return
    cfgmod.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.STATE_FILE.write_text(json.dumps(current_handlers(), indent=2) + "\n")


def load_handlers() -> dict:
    try:
        return json.loads(cfgmod.STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------ install / uninstall

def install(enable: bool = True) -> tuple[str, str]:
    """How hyprland.lua was handled ('added', 'present' or 'manual') and the plugin enable error, if any.

    ``enable=False`` is for the overlay's own call: it is loaded already.
    """
    missing = [f for f in ("bin/omaprofile", *PLUGIN_FILES, "hypr/omaprofile.lua")
               if not (SRC_ROOT / f).is_file()]
    if missing:
        raise InstallError(f"{SRC_ROOT} is not an omaprofile checkout (missing {', '.join(missing)}); "
                           "clone the repository and run bin/omaprofile install from it")
    for tool in ("omarchy-shell", "omarchy"):
        if not shutil.which(tool):
            raise InstallError(f"'{tool}' not found; omaprofile needs Omarchy with shell plugins")

    if not cfgmod.CONFIG_FILE.exists():
        cfgmod.write_default()

    write_shim()
    write_cleanup_copy()
    if not managed_by_omarchy():
        copy_plugin()

    save_handlers()
    # Desktop entries run the ~/.local/bin link, not the checkout: xdg-mime and
    # `omarchy launch browser` split Exec on the first space and ignore quoting,
    # so a checkout path with a space in it would make the entry invisible.
    register(cfgmod.BIN_LINK)
    rewrite_webapps(to_omaprofile=True)
    # Hook file before the require line that loads it.
    HYPR_HOOK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC_ROOT / "hypr" / "omaprofile.lua", HYPR_HOOK)
    hook = add_hypr_require()
    reload_hyprland()
    DECLINED_FILE.unlink(missing_ok=True)
    # Last: the overlay asks for install when it loads and finds no record.
    plugin_error = enable_plugin() if enable else ""
    return hook, plugin_error


def setup_state() -> str:
    """What the overlay should do when it loads, run from the folder Omarchy cloned.

    'ask' when install has not run (and the user did not say never), else ''
    after refreshing files an update changed. The install record is the
    switch: present means installed already, so a default browser the user
    picked since is left alone, and uninstall (which deletes it) disables the
    overlay first.
    """
    if not managed_by_omarchy():
        return ""
    if cfgmod.STATE_FILE.exists():
        refresh()
        return ""
    return "" if DECLINED_FILE.exists() else "ask"


def decline_setup() -> None:
    DECLINED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DECLINED_FILE.touch()


def refresh() -> bool:
    """Bring the launcher script and the Hyprland hook up to date with this checkout.

    Only files that are already ours and point here: another checkout's
    launcher, or one that is gone, is left as it is.
    """
    changed = write_cleanup_copy() if cfgmod.STATE_FILE.exists() else False
    target = shim_target()
    if target is not None and target.resolve() == LAUNCHER.resolve():
        text = shim_text()
        if cfgmod.BIN_LINK.is_symlink() or cfgmod.BIN_LINK.read_text() != text:
            write_shim()
            changed = True
    source = SRC_ROOT / "hypr" / "omaprofile.lua"
    if HYPR_HOOK.is_file() and HYPR_HOOK.read_bytes() != source.read_bytes():
        shutil.copyfile(source, HYPR_HOOK)
        reload_hyprland()
        changed = True
    return changed


def reload_hyprland() -> None:
    """Rebuild the keybindings so the web app ones pick up (or drop) the hook now."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        subprocess.run(["hyprctl", "reload"], capture_output=True)


def enable_plugin(attempts: int = 40, wait: float = 0.25) -> str:
    """Rescan and enable the overlay; '' on success, else the last error.

    The rescan is asynchronous: until the shell has seen the new folder,
    enable answers that the plugin is unknown, so keep trying for a while.
    """
    subprocess.run(["omarchy-shell", "-q", "shell", "rescanPlugins"], capture_output=True)
    error = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(wait)
        proc = subprocess.run(["omarchy", "plugin", "enable", PLUGIN_ID], capture_output=True, text=True)
        if proc.returncode == 0:
            return ""
        error = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
    return error


def uninstall(fallback: str = "chromium.desktop", removed: bool = False) -> bool:
    """Returns True when the plugin folder is Omarchy's to remove (omarchy plugin remove).

    ``removed``: the plugin folder is gone already and this runs from the
    cleanup copy, so there is no plugin to disable or delete.
    """
    # First, so the overlay is gone before the install record: an overlay that
    # loads without one asks to install again.
    if not removed:
        subprocess.run(["omarchy", "plugin", "disable", PLUGIN_ID], capture_output=True)
    unregister(load_handlers(), fallback)
    cfgmod.STATE_FILE.unlink(missing_ok=True)
    DECLINED_FILE.unlink(missing_ok=True)
    rewrite_webapps(to_omaprofile=False)
    # require line first, then the file it loads.
    remove_hypr_require()
    HYPR_HOOK.unlink(missing_ok=True)
    reload_hyprland()

    managed = managed_by_omarchy()
    if not managed and not removed:
        remove_plugin_copy()
        subprocess.run(["omarchy-shell", "-q", "shell", "rescanPlugins"], capture_output=True)

    remove_shim()
    return managed
