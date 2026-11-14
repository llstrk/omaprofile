"""Deploy the shell plugin, link the CLI, register as default browser, route web apps.

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

``uninstall`` undoes every step: the previous link handlers come back, the
chromium.desktop for web apps goes away (or back to the user's own), so does
the CLI link, and the
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
from . import update
from .browser import Browser, current_handlers, desktop_exec_arg, register, unregister

SRC_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = SRC_ROOT / "bin" / "omaprofile"
# Relative to the repository root. A QML file keeps a path only while it stays
# the same: until it restarts, the shell reuses what it compiled from a path and
# the listing of a folder it loaded from, even across `omarchy plugin update`.
# A path it has never loaded is read fresh, so a release that has to take effect
# right away moves the file (0.3.0: from plugin/ into these folders).
PLUGIN_FILES = ("manifest.json", "plugin/overlay/Omaprofile.qml", "plugin/bar/UpdateIcon.qml")
OLD_PLUGIN_FILES = ("Omaprofile.qml", "plugin/Omaprofile.qml")  # copied by 0.2.0 and earlier
# Omarchy starts every web app (launchers, keybindings, launch-or-focus) with
# omarchy-launch-webapp. For a default browser it does not know, omaprofile
# among them, that takes the browser command from chromium.desktop's Exec,
# looked up in ~/.local/share/applications first. omaprofile puts its own
# chromium.desktop there: web apps (--app=URL) go through the picker, anything
# else goes straight to Chromium.
CHROMIUM_ID = "chromium.desktop"
CHROMIUM_MARK = "# omaprofile: routes Omarchy web apps through the profile picker; `omaprofile uninstall` removes it."
CHROMIUM_BACKUP = cfgmod.STATE_FILE.parent / "chromium.desktop.orig"  # a chromium.desktop the user had
HIDDEN_CHROMIUM_ENTRY = """[Desktop Entry]
Type=Application
Name=Chromium (omaprofile)
Exec={exec_line}
NoDisplay=true
"""

# Older versions (0.2.x) routed web apps by rewriting each launcher and with a
# Hyprland hook; install and the overlay's refresh take those back out.
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
  chromium) shift ;;
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
    for folder in (cfgmod.PLUGIN_DIR / "plugin" / "overlay", cfgmod.PLUGIN_DIR / "plugin" / "bar",
                   cfgmod.PLUGIN_DIR / "plugin", cfgmod.PLUGIN_DIR):
        try:
            folder.rmdir()
        except OSError:
            pass  # holds files that are not ours


def chromium_override() -> Path:
    return cfgmod.DESKTOP_FILE.parent / CHROMIUM_ID


def system_chromium_entry() -> Path | None:
    """Chromium's own desktop entry, from the system data dirs."""
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for d in dirs.split(":"):
        path = Path(d) / "applications" / CHROMIUM_ID
        if d and path.is_file():
            return path
    return None


def chromium_override_text() -> str:
    """Chromium's entry with its main Exec running `omaprofile chromium` and the same arguments.

    The Desktop Actions (New Window, Incognito) keep starting Chromium itself.
    A chromium.desktop the user had is the base, so their flags still apply.
    """
    exe = desktop_exec_arg(cfgmod.BIN_LINK)
    base = CHROMIUM_BACKUP if CHROMIUM_BACKUP.is_file() else system_chromium_entry()
    if base is None:
        return CHROMIUM_MARK + "\n" + HIDDEN_CHROMIUM_ENTRY.format(exec_line=f"{exe} chromium %U")
    out, group, done = [CHROMIUM_MARK + "\n"], "", False
    for line in base.read_text().splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            group = stripped
        elif group == "[Desktop Entry]" and not done and line.startswith("Exec="):
            args = line[len("Exec="):].rstrip("\n").split(" ", 1)[1:]  # drop Chromium's command
            line = f"Exec={exe} chromium{' ' + args[0] if args else ''}\n"
            done = True
        out.append(line)
    return "".join(out)


def is_our_chromium_override(path: Path | None = None) -> bool:
    try:
        return (path or chromium_override()).read_text().startswith(CHROMIUM_MARK)
    except (OSError, UnicodeDecodeError):
        return False


def write_chromium_override() -> bool:
    """Write (or refresh) ~/.local/share/applications/chromium.desktop. True if it changed."""
    path = chromium_override()
    if path.exists() and not is_our_chromium_override(path) and not CHROMIUM_BACKUP.exists():
        CHROMIUM_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, CHROMIUM_BACKUP)
    text = chromium_override_text()
    if is_our_chromium_override(path) and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def remove_chromium_override() -> None:
    """Remove our chromium.desktop, putting back the one the user had."""
    path = chromium_override()
    if is_our_chromium_override(path):
        path.unlink()
    if CHROMIUM_BACKUP.is_file():
        if not path.exists():
            shutil.copy2(CHROMIUM_BACKUP, path)
        CHROMIUM_BACKUP.unlink()


def webapps_need_space_free_path() -> bool:
    """omarchy-launch-webapp takes the command up to the first space of Exec."""
    return " " in str(cfgmod.BIN_LINK)


