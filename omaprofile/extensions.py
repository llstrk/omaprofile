"""The ``extensions`` group: bring every profile up to the first profile's extensions.

Chromium signs each profile's extension settings (``protection.macs`` in
Preferences), so extensions cannot be written in from outside. Syncing opens
the Chrome Web Store page of every missing extension in the target profile;
one "Add to Chromium" click per extension finishes the job.

Only Web Store installs are synced. Extensions loaded with ``--load-extension``
(Omarchy's own), component and policy extensions reach every profile on their
own; unpacked ones have no store page.
"""

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .browser import Browser, BrowserError, Profile
from .picker import Choice, notify, pick  # noqa: F401  (notify re-exported for callers)

STORE_URL = "https://chromewebstore.google.com/detail/{}"
STORE_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
LOCATION_INTERNAL = 1  # installed by the user (Web Store or a .crx)
ALL = "all"


@dataclass(frozen=True)
class Extension:
    id: str
    name: str
    syncable: bool  # a Web Store install the user made


def _settings(profile_dir: Path) -> dict:
    """extensions.settings of a profile, from Preferences and Secure Preferences."""
    merged = {}
    for file in ("Secure Preferences", "Preferences"):
        try:
            data = json.loads((profile_dir / file).read_text())
        except (OSError, ValueError):
            continue
        merged.update(data.get("extensions", {}).get("settings", {}))
    return merged


def _localized(value: str, ext_dir: Path, default_locale: str) -> str:
    """Resolve ``__MSG_key__`` through _locales/<default_locale>/messages.json."""
    if not (value.startswith("__MSG_") and value.endswith("__")):
        return value
    key = value[6:-2].lower()
    try:
        messages = json.loads((ext_dir / "_locales" / default_locale / "messages.json").read_text())
    except (OSError, ValueError):
        return ""
    for k, v in messages.items():
        if k.lower() == key:
            return v.get("message", "")
    return ""


