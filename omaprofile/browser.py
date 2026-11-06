"""The built-in ``browser`` group: Chromium profiles, rules, launching, default-browser registration."""

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config as cfgmod
from .picker import Choice, detached, pick

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


class BrowserError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    directory: str  # "Default", "Profile 1", ...
    name: str       # display name
    color: str      # "#rrggbb" or ""


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
        section = cfg.get("browser", {})
        self.command = section.get("command", "chromium")
        self.rules = section.get("rules", [])
        self.config_dir = Path(section.get("configDir", cfgmod.CONFIG_HOME / "chromium")).expanduser()

    def profiles(self) -> list[Profile]:
        state = self.config_dir / "Local State"
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
            if fnmatch.fnmatchcase(host, rule.get("host", "")) or fnmatch.fnmatchcase(url, rule.get("url", "")):
                return self.resolve(rule["profile"])
        return None

    def choose_profile(self, url: str, title: str) -> Profile | None:
        """Rule match, single profile, or the picker. None means cancelled."""
        profile = self.rule_match(url) if url else None
        if profile:
            return profile
        profiles = self.profiles()
        if len(profiles) == 1:
            return profiles[0]
        chosen = pick(title, url_host(url) or "New window",
                      [Choice(p.directory, p.name, p.color, detail=p.directory) for p in profiles])
        return self.resolve(chosen) if chosen else None

    def launch(self, profile: Profile, args: list[str]) -> None:
        if not shutil.which(self.command):
            raise BrowserError(f"browser '{self.command}' not found")
        detached([self.command, f"--profile-directory={profile.directory}", *args])

    def open(self, args: list[str]) -> None:
        """Default-browser entry point: [flags...] [url]."""
        flags, url = split_args(args)
        title = "Open incognito as" if "--incognito" in flags else "Open with profile"
        profile = self.choose_profile(url, title)
        if profile:  # None: cancelled, drop the link
            self.launch(profile, flags + ([url] if url else []))

    def open_webapp(self, url: str, flags: list[str]) -> None:
        """Omarchy web app: Chromium's --app mode in the chosen profile."""
        profile = self.choose_profile(url, "Open web app as")
        if profile:
            self.launch(profile, [f"--app={url}", *flags])


def set_default_browser(desktop_id: str) -> None:
    subprocess.run(["xdg-settings", "set", "default-web-browser", desktop_id], capture_output=True)
    for mime in MIME_TYPES:
        subprocess.run(["xdg-mime", "default", desktop_id, mime], capture_output=True)


def default_browser() -> str:
    return subprocess.run(["xdg-settings", "get", "default-web-browser"],
                          capture_output=True, text=True).stdout.strip()


def register(exe: Path) -> None:
    cfgmod.DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.DESKTOP_FILE.write_text(DESKTOP_ENTRY.format(exe=exe, mime=";".join(MIME_TYPES)))
    subprocess.run(["update-desktop-database", str(cfgmod.DESKTOP_FILE.parent)], capture_output=True)
    set_default_browser(cfgmod.DESKTOP_ID)


def unregister(fallback: str = "chromium.desktop") -> None:
    set_default_browser(fallback)
    cfgmod.DESKTOP_FILE.unlink(missing_ok=True)
    subprocess.run(["update-desktop-database", str(cfgmod.DESKTOP_FILE.parent)], capture_output=True)
