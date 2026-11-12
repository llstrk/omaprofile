"""Command line: ``omaprofile <group> <action> ...`` in the omarchy grammar."""

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from pathlib import Path

from . import PLUGIN_ID
from . import config as cfgmod
from .browser import Browser, BrowserError, default_browser, looks_like_url
from .contexts import Context, ContextError
from .extensions import Extensions
from .install import (CLEANUP_DIR, CLEANUP_LOCK, HYPRLAND_LUA, LAUNCHER, REQUIRE_LINE, InstallError,
                      decline_setup, install, plugin_was_removed, remove_cleanup_copy, setup_state,
                      uninstall)
from .picker import Choice, PickerError, notify, pick
from . import policy

USAGE = """\
omaprofile — pick a profile before something launches.

  omaprofile browser open [flags...] [url]     pick a profile, open the link
  omaprofile browser launch <profile> [args]   open as a profile, no picker
  omaprofile browser profiles                  list profiles
  omaprofile webapp open <url> [flags...]      pick a profile, open as a web app
  omaprofile extensions [sync]                 pick a profile (or all), sync extensions
  omaprofile extensions sync all|<profile>     sync from the first profile, no picker
  omaprofile extensions list                   first profile's extensions vs the others
  omaprofile <context> open [args...]          pick a choice, run its command
  omaprofile <context> list                    list the context's choices
  omaprofile contexts                          list configured contexts
  omaprofile pick [--title T] [--subtitle S] CHOICE...
                                               generic picker; prints chosen id
                                               CHOICE = id|label|color|glyph|detail
  omaprofile install                           plugin, default browser, web app hook
  omaprofile install --auto                    the overlay's call: ask to install if needed
  omaprofile uninstall [fallback.desktop]      undo install, restore the previous browser
  omaprofile policy [recommend|restore]        Omarchy's Chromium theme policy: show,
                                               make it recommended, make it mandatory again
  omaprofile config                            print the config

A bare URL, browser flag, or no argument at all means `browser open`, which
is what the desktop entry and `omarchy launch browser` send.
"""


class UsageError(Exception):
    pass


def split_action(args: list[str], default: str = "open") -> tuple[str, list[str]]:
    return (args[0], args[1:]) if args else (default, [])


# ---------------------------------------------------------------- groups

def cmd_browser(cfg: dict, args: list[str]) -> None:
    browser = Browser(cfg)
    action, rest = split_action(args)
    if action == "open":
        browser.open(rest)
    elif action == "launch" and rest:
        browser.launch(browser.resolve(rest[0]), rest[1:])
    elif action == "profiles":
        for p in browser.profiles():
            print(f"{p.directory:12}  {p.name}")
    elif action.startswith("--") or looks_like_url(action):
        browser.open(args)
    else:
        raise UsageError(f"unknown browser action '{action}' (open, launch <profile>, profiles)")


def cmd_webapp(cfg: dict, args: list[str]) -> None:
    action, rest = split_action(args)
    if action == "open" and rest:
        Browser(cfg).open_webapp(rest[0], rest[1:])
    else:
        raise UsageError("usage: omaprofile webapp open <url> [flags...]")


def cmd_extensions(cfg: dict, args: list[str]) -> None:
    exts = Extensions(cfg)
    action, rest = split_action(args, default="sync")
    if action == "list":
        print(exts.list())
    elif action == "sync" and len(rest) <= 1:
        plan = exts.sync(rest[0] if rest else None)
        if plan is None:  # picker cancelled
            return
        if plan:
            for profile, missing in plan:
                print(f"{profile.name}: opened {', '.join(e.name for e in missing)}")
            notify("Extensions", "Click \"Add to Chromium\" on each opened tab.")
        else:
            print("nothing to sync")
            notify("Extensions", "Nothing to sync.")
    else:
        raise UsageError("usage: omaprofile extensions [list | sync [all|<profile>]]")


