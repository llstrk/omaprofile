#!/usr/bin/python3
"""Stand-in for Omarchy's commands and the desktop tools in feature scenarios.

Linked under each tool's name in a scenario's bin folder, first on PATH. Every
call is appended to $FAKE_LOG as {"tool": name, "args": [...]}; nothing reaches
the real desktop. Behaviour the scenarios steer lives in $FAKE_DIR:

  notify-click   the button label to "click" on the next notification with buttons
  update-fails   `omarchy plugin update` rolls back instead of updating
"""

import json
import os
import subprocess
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "args": args}) + "\n")
fake_dir = Path(os.environ["FAKE_DIR"])
plugin_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "omarchy" / "plugins" / "dry.omaprofile"


def run_rest(rest: list[str]) -> None:
    """setsid / uwsm-app: run what they wrap, so it is logged too."""
    if rest and rest[0] == "--":
        rest = rest[1:]
    if rest:
        sys.exit(subprocess.run(rest).returncode)


if tool == "omarchy-shell":
    rest = [a for a in args if a != "-q"]
    if rest[:2] == ["shell", "summon"] and len(rest) >= 4:
        payload = json.loads(rest[3])
        Path(payload["doneFile"]).touch()  # cancelled: nothing chosen
    print("ok")
elif tool == "notify-send":
    actions = {}
    for i, a in enumerate(args):
        spec = args[i + 1] if a in ("-A", "--action") and i + 1 < len(args) else \
            a.split("=", 1)[1] if a.startswith("--action=") else None
        if spec:
            name, _, label = spec.partition("=")
            actions[label or name] = name
    click = fake_dir / "notify-click"
    if actions and click.exists():
        label = click.read_text().strip()
        click.unlink()
        if label in actions:
            print(actions[label])
elif tool == "omarchy":
    if args[:2] == ["plugin", "update"]:
        if (fake_dir / "update-fails").exists():
            print("omarchy-plugin-update: update of 'dry.omaprofile' failed validation; rolled back",
                  file=sys.stderr)
            sys.exit(1)
        git = ["git", "-C", str(plugin_dir)]
        subprocess.run([*git, "fetch", "--quiet", "origin", "HEAD"], check=True)
        subprocess.run([*git, "merge", "--quiet", "--ff-only", "FETCH_HEAD"], check=True)
        print("Updated dry.omaprofile.")
elif tool in ("setsid", "uwsm-app"):
    run_rest(args)
# everything else: logged, succeeds, prints nothing
