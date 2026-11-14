"""The built-in ``browser`` group: Chromium profiles, rules, launching, default-browser registration."""

import fnmatch
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config as cfgmod
from . import update
from .picker import Choice, detached, notify, pick

MIME_TYPES = [
    "text/html",
    "application/xhtml+xml",
    "x-scheme-handler/http",
    "x-scheme-handler/https",
    "x-scheme-handler/about",
    "x-scheme-handler/unknown",
]

DESKTOP_ENTRY = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Omaprofile
GenericName=Web Browser
Comment=Pick a browser profile, then open the link
Exec={exe} browser open %U
Icon=chromium
Terminal=false
StartupNotify=false
Categories=Network;WebBrowser;
MimeType={mime};
Actions=new-window;new-private-window;

[Desktop Action new-window]
Name=New Window
Exec={exe} browser open

[Desktop Action new-private-window]
Name=New Incognito Window
Exec={exe} browser open --incognito
"""


MANAGE_EXTENSIONS = "omaprofile:extensions"  # picker row id; cannot clash with a profile dir


class BrowserError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    directory: str  # "Default", "Profile 1", ...
    name: str       # display name
    color: str      # "#rrggbb" or ""


# The browser has never run, so it has no profiles yet: launch it without
# --profile-directory and let it create its first one.
NO_PROFILE = Profile("", "", "")


def color_from_seed(seed) -> str:
    """Chromium stores profile colors as signed 32-bit ARGB ints."""
    return "" if seed is None else "#%06x" % ((seed + 2**32) % 2**24)


def url_host(url: str) -> str:
    if "://" not in url:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):  # about:blank, mailto:, file:
            return ""
        url = "//" + url
    return (urlsplit(url).hostname or "").lower()


def looks_like_url(arg: str) -> bool:
    return "://" in arg or "." in arg or arg.startswith(("localhost", "about:", "file:"))


def split_args(args: list[str]) -> tuple[list[str], str]:
    """Browser flags and the URL; ``--private`` (from omarchy) becomes ``--incognito``."""
    flags, url = [], ""
    for a in args:
        if a.startswith("--") and not url:
            flags.append("--incognito" if a == "--private" else a)
        else:
            url = a
    return flags, url


class Browser:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        section = cfg.get("browser", {})
        self.command = section.get("command", "chromium")
        self.rules = check_rules(section.get("rules", []))
        self.config_dir = Path(section.get("configDir", cfgmod.CONFIG_HOME / "chromium")).expanduser()

    def profiles(self) -> list[Profile]:
        """Profiles from the browser's Local State; none when the browser has never run."""
        state = self.config_dir / "Local State"
        if not state.exists():
            return []
        try:
            cache = json.loads(state.read_text())["profile"]["info_cache"]
        except (OSError, ValueError, KeyError) as e:
            raise BrowserError(f"cannot read profiles from {state}: {e}")
        profiles = [Profile(key, v.get("name", key), color_from_seed(v.get("profile_color_seed")))
                    for key, v in cache.items()]
        profiles.sort(key=lambda p: "" if p.directory == "Default" else p.directory)
        return profiles

    def resolve(self, wanted: str) -> Profile:
        for p in self.profiles():
            if wanted in (p.directory, p.name):
                return p
        raise BrowserError(f"unknown profile '{wanted}'")

    def rule_match(self, url: str) -> Profile | None:
        """First rule whose host/url glob matches, else None."""
        host = url_host(url)
        for rule in self.rules:
            # A rule only tests the keys it sets: an unset "host" must not match
            # the empty host of about:blank or file: URLs.
            if (("host" in rule and host and fnmatch.fnmatchcase(host, rule["host"].lower()))
                    or ("url" in rule and fnmatch.fnmatchcase(url, rule["url"]))):
                return self.resolve(rule["profile"])
        return None

    def choose_profile(self, url: str, title: str, extras: Callable[[], list[Choice]] | None = None
                       ) -> Profile | str | None:
        """Rule match, single profile, or the picker. None means cancelled.

        `extras` builds rows shown after the profiles; picking one returns its id.
        While the picker is open, rows are rebuilt whenever Chromium writes its
        profile or extension state, which it does seconds to minutes after a
        change, so a new profile's name appears without reopening.
        """
        profiles = self.profiles()
        if not profiles:
            return NO_PROFILE
        if url:
            try:
                profile = self.rule_match(url)
            except BrowserError as e:  # rule names a profile that is gone: ask instead of dropping the link
                notify("omaprofile: rule ignored", str(e))
                profile = None
            if profile:
                return profile
        if len(profiles) == 1:
            return profiles[0]

        def rows() -> list[Choice]:
            return ([Choice(p.directory, p.name, p.color, detail=p.directory) for p in self.profiles()]
                    + (extras() if extras else []))

        from .extensions import signature  # extensions imports this module
        last = [signature(self.config_dir), rows()]

        def refresh() -> list[Choice] | None:
            sig = signature(self.config_dir)
            if sig == last[0]:
                return None
            try:
                new = rows()
            except BrowserError:  # Local State caught mid-write; next poll retries
                return None
            changed = new != last[1]
            last[:] = [sig, new]
            return new if changed else None

        footer = update.footer(self.cfg)  # an update waiting: a line under the rows, U opens the updater
        extra = {"footer": footer, "footer_command": [str(update.LAUNCHER), "update", "--open"]} if footer else {}
        chosen = pick(title, url_host(url) or "New window", last[1], refresh=refresh, **extra)
        if chosen and not any(chosen == p.directory for p in self.profiles()) and extras:
            return chosen  # an extras row
        return self.resolve(chosen) if chosen else None

    def launch(self, profile: Profile, args: list[str]) -> None:
        if not shutil.which(self.command):
            raise BrowserError(f"browser '{self.command}' not found")
        profile_flag = [f"--profile-directory={profile.directory}"] if profile.directory else []
        detached([self.command, *profile_flag, *args])

    def open(self, args: list[str]) -> None:
        """Default-browser entry point: [flags...] [url]."""
        flags, url = split_args(args)
        title = "Open incognito as" if "--incognito" in flags else "Open with profile"
        while True:
            profile = self.choose_profile(url, title, lambda: [self.manage_extensions_choice()])
            if profile != MANAGE_EXTENSIONS:
                break
            self.manage_extensions()
            if not url:  # a new window was all that was asked; managing replaced it
                return
        if profile:  # None: cancelled, drop the link
            self.launch(profile, flags + ([url] if url else []))

    def manage_extensions_choice(self) -> Choice:
        from .extensions import Extensions  # extensions imports this module
        try:
            missing = Extensions(self.cfg).missing_count()
            detail = f"{missing} missing in other profiles" if missing else "all profiles in sync"
        except (BrowserError, OSError):
            detail = "sync from the first profile"
        return Choice(MANAGE_EXTENSIONS, "Manage extensions", glyph="+", detail=detail)

    def manage_extensions(self) -> None:
        from .extensions import Extensions
        plan = Extensions(self.cfg).sync(None)
        if plan:
            notify("Extensions", 'Click "Add to Chromium" on each opened tab.')
        elif plan == []:
            notify("Extensions", "Nothing to sync.")

    def open_webapp(self, url: str, flags: list[str]) -> None:
        """Omarchy web app: Chromium's --app mode in the chosen profile."""
        profile = self.choose_profile(url, "Open web app as")
        if profile:
            self.launch(profile, [f"--app={url}", *flags])