def cmd_context(cfg: dict, name: str, args: list[str]) -> None:
    ctx = Context(name, cfg)
    action, rest = split_action(args)
    if action == "open":
        ctx.open(rest)
    elif action == "list":
        for c in ctx.choices:
            print(f"{c.id:12}  {c.label:16}  {c.command}")
    else:
        raise UsageError(f"unknown action '{action}' for context '{name}' (open, list)")


def cmd_contexts(cfg: dict, args: list[str]) -> None:
    profiles = Browser(cfg).profiles()
    print(f"{'browser':12}  " + (", ".join(p.name for p in profiles) or "(no profiles yet; run the browser once)"))
    for name in cfg.get("contexts", {}):
        print(f"{name:12}  " + ", ".join(c.label for c in Context(name, cfg).choices))


def cmd_pick(cfg: dict, args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="omaprofile pick")
    parser.add_argument("--title", default="Choose")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("choices", nargs="+", metavar="CHOICE")
    ns = parser.parse_args(args)
    chosen = pick(ns.title, ns.subtitle, [Choice.from_spec(s) for s in ns.choices])
    if chosen is None:
        sys.exit(1)
    print(chosen)


def cmd_install(cfg: dict, args: list[str]) -> None:
    if args == ["--auto"]:
        cmd_auto_install()
        return
    if args:
        raise UsageError("usage: omaprofile install [--auto]")
    hook, plugin_error = install()
    print(f"installed: default browser is {default_browser()}, cli {cfgmod.BIN_LINK} -> {LAUNCHER}")
    if plugin_error:
        print(f"plugin: could not enable {PLUGIN_ID} ({plugin_error}); is the Omarchy shell running? "
              f"Then run: omarchy plugin enable {PLUGIN_ID}", file=sys.stderr)
    else:
        print(f"plugin: {PLUGIN_ID} enabled")
    if hook == "added":
        print(f"web apps: added {REQUIRE_LINE} to {HYPRLAND_LUA}")
    elif hook == "manual":
        print(f"web apps: add {REQUIRE_LINE} to {HYPRLAND_LUA} before require(\"default.hypr.omarchy\") "
              "so Omarchy's web app keybindings use the picker", file=sys.stderr)
    cmd_contexts(cfgmod.load(), [])


SETUP_BODY = (
    "To open links in the profile you choose, omaprofile needs to:\n"
    "\u2022 become the default browser for http, https and HTML files (xdg-mime)\n"
    "\u2022 point your web app launchers at the picker\n"
    "\u2022 add one line to ~/.config/hypr/hyprland.lua for the web app keys\n"
    "\u2022 add the omaprofile command to ~/.local/bin\n"
    "\n"
    "Your current browser settings are saved; uninstall restores them."
)


def cmd_auto_install() -> None:
    """The overlay's call when it loads: ask to finish setting up, if that is needed."""
    if setup_state() != "ask":
        return
    choice = pick("Finish setting up omaprofile", "", [
        Choice("install", "Set it up", color="#1bb91f", glyph="\u2713",
               detail="Make the changes above"),
        Choice("later", "Not now", color="#cacccc", glyph="\u2026",
               detail="Ask again next time the shell starts"),
        Choice("never", "Don't ask again", color="#f3b2be", glyph="\u00d7",
               detail="To set up later, run the install command from the README"),
    ], body=SETUP_BODY)
    if choice == "install":
        hook, _ = install(enable=False)
        body = "Links from any app now ask which profile to open in."
        if hook == "manual":
            body += f" For the web app keys, add {REQUIRE_LINE} to {HYPRLAND_LUA}."
        notify("omaprofile is set up", body)
        ask_policy()
    elif choice == "never":
        decline_setup()


