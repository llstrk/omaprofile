"""Command line: ``omaprofile <group> <action> ...`` in the omarchy grammar."""

import argparse
import json
import sys

from . import config as cfgmod
from .browser import Browser, BrowserError, default_browser, looks_like_url
from .contexts import Context, ContextError
from .install import LAUNCHER, install, uninstall
from .picker import Choice, PickerError, pick

USAGE = """\
omaprofile — pick a profile before something launches.

  omaprofile browser open [flags...] [url]     pick a profile, open the link
  omaprofile browser launch <profile> [args]   open as a profile, no picker
  omaprofile browser profiles                  list profiles
  omaprofile webapp open <url> [flags...]      pick a profile, open as a web app
  omaprofile <context> open [args...]          pick a choice, run its command
  omaprofile <context> list                    list the context's choices
  omaprofile contexts                          list configured contexts
  omaprofile pick [--title T] [--subtitle S] CHOICE...
                                               generic picker; prints chosen id
                                               CHOICE = id|label|color|glyph|detail
  omaprofile install | uninstall               plugin + default browser
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
    print(f"{'browser':12}  " + ", ".join(p.name for p in Browser(cfg).profiles()))
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
    install()
    print(f"installed: default browser is {default_browser()}, cli {cfgmod.BIN_LINK} -> {LAUNCHER}")
    cmd_contexts(cfgmod.load(), [])


def cmd_uninstall(cfg: dict, args: list[str]) -> None:
    uninstall(*args[:1])
    print(f"default browser is now {default_browser()}; plugin and config left in place")


def cmd_config(cfg: dict, args: list[str]) -> None:
    print(json.dumps(cfg, indent=2))


def cmd_help(cfg: dict, args: list[str]) -> None:
    print(USAGE, end="")


GROUPS = {
    "browser": cmd_browser,
    "webapp": cmd_webapp,
    "pick": cmd_pick,
    "contexts": cmd_contexts,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
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


def main(argv: list[str] | None = None) -> int:
    try:
        dispatch(sys.argv[1:] if argv is None else argv)
    except (UsageError, BrowserError, ContextError, PickerError, cfgmod.ConfigError, OSError) as e:
        print(f"omaprofile: {e}", file=sys.stderr)
        return 1
    return 0