def check_rules(rules) -> list[dict]:
    """Rules from the config, rejecting ones that could never match or have no profile."""
    if not isinstance(rules, list):
        raise cfgmod.ConfigError("browser.rules must be a list")
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("profile"), str)
                or not any(isinstance(rule.get(k), str) and rule[k] for k in ("host", "url"))):
            raise cfgmod.ConfigError(f"browser rule {rule!r} needs a 'profile' and a 'host' or 'url' pattern")
    return rules


def desktop_exec_arg(path: Path | str) -> str:
    """`path` as one argument of a desktop entry Exec key (quoted when it needs to be)."""
    text = str(path)
    if not re.search(r'[\s"\'\\><~|&;$*?#()`]', text):
        return text
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", text) + '"'
    return quoted.replace("\\", "\\\\")  # the string-value escape layer on top


def desktop_mimeapps_lists() -> list[Path]:
    """The per-desktop mimeapps lists xdg-mime reads before mimeapps.list."""
    lists = []
    for desktop in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
        if desktop:
            path = cfgmod.CONFIG_HOME / f"{desktop.lower()}-mimeapps.list"
            if path not in lists:
                lists.append(path)
    return lists


def set_desktop_mimeapps_list(path: Path, desktop_id: str, only: list[str] | None = None) -> None:
    """Route the MIME types omaprofile handles in `path` through `desktop_id`.

    xdg-mime reads `$XDG_CURRENT_DESKTOP`-specific lists before mimeapps.list, so
    Omarchy's ~/.config/hyprland-mimeapps.list keeps its own http/https handler
    and the picker is never asked. Only keys this file already sets for a MIME
    type omaprofile handles change; comments and every other handler are kept.
    A missing file means the desktop sets no such list, so none is created.
    """
    if not path.is_file():
        return
    lines = path.read_text().splitlines(keepends=True)
    section = ""
    for number, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        elif section == "[Default Applications]":
            key = stripped.partition("=")[0]
            if key in (only or MIME_TYPES):
                lines[number] = f"{key}={desktop_id}\n"
    path.write_text("".join(lines))


