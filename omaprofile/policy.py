"""Omarchy's Chromium machine policy: mandatory (one theme for every profile) or recommended.

Omarchy installs ``/etc/chromium/policies/managed/omarchy.json`` and, on each
theme change, writes ``color.json`` next to it. Managed policy is mandatory, so
every profile gets the same theme color and profiles cannot be told apart by
it. Moving both files to ``recommended/`` makes them defaults: a new profile
starts with the Omarchy theme and can then pick its own. Omarchy's theme writer
only writes into managed folders that exist, so with that folder gone the move
sticks (and later theme changes no longer reach Chromium). ``restore`` puts the
files back and repaints the theme color.

Both directions are a root write under /etc, done through sudo in a terminal
and pkexec otherwise. OMAPROFILE_POLICY_ROOTS (colon-separated) points both at
other folders, written as the current user without asking for root: for tests.
"""

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod

# Omarchy's policy roots (install/helpers/browser-policy.sh), one per browser.
POLICY_ROOTS = (
    Path("/etc/chromium/policies"),
    Path("/etc/opt/chrome/policies"),
    Path("/etc/opt/edge/policies"),
    Path("/etc/brave/policies"),
)
OMARCHY_FILES = ("omarchy.json", "color.json")
# Written when omaprofile moved the policy, so uninstall knows to put it back.
MOVED_MARKER = cfgmod.STATE_HOME / "omaprofile" / "policy-moved"


def policy_roots() -> tuple[Path, ...]:
    override = os.environ.get("OMAPROFILE_POLICY_ROOTS")
    return tuple(Path(p) for p in override.split(":") if p) if override else POLICY_ROOTS

# $1 is the owner spec for install(1) ("root" in production), the rest are
# policy roots. Only Omarchy's own files move; a managed folder that holds an
# administrator's other files stays (and keeps Omarchy's color writes coming).
RECOMMEND_SCRIPT = r'''
set -eu
own=$1; shift
for root; do
  m=$root/managed; r=$root/recommended
  [ -d "$m" ] && [ ! -L "$m" ] || continue
  for f in omarchy.json color.json; do
    [ -f "$m/$f" ] && [ ! -L "$m/$f" ] || continue
    install -d -m 0755 -o "$own" -g "$own" "$r"
    install -m 0644 -o "$own" -g "$own" -T "$m/$f" "$r/$f"
    rm -f -- "$m/$f"
  done
  rmdir -- "$m" 2>/dev/null || echo "kept $m: it holds other policy files" >&2
done
'''

RESTORE_SCRIPT = r'''
set -eu
own=$1; shift
for root; do
  m=$root/managed; r=$root/recommended
  [ -f "$r/omarchy.json" ] && [ ! -L "$r/omarchy.json" ] || continue
  install -d -m 0755 -o "$own" -g "$own" "$m"
  for f in omarchy.json color.json; do
    [ -f "$r/$f" ] && [ ! -L "$r/$f" ] || continue
    install -m 0644 -o "$own" -g "$own" -T "$r/$f" "$m/$f"
    rm -f -- "$r/$f"
  done
  rmdir -- "$r" 2>/dev/null || true
done
'''


class PolicyError(Exception):
    pass


def managed_roots(roots=None) -> list[Path]:
    """Policy roots whose managed folder still holds an Omarchy file."""
    roots = policy_roots() if roots is None else roots
    return [r for r in roots if any((r / "managed" / f).is_file() for f in OMARCHY_FILES)]


def recommended_roots(roots=None) -> list[Path]:
    roots = policy_roots() if roots is None else roots
    return [r for r in roots if (r / "recommended" / "omarchy.json").is_file()]


def status(roots=None) -> list[tuple[Path, str]]:
    out = []
    for root in (policy_roots() if roots is None else roots):
        if root in managed_roots([root]):
            out.append((root, "mandatory (one theme for every profile)"))
        elif root in recommended_roots([root]):
            out.append((root, "recommended (applies when a profile first starts)"))
    return out


def _run_as_root(script: str, roots: list[Path]) -> None:
    testing = bool(os.environ.get("OMAPROFILE_POLICY_ROOTS"))
    owner = getpass.getuser() if testing else "root"
    argv = ["sh", "-c", script, "omaprofile-policy", owner, *map(str, roots)]
    if testing or os.geteuid() == 0:
        prefix = []
    elif sys.stdin.isatty() and shutil.which("sudo"):
        prefix = ["sudo"]
    elif shutil.which("pkexec"):
        prefix = ["pkexec"]
    else:
        raise PolicyError("needs root: run it in a terminal (sudo) or install polkit (pkexec)")
    proc = subprocess.run([*prefix, *argv])
    if proc.returncode != 0:
        raise PolicyError(f"could not change the browser policy (exit {proc.returncode}); nothing else changed")


def recommend(roots=None) -> list[Path]:
    """Make Omarchy's policy recommended instead of mandatory. Returns the roots changed."""
    todo = managed_roots(roots)
    if todo:
        _run_as_root(RECOMMEND_SCRIPT, todo)
        MOVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        MOVED_MARKER.touch()
    return todo


def restore(roots=None) -> list[Path]:
    """Make Omarchy's policy mandatory again and repaint the theme color."""
    todo = recommended_roots(roots)
    if todo:
        _run_as_root(RESTORE_SCRIPT, todo)
        if shutil.which("omarchy-theme-set-browser") and not os.environ.get("OMAPROFILE_POLICY_ROOTS"):
            subprocess.run(["omarchy-theme-set-browser"])  # writes color.json again
    MOVED_MARKER.unlink(missing_ok=True)
    return todo