def _manifest(ext_dir: Path) -> dict:
    try:
        return json.loads((ext_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def _name(ext_id: str, manifest: dict, ext_dir: Path) -> str:
    name = _localized(manifest.get("name", ""), ext_dir, manifest.get("default_locale", "en"))
    return name or ext_id


def _version_dir(ext_root: Path) -> Path | None:
    """Newest ``Extensions/<id>/<version>`` holding a manifest (old ones linger until restart)."""
    try:
        dirs = [d for d in ext_root.iterdir() if (d / "manifest.json").is_file()]
    except OSError:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime, default=None)


def installed(profile_dir: Path) -> dict[str, Extension]:
    """Every extension a profile has, by id.

    Chromium can take a minute or more to write an install or uninstall to
    Preferences, but unpacks the extension into ``Extensions/<id>/`` (and
    deletes it on uninstall) right away. So user installs are read from those
    directories, and Preferences only adds what lives elsewhere (command line,
    component, unpacked) plus the from_webstore flag.
    """
    settings = _settings(profile_dir)
    found = {}
    ext_root = profile_dir / "Extensions"
    try:
        roots = [d for d in ext_root.iterdir() if d.is_dir() and d.name != "Temp"]
    except OSError:
        roots = []
    for root in roots:
        version_dir = _version_dir(root)
        if version_dir is None:
            continue
        manifest = _manifest(version_dir)
        entry = settings.get(root.name, {})
        store = manifest.get("update_url", "").startswith(STORE_UPDATE_URL) or bool(entry.get("from_webstore"))
        found[root.name] = Extension(root.name, _name(root.name, manifest, version_dir), store)
    for ext_id, entry in settings.items():
        path = Path(entry.get("path", ""))
        if ext_id in found or entry.get("location") == LOCATION_INTERNAL or not path.is_absolute():
            continue  # a user install whose directory is gone has been uninstalled
        found[ext_id] = Extension(ext_id, _name(ext_id, entry.get("manifest") or _manifest(path), path), False)
    return found


def signature(config_dir: Path) -> tuple:
    """Changes whenever a profile or extension appears, goes, or is renamed on disk."""
    paths = [config_dir / "Local State", *config_dir.glob("*/Extensions")]
    sig = []
    for path in sorted(paths):
        try:
            sig.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(sig)


class Extensions:
    def __init__(self, cfg: dict):
        self.browser = Browser(cfg)

    def profile_dir(self, profile: Profile) -> Path:
        return self.browser.config_dir / profile.directory

    def source_and_targets(self) -> tuple[Profile, list[Profile]]:
        profiles = self.browser.profiles()
        if len(profiles) < 2:
            raise BrowserError("only one profile; nothing to sync")
        return profiles[0], profiles[1:]

    def wanted(self, source: Profile) -> list[Extension]:
        exts = [e for e in installed(self.profile_dir(source)).values() if e.syncable]
        return sorted(exts, key=lambda e: e.name.lower())

    def missing(self, wanted: list[Extension], target: Profile) -> list[Extension]:
        have = installed(self.profile_dir(target))
        return [e for e in wanted if e.id not in have]

    def missing_count(self) -> int:
        """Extensions missing across all other profiles, 0 when in sync or single-profile."""
        profiles = self.browser.profiles()
        if len(profiles) < 2:
            return 0
        wanted = self.wanted(profiles[0])
        return sum(len(self.missing(wanted, p)) for p in profiles[1:])

    # ------------------------------------------------------------ actions

    def list(self) -> str:
        """Table of the first profile's syncable extensions against every other profile."""
        source, targets = self.source_and_targets()
        wanted = self.wanted(source)
        width = max([len(e.name) for e in wanted] + [len("extension")])
        cols = [source, *targets]
        lines = [f"{'extension':{width}}  " + "  ".join(f"{p.name:>8}" for p in cols)]
        have = {p.directory: installed(self.profile_dir(p)) for p in targets}
        for e in wanted:
            marks = ["source"] + ["yes" if e.id in have[p.directory] else "MISSING" for p in targets]
            lines.append(f"{e.name:{width}}  " + "  ".join(f"{m:>8}" for m in marks))
        if not wanted:
            lines.append(f"(no Web Store extensions in {source.name})")
        return "\n".join(lines)

    def plan(self, target: str | None) -> list[tuple[Profile, list[Extension]]] | None:
        """Profiles to sync with what each is missing; `target` is 'all', a profile, or None to pick.

        None means the picker was cancelled.
        """
        source, targets = self.source_and_targets()
        wanted = self.wanted(source)
        gaps = [(p, self.missing(wanted, p)) for p in targets]
        if target is None:
            target = self.choose(source, gaps)
            if target is None:
                return None
        if target == ALL:
            return [(p, m) for p, m in gaps if m]
        chosen = self.browser.resolve(target)
        if chosen.directory == source.directory:
            raise BrowserError(f"'{target}' is the source profile")
        return [(p, m) for p, m in gaps if p.directory == chosen.directory and m]

    def choose(self, source: Profile, gaps: list[tuple[Profile, list[Extension]]]) -> str | None:
        return pick("Sync extensions", f"from {source.name}", self.rows(gaps), refresh=self.refresher(source))

    @staticmethod
    def rows(gaps: list[tuple[Profile, list[Extension]]]) -> list[Choice]:
        total = sum(len(m) for _, m in gaps)
        short = sum(1 for _, m in gaps if m)
        choices = [Choice(ALL, "All profiles", glyph="*",
                          detail=f"{total} missing across {short} profile(s)" if total else "in sync")]
        for p, m in gaps:
            detail = ", ".join(e.name for e in m) if m else "in sync"
            choices.append(Choice(p.directory, p.name, p.color, detail=detail))
        return choices

    def refresher(self, source: Profile):
        """Rebuild the sync picker's rows when an install or rename lands on disk."""
        last = [signature(self.browser.config_dir)]

        def refresh() -> list[Choice] | None:
            sig = signature(self.browser.config_dir)
            if sig == last[0]:
                return None
            last[0] = sig
            try:
                _, targets = self.source_and_targets()
            except BrowserError:
                return None
            wanted = self.wanted(source)
            return self.rows([(p, self.missing(wanted, p)) for p in targets])

        return refresh

    def sync(self, target: str | None) -> list[tuple[Profile, list[Extension]]] | None:
        """Open the store page of every missing extension in its profile; returns what was opened."""
        plan = self.plan(target)
        if not plan:
            return plan
        if not shutil.which(self.browser.command):
            raise BrowserError(f"browser '{self.browser.command}' not found")
        for i, (profile, missing) in enumerate(plan):
            if i:
                time.sleep(0.5)  # let a just-started browser take the singleton lock first
            spawn([self.browser.command, f"--profile-directory={profile.directory}",
                   *(STORE_URL.format(e.id) for e in missing)])
        return plan


def spawn(argv: list[str]) -> None:
    """Start argv detached without replacing this process (sync may launch several)."""
    if shutil.which("uwsm-app"):
        argv = ["uwsm-app", "--", *argv]
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