def ask_policy() -> None:
    """Last setup step, default no: let each profile have its own theme."""
    roots = policy.managed_roots()
    if not roots:
        return
    choice = pick("A different theme for each profile?", "", [
        Choice("keep", "No, keep it mandatory", color="#cacccc", glyph="\u2013",
               detail="Every profile uses the Omarchy theme"),
        Choice("move", "Yes, make it recommended", color="#1bb91f", glyph="\u2713",
               detail="Asks for your password"),
    ], body=(
        "Omarchy's Chromium theme is a mandatory policy, so every profile looks the same. "
        "As a recommended policy it only applies when a profile first starts; "
        "after that each profile can pick its own theme.\n\n"
        "Undo with: omaprofile policy restore"))
    if choice != "move":
        return
    try:
        policy.recommend(roots)
    except policy.PolicyError as e:
        notify("omaprofile: browser policy unchanged", str(e))


def cmd_policy(cfg: dict, args: list[str]) -> None:
    action = args[0] if args else "status"
    if action == "recommend":
        done = policy.recommend()
        print("Omarchy's policy is now recommended: " + ", ".join(map(str, done)) if done
              else "nothing to do: Omarchy's policy is not mandatory")
    elif action == "restore":
        done = policy.restore()
        print("Omarchy's policy is mandatory again: " + ", ".join(map(str, done)) if done
              else "nothing to restore")
    elif action == "status":
        for root, state in policy.status() or [("browser policy", "no Omarchy policy found")]:
            print(f"{root}: {state}")
    else:
        raise UsageError("usage: omaprofile policy [recommend|restore]")


def restore_policy() -> str:
    """Undo the theme policy change if omaprofile made it; '' or the error."""
    if not policy.MOVED_MARKER.exists():
        return ""
    try:
        policy.restore()
    except policy.PolicyError as e:
        return str(e)
    return ""


def cmd_uninstall(cfg: dict, args: list[str]) -> None:
    if "--removed" in args:
        cmd_uninstall_removed([a for a in args if a != "--removed"])
        return
    managed = uninstall(*args[:1])
    print(f"uninstalled: default browser is now {default_browser()}; config kept in {cfgmod.CONFIG_FILE}")
    policy_error = restore_policy()
    if policy_error:
        print(f"browser policy: {policy_error}; make it mandatory again with: {LAUNCHER} policy restore",
              file=sys.stderr)
    remove_cleanup_copy()
    if managed:
        print(f"plugin: {PLUGIN_ID} disabled; delete it with: omarchy plugin remove {PLUGIN_ID}")


def removal_asked_marker() -> Path:
    """Per login session: "Leave them for now" means not again until the next login."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "omaprofile-removal-asked"


# Runs in a floating Omarchy terminal from a script file next to the answer
# file, so nothing has to survive the terminal's own argument handling. gum
# choose moves with j/k or the arrows; Esc (or closing the window) leaves
# things as they are. The answer file is written on every way out.
REMOVAL_PROMPT = """#!/bin/bash
dir=$(dirname "$0")
trap 'printf "%s" "${{choice:-}}" > "$dir/answer.tmp" && mv "$dir/answer.tmp" "$dir/answer"' EXIT
# The current theme's gum colors, as omarchy-restart-gum exports them (read
# here so a refused or missing helper cannot end the prompt).
gum_env=$HOME/.local/state/omarchy/current/theme/gum_env.lua
if [ -f "$gum_env" ]; then
  while IFS=$'\t' read -r key value; do
    [ -n "$key" ] && [ -n "$value" ] && export "$key=$value"
  done < <(awk -F'"' '/hl\\.env\\("[A-Z0-9_]+", "[^"]+"\\)/ {{ print $2 "\t" $4 }}' "$gum_env")
