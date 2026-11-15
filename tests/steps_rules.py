"""Step definitions for features/rules.feature.

Uses the scenario Context of steps_updates (a sandbox home, a plugin folder
cloned from a test repository, fakes on PATH) and its shared steps. The picker
is answered by tests/fake_picker.py, linked as omarchy-shell.
"""

import json
from pathlib import Path

from gherkin import step
from steps_updates import CONFIG_FILE, CONFIG_HOME

FAKE_PICKER = Path(__file__).resolve().parent / "fake_picker.py"
DEFAULT_TERMINAL_CHOICES = ["shell", "tmux", "herdr"]


def answer_picker(ctx, label: str, shift: bool) -> None:
    (ctx.fake_dir / "pick").write_text(json.dumps({"label": label, "shift": shift}))


def read_config() -> dict:
    assert CONFIG_FILE.exists(), f"there is no {CONFIG_FILE}"
    return json.loads(CONFIG_FILE.read_text())


def rules_table(ctx) -> list[dict]:
    head, *rows = ctx.step.table
    return [dict(zip(head, row)) for row in rows]


def write_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def open_link(ctx, url: str) -> None:
    ctx.begin_action()
    proc = ctx.run("browser", "open", *([url] if url else []))
    assert proc.returncode == 0, ctx.output()


# ------------------------------------------------------------------ Given

@step("Chromium has the profiles {first} and {second}")
def chromium_profiles(ctx, first, second):
    local_state = CONFIG_HOME / "chromium" / "Local State"
    local_state.parent.mkdir(parents=True, exist_ok=True)
    local_state.write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": first}, "Profile 1": {"name": second}}}}))
    shell = ctx.tmp / "bin" / "omarchy-shell"
    shell.unlink()
    shell.symlink_to(FAKE_PICKER)


@step("the config already has the rules:")
def config_rules(ctx):
    write_config({"browser": {"command": "chromium", "rules": rules_table(ctx)}})


@step("the config has update checks turned off and already has the rules:")
def config_checks_off_rules(ctx):
    write_config({"browser": {"command": "chromium", "rules": rules_table(ctx)}, "updates": {"check": False}})


@step("there is no config file")
def no_config(ctx):
    CONFIG_FILE.unlink(missing_ok=True)


@step("{label} was picked with Shift+Enter for {url}")
def was_picked_shift(ctx, label, url):
    picked_shift(ctx, label, url)


# ------------------------------------------------------------------ When

@step("a link to {url} opens the profile picker")
def link_to_opens_picker(ctx, url):
    open_link(ctx, url)
    assert ctx.summons(), f"the picker was not opened; output:\n{ctx.output()}"


@step("a link to {url} opens")
def link_to_opens(ctx, url):
    open_link(ctx, url)


@step("a new browser window opens the profile picker")
def new_window(ctx):
    open_link(ctx, "")
    assert ctx.summons(), f"the picker was not opened; output:\n{ctx.output()}"


@step("{label} is picked with Shift+Enter for the web app {url}")
def picked_shift_webapp(ctx, label, url):
    answer_picker(ctx, label, True)
    ctx.begin_action()
    proc = ctx.run("webapp", "open", url)
    assert proc.returncode == 0, ctx.output()


@step("{label} is picked with Shift+Enter for {url}")
def picked_shift(ctx, label, url):
    answer_picker(ctx, label, True)
    open_link(ctx, url)


@step("{label} is picked with Enter for {url}")
def picked_enter(ctx, label, url):
    answer_picker(ctx, label, False)
    open_link(ctx, url)


# ------------------------------------------------------------------ Then

@step("the picker offers to remember the profile for {host}")
def offers_remember(ctx, host):
    payload = ctx.summons()[-1]
    assert payload.get("remember") == host, f"remember {payload.get('remember')!r}, expected {host!r}"


@step("the picker does not offer to remember the profile")
def no_remember(ctx):
    payload = ctx.summons()[-1]
    assert not payload.get("remember"), f"remember {payload.get('remember')!r}"


def chromium_runs(ctx) -> list[list[str]]:
    return [c["args"] for c in ctx.calls("chromium")]


@step("Chromium opens the web app {url} in {directory}")
def chromium_opens_webapp(ctx, url, directory):
    runs = chromium_runs(ctx)
    want = [f"--profile-directory={directory}", f"--app={url}"]
    assert any(all(w in run for w in want) for run in runs), f"no chromium {want}; ran {runs}"


@step("Chromium opens {url} in {directory}")
def chromium_opens(ctx, url, directory):
    runs = chromium_runs(ctx)
    want = [f"--profile-directory={directory}", url]
    assert any(all(w in run for w in want) for run in runs), f"no chromium {want}; ran {runs}"


@step("the config has the rules:")
def has_rules(ctx):
    rules = read_config().get("browser", {}).get("rules")
    assert rules == rules_table(ctx), f"rules {rules}"


@step("the config has no rules")
def has_no_rules(ctx):
    rules = read_config().get("browser", {}).get("rules", []) if CONFIG_FILE.exists() else []
    assert not rules, f"rules {rules}"


@step("the config still has update checks turned off")
def still_checks_off(ctx):
    assert read_config().get("updates") == {"check": False}, f"updates {read_config().get('updates')!r}"


@step("the config has the default terminal context")
def default_terminal(ctx):
    choices = read_config().get("contexts", {}).get("terminal", {}).get("choices", [])
    assert [c.get("id") for c in choices] == DEFAULT_TERMINAL_CHOICES, f"terminal choices {choices}"


@step("the profile picker is not shown")
def picker_not_shown(ctx):
    assert not ctx.summons(), f"the picker was shown: {ctx.summons()}"


@step("the profile picker is shown")
def picker_shown(ctx):
    assert ctx.summons(), f"the picker was not shown; output:\n{ctx.output()}"