def desktop_mimeapps_handlers(path: Path) -> dict[str, str]:
    """The handlers `path` sets for the MIME types omaprofile handles."""
    handlers, section = {}, ""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return handlers
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        elif section == "[Default Applications]":
            key, _, value = stripped.partition("=")
            if key in MIME_TYPES:
                handlers[key] = value
    return handlers


def handler_lists() -> list[Path]:
    """Every list that routes links: the desktop-specific ones, then mimeapps.list."""
    return [*desktop_mimeapps_lists(), cfgmod.CONFIG_HOME / "mimeapps.list"]


def current_handlers() -> dict:
    """What each list routes links to now, so uninstall can put exactly that back.

    Taken while omaprofile is already installed (an upgrade), its own entries are
    recorded as "" (unknown) rather than as something to restore.
    """
    def known(value: str) -> str:
        return "" if value == cfgmod.DESKTOP_ID else value

    return {
        "default": known(default_browser()),
        "lists": {str(p): {k: known(v) for k, v in desktop_mimeapps_handlers(p).items()}
                  for p in handler_lists() if p.is_file()},
    }


def set_default_browser(desktop_id: str) -> None:
    subprocess.run(["xdg-settings", "set", "default-web-browser", desktop_id], capture_output=True)
    for mime in MIME_TYPES:
        subprocess.run(["xdg-mime", "default", desktop_id, mime], capture_output=True)
    for path in desktop_mimeapps_lists():
        set_desktop_mimeapps_list(path, desktop_id)


def restore_list(path: Path, handlers: dict[str, str] | None, leftover: str) -> None:
    """Point the omaprofile lines of `path` back at `handlers`.

    `handlers` None means nothing was recorded for the file. A line recorded as
    unknown (""), or in a file with no record, gets `leftover`. A line the record
    does not have was added by omaprofile and is dropped. Lines naming any other
    handler were changed since install and are kept.
    """
    if not path.is_file():
        return
    out, section = [], ""
    for line in path.read_text().splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        elif section == "[Default Applications]":
            key, _, value = stripped.partition("=")
            if key in MIME_TYPES and value == cfgmod.DESKTOP_ID:
                if handlers is None or handlers.get(key) == "":
                    line = f"{key}={leftover}\n"
                elif handlers.get(key):
                    line = f"{key}={handlers[key]}\n"
                else:
                    continue
        out.append(line)
    path.write_text("".join(out))


OMARCHY_BROWSER = "omarchy-browser.desktop"  # Omarchy's link router for its desktop list


def restore_handlers(saved: dict, fallback: str) -> None:
    """Undo set_default_browser from the record install made.

    Where the record does not know, mimeapps.list gets the recorded default
    browser or `fallback`, and a desktop-specific list gets Omarchy's
    omarchy-browser.desktop when it is installed (it forwards to the default
    browser), else the same.
    """
    lists = saved.get("lists", saved.get("desktopLists")) or {}
    default = saved.get("default") or fallback
    if default == cfgmod.DESKTOP_ID:
        default = fallback
    router = OMARCHY_BROWSER if (cfgmod.DESKTOP_FILE.parent / OMARCHY_BROWSER).is_file() else default
    desktop_lists = desktop_mimeapps_lists()
    for path in handler_lists():
        restore_list(path, lists.get(str(path)), router if path in desktop_lists else default)


def default_browser() -> str:
    return subprocess.run(["xdg-settings", "get", "default-web-browser"],
                          capture_output=True, text=True).stdout.strip()


def register(exe: Path) -> None:
    cfgmod.DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.DESKTOP_FILE.write_text(DESKTOP_ENTRY.format(exe=desktop_exec_arg(exe), mime=";".join(MIME_TYPES)))
    subprocess.run(["update-desktop-database", str(cfgmod.DESKTOP_FILE.parent)], capture_output=True)
    set_default_browser(cfgmod.DESKTOP_ID)


def unregister(saved: dict, fallback: str = "chromium.desktop") -> None:
    restore_handlers(saved, fallback)
    cfgmod.DESKTOP_FILE.unlink(missing_ok=True)
    subprocess.run(["update-desktop-database", str(cfgmod.DESKTOP_FILE.parent)], capture_output=True)
