#!/usr/bin/python3
"""omarchy-shell for rules.feature: answers the picker the way the overlay does.

Linked as omarchy-shell in place of tests/fakebin.py by rules.feature's
Background. A summon is answered from $FAKE_DIR/pick, {"label": ..., "shift":
bool}: the row with that label is chosen, with Shift held when "shift" is true.
Each answer is used once. Everything else, and a summon without an answer
(cancelled), goes to fakebin.py, which logs the call.

The overlay writes the chosen id on the first line of the selection file and,
when Shift was held, "remember" on the second.
"""

import json
import os
import sys
from pathlib import Path

FAKEBIN = Path(__file__).resolve().parent / "fakebin.py"
args = sys.argv[1:]
fake_dir = Path(os.environ["FAKE_DIR"])
rest = [a for a in args if a != "-q"]
pick = fake_dir / "pick"

if rest[:2] == ["shell", "summon"] and len(rest) >= 4 and pick.exists():
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"tool": "omarchy-shell", "args": args}) + "\n")
    payload = json.loads(rest[3])
    answer = json.loads(pick.read_text())
    pick.unlink()
    rows = [c for c in payload["choices"] if c["label"] == answer["label"]]
    assert rows, f"no row {answer['label']!r} in the picker: {[c['label'] for c in payload['choices']]}"
    Path(payload["selectionFile"]).write_text(rows[0]["id"] + "\n" + ("remember\n" if answer["shift"] else ""))
    Path(payload["doneFile"]).touch()
    print("ok")
    sys.exit(0)

# fakebin.py takes its tool name from argv[0]: run it through a link named omarchy-shell.
link = fake_dir / "fakebin" / "omarchy-shell"
if not link.exists():
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(FAKEBIN)
os.execv(str(link), [str(link), *args])