fi
gum style --border rounded --padding "1 2" --width 60 "omaprofile was removed" "" {question}
echo
choice=$(gum choose --header "" "Undo its changes" "Leave them for now")
"""


def ask_removal(timeout: float = 600) -> str:
    """Ask in a floating terminal whether to undo omaprofile's changes: 'undo', 'leave', or '' if nobody could."""
    what = ("the default browser, web apps, Hyprland and the Chromium theme policy"
            if policy.MOVED_MARKER.exists() else "the default browser, web apps and Hyprland")
    question = f"Undo its changes to {what}? Leaving them, links open in the browser directly."
    if not shutil.which("xdg-terminal-exec"):
        return ""
    folder = Path(tempfile.mkdtemp(prefix="omaprofile."))
    prompt, answer = folder / "prompt", folder / "answer"
    try:
        prompt.write_text(REMOVAL_PROMPT.format(question=shlex.quote(question)))
        prompt.chmod(0o700)
        subprocess.Popen(["xdg-terminal-exec", "--app-id=org.omarchy.terminal", "--title=omaprofile",
                          "-e", str(prompt)],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while not answer.exists():
            if time.monotonic() > deadline:
                return ""
            time.sleep(0.1)
        return "undo" if answer.read_text().strip() == "Undo its changes" else "leave"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def cmd_uninstall_removed(args: list[str]) -> None:
    """`omarchy plugin remove` ran before uninstall: offer to clean up, from the copy kept for this."""
    CLEANUP_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CLEANUP_LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # the overlay's watcher and the launcher can both start one
        try:
            offer_removal_cleanup(args)
        finally:
            CLEANUP_LOCK.unlink(missing_ok=True)


def offer_removal_cleanup(args: list[str]) -> None:
    if not plugin_was_removed() or removal_asked_marker().exists():
        return
    answer = ask_removal()
    if answer != "undo":
        if answer:
            removal_asked_marker().touch()
        return
    uninstall(*args[:1], removed=True)
    body = "Links and web apps open in the browser directly again."
    policy_error = restore_policy()
    if policy_error:
        body += (f" The Chromium theme policy is still recommended ({policy_error}); make it "
                 f"mandatory again with: {CLEANUP_DIR / 'bin' / 'omaprofile'} policy restore")
        print(body, file=sys.stderr)
    else:
        remove_cleanup_copy()
    notify("omaprofile uninstalled", body)


def cmd_config(cfg: dict, args: list[str]) -> None:
    print(json.dumps(cfg, indent=2))


def cmd_help(cfg: dict, args: list[str]) -> None:
    print(USAGE, end="")


GROUPS = {
    "browser": cmd_browser,
    "webapp": cmd_webapp,
    "extensions": cmd_extensions,
    "pick": cmd_pick,
    "contexts": cmd_contexts,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "policy": cmd_policy,
    "config": cmd_config,
    "help": cmd_help,
    "-h": cmd_help,
    "--help": cmd_help,
}


# ---------------------------------------------------------------- dispatch

def dispatch(argv: list[str]) -> None:
    cfg = cfgmod.load()
    group, rest = split_action(argv, default="browser")
    if group in GROUPS:
        GROUPS[group](cfg, rest)
    elif group in cfg.get("contexts", {}):
        cmd_context(cfg, group, rest)
    elif group.startswith("--") or looks_like_url(group):
        cmd_browser(cfg, ["open", *argv])
    else:
        known = ", ".join([*GROUPS, *cfg.get("contexts", {})])
        raise UsageError(f"unknown group '{group}' (groups: {known})")


def report(message: str) -> None:
    """stderr, plus a notification when nobody is watching it (desktop entry, keybinding)."""
    print(f"omaprofile: {message}", file=sys.stderr)
    if not sys.stderr.isatty():
        notify("omaprofile", message, urgency="critical")


def main(argv: list[str] | None = None) -> int:
    try:
        dispatch(sys.argv[1:] if argv is None else argv)
    except (UsageError, BrowserError, ContextError, PickerError, InstallError,
            policy.PolicyError, cfgmod.ConfigError, OSError) as e:
        report(str(e))
        return 1
    except Exception as e:  # a bug: still tell the user the link went nowhere
        traceback.print_exc()
        report(f"internal error: {type(e).__name__}: {e}")
        return 1
    return 0