def restore_webapps() -> list[Path]:
    """Point launchers an older version rewrote back at omarchy-launch-webapp."""
    changed = []
    for desktop in sorted(cfgmod.DESKTOP_FILE.parent.glob("*.desktop")):
        if desktop.name in (cfgmod.DESKTOP_ID, CHROMIUM_ID):
            continue
        try:
            text = desktop.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        updated = OMAPROFILE_WEBAPP_EXEC_RE.sub(OMARCHY_WEBAPP_EXEC, text)
        if updated != text:
            desktop.write_text(updated)
            changed.append(desktop)
    return changed


def remove_legacy_routing() -> bool:
    """Take out an older version's launcher rewrites and Hyprland hook. True if anything changed."""
    changed = bool(restore_webapps())
    hooked = remove_hypr_require()  # require line first, then the file it loads
    if HYPR_HOOK.exists():
        HYPR_HOOK.unlink()
        hooked = True
    if hooked:
        reload_hyprland()
    return changed or hooked


# ------------------------------------------------------------ hyprland.lua (0.2.x)

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
    """How web apps are routed ('ok', or 'manual' when they cannot be) and the plugin enable error, if any.

    ``enable=False`` is for the overlay's own call: it is loaded already.
    """
    missing = [f for f in ("bin/omaprofile", *PLUGIN_FILES)
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
    write_chromium_override()
    register(cfgmod.BIN_LINK)  # also refreshes the desktop database
    remove_legacy_routing()
    webapps = "manual" if webapps_need_space_free_path() else "ok"
    DECLINED_FILE.unlink(missing_ok=True)
    update.record_install()
    # Last: the overlay asks for install when it loads and finds no record.
    plugin_error = enable_plugin() if enable else ""
    if not plugin_error:
        keep_plugin_enabled()
    return webapps, plugin_error


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
        keep_plugin_enabled()
        update.after_reload()
        return ""
    return "" if DECLINED_FILE.exists() else "ask"


def decline_setup() -> None:
    DECLINED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DECLINED_FILE.touch()


def refresh() -> bool:
    """Bring the launcher script and the web app route up to date with this checkout.

    Only files that are already ours and point here: another checkout's
    launcher, or one that is gone, is left as it is. An install by an older
    version moves over to the chromium.desktop route.
    """
    changed = write_cleanup_copy() if cfgmod.STATE_FILE.exists() else False
    target = shim_target()
    if target is not None and target.resolve() == LAUNCHER.resolve():
        text = shim_text()
        if cfgmod.BIN_LINK.is_symlink() or cfgmod.BIN_LINK.read_text() != text:
            write_shim()
            changed = True
    if cfgmod.STATE_FILE.exists():
        if write_chromium_override():
            subprocess.run(["update-desktop-database", str(cfgmod.DESKTOP_FILE.parent)], capture_output=True)
            changed = True
        changed = remove_legacy_routing() or changed
    return changed


def reload_hyprland() -> None:
    """Rebuild the keybindings so the web app ones drop an older version's hook now."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        subprocess.run(["hyprctl", "reload"], capture_output=True)


SHELL_CONFIG = cfgmod.CONFIG_HOME / "omarchy" / "shell.json"


def _shell_plugin_records(config: dict) -> list:
    plugins = config.get("plugins")
    return plugins if isinstance(plugins, list) else []


def _is_ours(entry) -> bool:
    return (entry.get("id") if isinstance(entry, dict) else entry) == PLUGIN_ID


def _write_shell_config(config: dict) -> None:
    """Write shell.json the way the shell does, then have the shell read it again."""
    tmp = SHELL_CONFIG.with_name(SHELL_CONFIG.name + ".omaprofile.tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    shutil.copymode(SHELL_CONFIG, tmp)
    os.replace(tmp, SHELL_CONFIG)
    subprocess.run(["omarchy-shell", "-q", "shell", "reloadConfig"], capture_output=True)


def keep_plugin_enabled() -> bool:
    """Record the plugin in shell.json's plugins list, not only on the bar.

    The shell enables a plugin that also has a bar widget by its bar entry
    alone, so dragging the update icon off the bar would disable the plugin
    and unload the picker with it. A plugins record keeps it enabled.
    True when the record was added.
    """
    try:
        config = json.loads(SHELL_CONFIG.read_text())
    except (OSError, ValueError):
        return False  # no config yet (the shell writes one), or not ours to repair
    if not isinstance(config, dict) or any(_is_ours(e) for e in _shell_plugin_records(config)):
        return False
    if "plugins" in config and not isinstance(config["plugins"], list):
        return False
    config.setdefault("plugins", []).append({"id": PLUGIN_ID})
    _write_shell_config(config)
    return True


def drop_plugin_record() -> None:
    """Undo keep_plugin_enabled, so a disable really disables."""
    try:
        config = json.loads(SHELL_CONFIG.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(config, dict) or not any(_is_ours(e) for e in _shell_plugin_records(config)):
        return
    config["plugins"] = [e for e in config["plugins"] if not _is_ours(e)]
    _write_shell_config(config)


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
    drop_plugin_record()  # the disable above takes only the bar entry
    remove_chromium_override()
    unregister(load_handlers(), fallback)  # also refreshes the desktop database
    cfgmod.STATE_FILE.unlink(missing_ok=True)
    DECLINED_FILE.unlink(missing_ok=True)
    remove_legacy_routing()

    managed = managed_by_omarchy()
    if not managed and not removed:
        remove_plugin_copy()
        subprocess.run(["omarchy-shell", "-q", "shell", "rescanPlugins"], capture_output=True)

    remove_shim()
    return managed
