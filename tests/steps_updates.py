"""Step definitions for features/updates.feature.

Each scenario gets an empty sandbox home (tests/sandbox.py) and a temporary
folder holding:

  src/          a git repository made from this working tree, at the installed version
  remote.git    the bare repository omaprofile was "added" from (its origin)
  bin/          fakes for Omarchy's commands and the desktop tools (tests/fakebin.py)

The plugin folder is a clone of remote.git, like `omarchy plugin add` makes,
and the steps run its bin/omaprofile as a separate process. Time is moved with
OMAPROFILE_NOW (seconds since the epoch), which omaprofile reads instead of the
clock when it is set.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import sandbox
from gherkin import step

ROOT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).resolve().parent / "fakebin.py"
FAKE_TOOLS = (
    "omarchy", "omarchy-shell", "notify-send", "hyprctl", "uwsm-app", "setsid", "update-desktop-database",
    "xdg-mime", "xdg-settings", "gtk-launch", "chromium", "omarchy-launch-floating-terminal-with-presentation",
    "omarchy-launch-webapp", "omarchy-theme-set-browser", "pkexec", "sudo", "wl-copy",
)
HOME = sandbox.SANDBOX
CONFIG_HOME = HOME / ".config"
PLUGIN_DIR = CONFIG_HOME / "omarchy" / "plugins" / "dry.omaprofile"
SHELL_JSON = CONFIG_HOME / "omarchy" / "shell.json"
STATE_FILE = HOME / ".local" / "state" / "omaprofile" / "install.json"
CONFIG_FILE = CONFIG_HOME / "omaprofile" / "config.json"
DAY = 24 * 3600

SYSTEM_CHROMIUM = "[Desktop Entry]\nType=Application\nName=Chromium\nExec=/usr/bin/chromium %U\n"


def git(*args, cwd: Path) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    return subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd, env=env,
                          check=True, capture_output=True, text=True).stdout


class Context:
    def __init__(self):
        sandbox.wipe()
        self.tmp = Path(tempfile.mkdtemp(prefix="omaprofile-feature."))
        self.src = self.tmp / "src"
        self.remote = self.tmp / "remote.git"
        self.fake_dir = self.tmp / "fake"
        self.log = self.fake_dir / "calls.jsonl"
        self.cli = PLUGIN_DIR / "bin" / "omaprofile"
        self.now = int(time.time())
        self.mark = 0  # log entries before this one happened before the current When
        self.last = None
        self.fake_dir.mkdir()
        self.log.touch()

        bindir = self.tmp / "bin"
        bindir.mkdir()
        for name in FAKE_TOOLS:
            (bindir / name).symlink_to(FAKES)
        system = self.tmp / "system" / "applications"
        system.mkdir(parents=True)
        (system / "chromium.desktop").write_text(SYSTEM_CHROMIUM)
        self.env = {
            **os.environ,
            "PATH": f"{bindir}:/usr/bin:/bin",
            "XDG_DATA_DIRS": str(self.tmp / "system"),
            "OMAPROFILE_POLICY_ROOTS": str(self.tmp / "no-policy"),
            "FAKE_LOG": str(self.log),
            "FAKE_DIR": str(self.fake_dir),
            "GIT_TERMINAL_PROMPT": "0",
        }
        for path in (PLUGIN_DIR, SHELL_JSON, STATE_FILE, CONFIG_FILE):
            assert sandbox.inside(path), f"feature path escapes the sandbox: {path}"
        # A fake that cannot run would let PATH fall through to the real tool.
        for name in FAKE_TOOLS:
            found = shutil.which(name, path=self.env["PATH"])
            assert found == str(bindir / name), f"{name} resolves to {found}, not its fake"

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        sandbox.wipe()

    # -------------------------------------------------------------- repository

    def make_source(self, version: str) -> None:
        files = git("ls-files", "-co", "--exclude-standard", cwd=ROOT).splitlines()
        for name in files:
            if (ROOT / name).is_file():
                target = self.src / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        self.set_version(version, ["Every web app asks for a profile, including ones added later"], fresh=True)
        git("init", "-q", cwd=self.src)
        git("add", "-A", cwd=self.src)
        git("commit", "-qm", f"omaprofile {version}", cwd=self.src)
        git("clone", "-q", "--bare", str(self.src), str(self.remote), cwd=self.tmp)

    def set_version(self, version: str, changes: list[str], fresh: bool = False) -> None:
        manifest = self.src / "manifest.json"
        data = json.loads(manifest.read_text())
        data["version"] = version
        manifest.write_text(json.dumps(data, indent=2) + "\n")
        changelog = self.src / "CHANGELOG.md"
        old = "" if fresh or not changelog.exists() else changelog.read_text().split("\n", 2)[2]
        entry = f"## {version}\n" + "".join(f"- {c}\n" for c in changes) + "\n"
        changelog.write_text("# Changelog\n\n" + entry + old)

    def publish(self, version: str, changes: list[str] | None = None) -> None:
        self.set_version(version, changes or [f"Release {version}"])
        git("commit", "-qam", f"omaprofile {version}", cwd=self.src)
        git("push", "-q", str(self.remote), "HEAD:main", cwd=self.src)

    # -------------------------------------------------------------- running

    def run(self, *args: str, answer: str | None = None, at: int | None = None) -> subprocess.CompletedProcess:
        env = {**self.env, "OMAPROFILE_NOW": str(self.now if at is None else at)}
        self.last = subprocess.run([str(self.cli), *args], env=env, capture_output=True, text=True,
                                   input=answer + "\n" if answer is not None else "", timeout=60)
        return self.last

    def begin_action(self) -> None:
        self.mark = len(self.calls(all_calls=True))

    def calls(self, tool: str | None = None, all_calls: bool = False) -> list[dict]:
        entries = [json.loads(line) for line in self.log.read_text().splitlines() if line]
        entries = entries if all_calls else entries[self.mark:]
        return [e for e in entries if tool is None or e["tool"] == tool]

    def output(self) -> str:
        assert self.last is not None, "no command has run"
        return self.last.stdout + self.last.stderr

    def status(self) -> dict:
        proc = subprocess.run([str(self.cli), "update", "--status"], capture_output=True, text=True,
                              env={**self.env, "OMAPROFILE_NOW": str(self.now)}, timeout=30)
        assert proc.returncode == 0, f"omaprofile update --status failed: {proc.stdout}{proc.stderr}"
        return json.loads(proc.stdout)

    def notifications(self) -> list[dict]:
        out = []
        for call in self.calls("notify-send"):
            args, actions, plain, i = call["args"], [], [], 0
            while i < len(args):
                a = args[i]
                if a in ("-A", "--action"):
                    actions.append(args[i + 1].partition("=")[2]); i += 2; continue
                if a.startswith("--action="):
                    actions.append(a.split("=", 2)[2]); i += 1; continue
                if a in ("-a", "-i", "-u", "-t", "-h", "-c", "--app-name", "--icon", "--urgency",
                         "--expire-time", "--hint", "--category", "-r", "--replace-id"):
                    i += 2; continue
                if a.startswith("-"):
                    i += 1; continue
                plain.append(a); i += 1
            out.append({"summary": plain[0] if plain else "", "body": " ".join(plain[1:]),
                        "buttons": actions, "args": args})
        return out

    def summons(self) -> list[dict]:
        payloads = []
        for call in self.calls("omarchy-shell"):
            rest = [a for a in call["args"] if a != "-q"]
            if rest[:2] == ["shell", "summon"]:
                payloads.append(json.loads(rest[3]))
        return payloads


# ------------------------------------------------------------------ Given

@step("omaprofile {version} was installed with {how}")
def installed(ctx, version, how):
    assert how == "omarchy plugin add"
    ctx.make_source(version)
    PLUGIN_DIR.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "-q", str(ctx.remote), str(PLUGIN_DIR), cwd=ctx.tmp)


@step("omaprofile is set up")
def set_up(ctx):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text("{}\n")


@step("omaprofile has not been set up yet")
def not_set_up(ctx):
    STATE_FILE.unlink(missing_ok=True)


def write_bar(with_icon: bool) -> None:
    right = [{"id": "omarchy.tray"}, *([{"id": "dry.omaprofile"}] if with_icon else []),
             {"id": "omarchy.audio"}]
    SHELL_JSON.parent.mkdir(parents=True, exist_ok=True)
    SHELL_JSON.write_text(json.dumps({"bar": {"layout": {"left": [], "right": right}}}, indent=2))


@step("the update icon is on the bar")
def icon_on_bar(ctx):
    write_bar(True)


@step("the update icon is not on the bar")
def icon_not_on_bar(ctx):
    write_bar(False)


@step("version {version} is published")
def published(ctx, version):
    ctx.publish(version)


@step("version {version} is published with the changes:")
def published_with(ctx, version):
    ctx.publish(version, [row[0] for row in ctx.step.table])


@step("version {version} was found by an earlier check")
def found_earlier(ctx, version):
    if not re.search(rf"^## {re.escape(version)}$", (ctx.src / "CHANGELOG.md").read_text(), re.M):
        ctx.publish(version)
    proc = ctx.run("update", "--check", "--scheduled", at=ctx.now - DAY - 3600)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@step("the last check was {hours} hours ago")
def last_check(ctx, hours):
    proc = ctx.run("update", "--check", "--scheduled", at=ctx.now - int(hours) * 3600)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@step("the daily check ran yesterday")
def ran_yesterday(ctx):
    proc = ctx.run("update", "--check", "--scheduled", at=ctx.now - DAY - 3600)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@step("the repository cannot be reached")
def unreachable(ctx):
    git("remote", "set-url", "origin", str(ctx.tmp / "gone.git"), cwd=PLUGIN_DIR)


@step("the config has update checks turned off")
def checks_off(ctx):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"browser": {"command": "chromium", "rules": []},
                                       "updates": {"check": False}}, indent=2))


@step("notification buttons get clicked: {label}")
def will_click(ctx, label):
    (ctx.fake_dir / "notify-click").write_text(label)


@step("Omarchy's plugin updater will reject the update")
def update_fails(ctx):
    (ctx.fake_dir / "update-fails").touch()


@step("omaprofile runs from a source checkout instead")
def source_checkout(ctx):
    """A developer install: bin/omaprofile install from a clone; the plugin folder is a copy."""
    checkout = ctx.tmp / "checkout"
    git("clone", "-q", str(ctx.remote), str(checkout), cwd=ctx.tmp)
    shutil.rmtree(PLUGIN_DIR)
    PLUGIN_DIR.mkdir(parents=True)
    shutil.copy2(checkout / "manifest.json", PLUGIN_DIR / "manifest.json")
    shutil.copytree(checkout / "plugin", PLUGIN_DIR / "plugin")
    ctx.cli = checkout / "bin" / "omaprofile"


@step("omaprofile was updated from 0.2.0")
def updated_from_020(ctx):
    # 0.2.0 kept no update record: an install record and nothing else is how it looks.
    state = STATE_FILE.parent
    extra = [p.name for p in state.iterdir() if p != STATE_FILE] if state.exists() else []
    assert not extra, f"expected only the install record from 0.2.0, found {extra}"


@step("the plugin reloaded and said {summary}")
def reloaded_before(ctx, summary):
    ctx.begin_action()
    proc = ctx.run("install", "--auto")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    notification(ctx, summary)


# ------------------------------------------------------------------ When

@step("the daily check runs")
def daily_check(ctx):
    ctx.begin_action()
    proc = ctx.run("update", "--check", "--scheduled")
    assert proc.returncode == 0, f"the daily check must never fail; exit {proc.returncode}:\n{ctx.output()}"


@step("the daily check runs a day later")
def daily_check_later(ctx):
    ctx.begin_action()
    proc = ctx.run("update", "--check", "--scheduled", at=ctx.now + DAY + 3600)
    assert proc.returncode == 0, f"the daily check must never fail; exit {proc.returncode}:\n{ctx.output()}"


@step("I run {command}")
def run_command(ctx, command):
    ctx.begin_action()
    ctx.run(*command.removeprefix("omaprofile ").split())


@step("I run {command} and answer {answer}")
def run_and_answer(ctx, command, answer):
    ctx.begin_action()
    ctx.run(*command.removeprefix("omaprofile ").split(), answer=answer)


@step("I click the update icon")
def click_icon(ctx):
    ctx.begin_action()
    ctx.run("update", "--open")


@step("I right-click the update icon")
def right_click_icon(ctx):
    ctx.begin_action()
    ctx.run("update", "--dismiss")


@step("a link opens the profile picker")
def link_opens_picker(ctx):
    local_state = CONFIG_HOME / "chromium" / "Local State"
    local_state.parent.mkdir(parents=True, exist_ok=True)
    local_state.write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Personal"}, "Profile 1": {"name": "Work"}}}}))
    ctx.begin_action()
    ctx.run("browser", "open", "https://example.com")


@step("the plugin reloads")
@step("the plugin loads")
def plugin_loads(ctx):
    ctx.begin_action()
    ctx.run("install", "--auto")


# ------------------------------------------------------------------ Then

@step("the bar shows the update icon with {tooltip}")
def icon_shown(ctx, tooltip):
    status = ctx.status()
    assert status.get("icon") is True, f"icon hidden; status: {status}"
    assert status.get("tooltip") == tooltip, f"tooltip {status.get('tooltip')!r}, expected {tooltip!r}"


@step("the bar does not show the update icon")
def icon_hidden(ctx):
    status = ctx.status()
    assert status.get("icon") is False, f"icon shown; status: {status}"


@step("it prints {text}")
def prints(ctx, text):
    assert text in ctx.output(), f"{text!r} not in output:\n{ctx.output()}"


@step("it fails with {text}")
def fails_with(ctx, text):
    assert ctx.last.returncode != 0, f"exit 0, expected a failure; output:\n{ctx.output()}"
    assert text in ctx.output(), f"{text!r} not in output:\n{ctx.output()}"


@step("no error is shown")
def no_error(ctx):
    assert ctx.last.returncode == 0, f"exit {ctx.last.returncode}:\n{ctx.output()}"
    assert not ctx.last.stderr.strip(), f"stderr:\n{ctx.last.stderr}"
    assert not ctx.notifications(), f"notifications: {ctx.notifications()}"


@step("no notification is shown")
def no_notification(ctx):
    assert not ctx.notifications(), f"notifications: {[n['summary'] for n in ctx.notifications()]}"


@step("a notification says {summary}")
def notification(ctx, summary):
    found = [n for n in ctx.notifications() if n["summary"].startswith(summary)]
    assert found, f"no notification {summary!r}; got {[n['summary'] for n in ctx.notifications()]}"


@step("a notification says {summary} with the buttons {first} and {second}")
def notification_buttons(ctx, summary, first, second):
    found = [n for n in ctx.notifications() if n["summary"] == summary]
    assert found, f"no notification {summary!r}; got {[n['summary'] for n in ctx.notifications()]}"
    assert len(found) == 1, f"{len(found)} notifications {summary!r}"
    assert found[0]["buttons"] == [first, second], f"buttons {found[0]['buttons']}"


@step("a floating terminal opens running {command}")
def floating_terminal(ctx, command):
    tool = "omarchy-launch-floating-terminal-with-presentation"
    runs = [c for c in ctx.calls(tool)]
    assert runs, f"no {tool}; calls: {[c['tool'] for c in ctx.calls()]}"
    pattern = re.compile(r"(^|[\s/'\"])" + re.escape(command) + r"($|[\s'\"])")
    assert any(pattern.search(" ".join(c["args"])) for c in runs), f"{command!r} not run: {runs}"


@step("the picker's footer says {text}")
def picker_footer(ctx, text):
    payloads = ctx.summons()
    assert payloads, f"the picker was not opened; output:\n{ctx.output()}"
    assert payloads[-1].get("footer") == text, f"footer {payloads[-1].get('footer')!r}"


@step("the picker has no footer")
def picker_no_footer(ctx):
    payloads = ctx.summons()
    assert payloads, f"the picker was not opened; output:\n{ctx.output()}"
    assert not payloads[-1].get("footer"), f"footer {payloads[-1].get('footer')!r}"


@step("Omarchy's plugin updater was run for dry.omaprofile")
def updater_ran(ctx):
    runs = [c for c in ctx.calls("omarchy") if c["args"][:3] == ["plugin", "update", "dry.omaprofile"]]
    assert runs, f"omarchy plugin update was not run; calls: {ctx.calls()}"


@step("Omarchy's plugin updater was not run")
def updater_not_run(ctx):
    runs = [c for c in ctx.calls("omarchy") if c["args"][:2] == ["plugin", "update"]]
    assert not runs, f"omarchy plugin update ran: {runs}"


@step("omaprofile {version} is installed")
def version_installed(ctx, version):
    found = json.loads((PLUGIN_DIR / "manifest.json").read_text())["version"]
    assert found == version, f"installed {found}, expected {version}"


@step("the update icon is put on the right of the bar, unless the user placed it already")
def icon_put(ctx):
    calls = [[a for a in c["args"] if a != "-q"] for c in ctx.calls("omarchy-shell")]
    puts = [c for c in calls if c[:3] == ["shell", "putBarWidget", "dry.omaprofile"]]
    assert puts, f"putBarWidget was not called; omarchy-shell calls: {calls}"
    assert json.loads(puts[-1][3]).get("section") == "right", f"placement {puts[-1][3:]}"
    moved = [c for c in calls if c[1:2] in (["enablePlugin"], ["moveBarWidget"])]
    moved += [c["args"] for c in ctx.calls("omarchy") if c["args"][:2] in (["plugin", "enable"], ["bar", "move"])]
    assert not moved, f"moves a placed icon: {moved}"


@step("the setup prompt says {text}")
def setup_prompt(ctx, text):
    payloads = ctx.summons()
    assert payloads, f"no setup prompt; output:\n{ctx.output()}"
    assert text in payloads[-1].get("body", ""), f"{text!r} not in:\n{payloads[-1].get('body')}"


@step("the plugin manifest declares a bar widget that goes on the right by default")
def manifest_bar_widget(ctx):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert "bar-widget" in manifest.get("kinds", []), f"kinds: {manifest.get('kinds')}"
    entry = manifest.get("entryPoints", {}).get("barWidget")
    assert entry and (ROOT / entry).is_file(), f"barWidget entry point: {entry}"
    assert manifest.get("barWidget", {}).get("defaultSection") == "right", manifest.get("barWidget")


@step("manifest.json, pyproject.toml and the newest CHANGELOG.md entry name the same version")
def versions_agree(ctx):
    manifest = json.loads((ROOT / "manifest.json").read_text())["version"]
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    changelog_file = ROOT / "CHANGELOG.md"
    assert changelog_file.exists(), "there is no CHANGELOG.md"
    newest = re.search(r"^## \[?v?([0-9][^\]\s]*)", changelog_file.read_text(), re.M)
    assert newest, "CHANGELOG.md has no '## <version>' entry"
    found = {"manifest.json": manifest, "pyproject.toml": pyproject, "CHANGELOG.md": newest.group(1)}
    assert len(set(found.values())) == 1, f"versions differ: {found}"
