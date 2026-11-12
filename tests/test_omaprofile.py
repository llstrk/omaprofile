import io
import os
import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from omaprofile import cli, config as cfgmod, install as inst, policy


def setUpModule():
    # Uninstall and install clear this marker; never let a test reach the real one.
    tmp = tempfile.TemporaryDirectory()
    for patcher in (mock.patch.object(inst, "DECLINED_FILE", Path(tmp.name) / "setup-declined"),
                    mock.patch.object(inst, "CLEANUP_DIR", Path(tmp.name) / "cleanup"),
                    mock.patch.object(inst, "CLEANUP_LOCK", Path(tmp.name) / "cleanup.lock"),
                    mock.patch.object(cli, "CLEANUP_DIR", Path(tmp.name) / "cleanup"),
                    mock.patch.object(cli, "CLEANUP_LOCK", Path(tmp.name) / "cleanup.lock"),
                    mock.patch.object(policy, "MOVED_MARKER", Path(tmp.name) / "policy-moved"),
                    # No test may reach the real /etc policy folders.
                    mock.patch.dict("os.environ", {"OMAPROFILE_POLICY_ROOTS": str(Path(tmp.name) / "none")})):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(tmp.cleanup)
from omaprofile.browser import (
    NO_PROFILE,
    Browser,
    check_rules,
    current_handlers,
    desktop_exec_arg,
    desktop_mimeapps_handlers,
    restore_handlers,
    color_from_seed,
    desktop_mimeapps_lists,
    looks_like_url,
    set_desktop_mimeapps_list,
    split_args,
    url_host,
)
from omaprofile.contexts import Context, ContextError
from omaprofile.picker import Choice

CFG = {
    "browser": {"command": "chromium", "rules": [
        {"host": "github.com", "profile": "Work"},
        {"host": "*.github.com", "profile": "Work"},
        {"url": "https://x.com/*", "profile": "Profile 1"},
    ]},
    "contexts": {"terminal": {"title": "T", "choices": [
        {"id": "shell", "label": "Shell", "command": "omarchy launch terminal"},
        {"id": "tmux", "label": "tmux", "command": "omarchy launch terminal tmux"},
    ]}},
}

LOCAL_STATE = {"profile": {"info_cache": {
    "Profile 1": {"name": "Personal", "profile_color_seed": -806210},
    "Default": {"name": "Work", "profile_color_seed": -4340993},
    "Profile 2": {"name": "Hobby"},
}}}


class Helpers(unittest.TestCase):
    def test_color_from_seed(self):
        self.assertEqual(color_from_seed(-16711936), "#00ff00")
        self.assertEqual(color_from_seed(-4340993), "#bdc2ff")
        self.assertEqual(color_from_seed(None), "")

    def test_url_host(self):
        self.assertEqual(url_host("https://user@GitHub.com:8443/a/b?c"), "github.com")
        self.assertEqual(url_host("example.org/x"), "example.org")
        self.assertEqual(url_host("about:blank"), "")

    def test_looks_like_url(self):
        for s in ("https://a.b", "example.com", "localhost:3000", "about:blank", "file:///tmp/x"):
            self.assertTrue(looks_like_url(s), s)
        for s in ("bogus", "frobnicate", "terminal"):
            self.assertFalse(looks_like_url(s), s)

    def test_split_args(self):
        self.assertEqual(split_args(["--private", "https://a.b/"]), (["--incognito"], "https://a.b/"))
        self.assertEqual(split_args([]), ([], ""))

    def test_choice_spec(self):
        c = Choice.from_spec("Profile 1|Personal|#f3b2be||Profile 1")
        self.assertEqual((c.id, c.label, c.color, c.glyph, c.detail), ("Profile 1", "Personal", "#f3b2be", "", "Profile 1"))
        self.assertEqual(Choice.from_spec("x").label, "x")


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = Browser(CFG)
        for patcher in (mock.patch("pathlib.Path.read_text", return_value=json.dumps(LOCAL_STATE)),
                        mock.patch("pathlib.Path.exists", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_profiles_sorted_default_first(self):
        self.assertEqual([p.name for p in self.browser.profiles()], ["Work", "Personal", "Hobby"])
        self.assertEqual(self.browser.profiles()[2].color, "")

    def test_resolve_by_name_or_dir(self):
        self.assertEqual(self.browser.resolve("Personal").directory, "Profile 1")
        self.assertEqual(self.browser.resolve("Profile 2").name, "Hobby")

    def test_rules(self):
        self.assertEqual(self.browser.rule_match("https://github.com/x").directory, "Default")
        self.assertEqual(self.browser.rule_match("https://gist.github.com/x").directory, "Default")
        self.assertEqual(self.browser.rule_match("https://x.com/home").directory, "Profile 1")
        self.assertIsNone(self.browser.rule_match("https://example.com/"))

    def test_open_uses_rule_without_picker(self):
        with mock.patch.object(Browser, "launch") as launch, mock.patch("omaprofile.browser.pick") as picker:
            self.browser.open(["--private", "https://github.com/a"])
        picker.assert_not_called()
        profile, args = launch.call_args.args
        self.assertEqual(profile.directory, "Default")
        self.assertEqual(args, ["--incognito", "https://github.com/a"])

    def test_open_asks_picker_and_cancel_drops_link(self):
        with mock.patch.object(Browser, "launch") as launch, \
             mock.patch("omaprofile.browser.pick", return_value=None) as picker:
            self.browser.open(["https://example.com/"])
        launch.assert_not_called()
        self.assertEqual(picker.call_args.args[:2], ("Open with profile", "example.com"))


    def test_open_webapp_adds_app_flag(self):
        with mock.patch.object(Browser, "launch") as launch, \
             mock.patch("omaprofile.browser.pick", return_value="Profile 1") as picker:
            self.browser.open_webapp("https://example.com/", ["--x"])
        self.assertEqual(picker.call_args.args[:2], ("Open web app as", "example.com"))
        profile, args = launch.call_args.args
        self.assertEqual((profile.directory, args), ("Profile 1", ["--app=https://example.com/", "--x"]))


class ContextTests(unittest.TestCase):
    def test_unknown(self):
        with self.assertRaises(ContextError):
            Context("nope", CFG)

    def test_open_execs_chosen_command_with_args(self):
        with mock.patch("omaprofile.contexts.pick", return_value="tmux"), \
             mock.patch("omaprofile.contexts.detached") as detached:
            Context("terminal", CFG).open(["--cwd", "/tmp"])
        argv = detached.call_args.args[0]
        self.assertEqual(argv[:3], ["bash", "-c", 'omarchy launch terminal tmux "$@"'])
        self.assertEqual(argv[4:], ["--cwd", "/tmp"])

    def test_cancel_runs_nothing(self):
        with mock.patch("omaprofile.contexts.pick", return_value=None), \
             mock.patch("omaprofile.contexts.detached") as detached:
            Context("terminal", CFG).open([])
        detached.assert_not_called()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch("omaprofile.cli.cfgmod.load", return_value=CFG),
                        mock.patch("omaprofile.cli.notify"),
                        mock.patch("sys.stderr", new_callable=io.StringIO)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_bare_url_and_no_args_go_to_browser_open(self):
        with mock.patch.object(Browser, "open") as op:
            for argv in (["https://example.com/"], [], ["--incognito"], ["browser", "https://example.com/"]):
                cli.dispatch(argv)
        self.assertEqual([c.args[0] for c in op.call_args_list],
                         [["https://example.com/"], [], ["--incognito"], ["https://example.com/"]])

    def test_unknown_group_and_action_fail_without_picker(self):
        with mock.patch.object(Browser, "open") as op:
            self.assertEqual(cli.main(["bogus", "list"]), 1)
            self.assertEqual(cli.main(["browser", "frobnicate"]), 1)
        op.assert_not_called()

    def test_webapp_dispatch(self):
        with mock.patch.object(Browser, "open_webapp") as op:
            cli.dispatch(["webapp", "open", "https://x.com/", "--y"])
            self.assertEqual(cli.main(["webapp"]), 1)
        self.assertEqual(op.call_args.args, ("https://x.com/", ["--y"]))

    def test_context_dispatch(self):
        with mock.patch.object(Context, "open") as op:
            cli.dispatch(["terminal", "open", "x"])
            cli.dispatch(["terminal"])
        self.assertEqual([c.args[0] for c in op.call_args_list], [["x"], []])


class DefaultBrowserTests(unittest.TestCase):
    LIST = (
        "# Apps open links through the handler listed here.\n"
        "[Default Applications]\n"
        "x-scheme-handler/http=omarchy-browser.desktop\n"
        "x-scheme-handler/https=omarchy-browser.desktop\n"
        "x-scheme-handler/mailto=chromium.desktop\n"
        "\n[Added Associations]\n"
        "x-scheme-handler/https=omarchy-browser.desktop\n"
    )

    def test_desktop_lists_come_from_the_running_desktop(self):
        with mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "Hyprland:xdg-desktop-portal-hyprland"}), \
             mock.patch.object(cfgmod, "CONFIG_HOME", Path("/home/user/.config")):
            self.assertEqual(
                desktop_mimeapps_lists(),
                [Path("/home/user/.config/hyprland-mimeapps.list"),
                 Path("/home/user/.config/xdg-desktop-portal-hyprland-mimeapps.list")],
            )

    def test_only_the_running_desktops_own_keys_are_repointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hyprland-mimeapps.list"
            path.write_text(self.LIST)
            set_desktop_mimeapps_list(path, "omaprofile.desktop")
            text = path.read_text()
            set_desktop_mimeapps_list(Path(tmp) / "no-such-list", "omaprofile.desktop")

        self.assertIn("x-scheme-handler/http=omaprofile.desktop", text)
        self.assertIn("x-scheme-handler/https=omaprofile.desktop", text)
        self.assertIn("x-scheme-handler/mailto=chromium.desktop", text)  # not ours
        self.assertIn("# Apps open links", text)  # comments survive
        self.assertEqual(text.count("omaprofile.desktop"), 2)  # other sections untouched


if __name__ == "__main__":
    unittest.main()


class ChromiumDirCase(unittest.TestCase):
    """A fake Chromium config dir: Default is the source, Profile 1 and 2 are targets."""

    STORE = {"location": 1, "from_webstore": True}

    def setUp(self):
        from omaprofile import extensions
        self.ext = extensions
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "Local State").write_text(json.dumps(LOCAL_STATE))
        self.write(root, "Default", {
            "aaa": {**self.STORE, "path": "aaa/1.0_0"},
            "bbb": {**self.STORE, "path": "bbb/2.0_0"},
            "omarchy": {"location": 8, "from_webstore": False, "path": "/usr/share/x"},
            "pdf": {"location": 5, "from_webstore": False},
        }, manifests={"aaa": {"name": "Alpha"},
                      "bbb": {"name": "__MSG_appName__", "default_locale": "en"}},
           messages={"bbb": {"appname": {"message": "Beta"}}})
        self.write(root, "Profile 1", {"aaa": dict(self.STORE)})
        self.write(root, "Profile 2", {"aaa": dict(self.STORE), "bbb": dict(self.STORE)})
        self.exts = extensions.Extensions({"browser": {"configDir": str(root)}})

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write(root, profile, settings, manifests={}, messages={}, prefs=True):
        """A profile whose user installs are unpacked under Extensions/<id>/<version>/."""
        pdir = root / profile
        pdir.mkdir()
        if prefs:
            (pdir / "Preferences").write_text(json.dumps({"extensions": {"settings": settings}}))
        for ext_id, entry in settings.items():
            if entry.get("location") != 1:
                continue
            edir = pdir / "Extensions" / ext_id / "1.0_0"
            edir.mkdir(parents=True)
            manifest = manifests.get(ext_id, {"name": ext_id.upper()})
            manifest.setdefault("update_url", "https://clients2.google.com/service/update2/crx")
            (edir / "manifest.json").write_text(json.dumps(manifest))
            if ext_id in messages:
                (edir / "_locales" / "en").mkdir(parents=True)
                (edir / "_locales" / "en" / "messages.json").write_text(json.dumps(messages[ext_id]))



class ExtensionTests(ChromiumDirCase):
    def test_disk_is_ahead_of_preferences(self):
        """Chromium unpacks right away but writes Preferences up to minutes later."""
        root = Path(self.tmp.name)
        # Profile 1 just installed bbb: directory there, Preferences not yet written.
        edir = root / "Profile 1" / "Extensions" / "bbb" / "2.0_0"
        edir.mkdir(parents=True)
        (edir / "manifest.json").write_text(json.dumps({"name": "Beta"}))
        # Profile 2 just uninstalled bbb: directory gone, Preferences still lists it.
        shutil.rmtree(root / "Profile 2" / "Extensions" / "bbb")
        self.assertEqual([(p.directory, [e.id for e in m]) for p, m in self.exts.plan("all")],
                         [("Profile 2", ["bbb"])])

    def test_signature_changes_on_install(self):
        root = Path(self.tmp.name)
        before = self.ext.signature(root)
        time.sleep(0.01)
        (root / "Profile 2" / "Extensions" / "ccc").mkdir()
        self.assertNotEqual(before, self.ext.signature(root))

    def test_source_is_first_profile_and_only_store_installs_count(self):
        source, targets = self.exts.source_and_targets()
        self.assertEqual(source.directory, "Default")
        self.assertEqual([e.name for e in self.exts.wanted(source)], ["Alpha", "Beta"])
        self.assertEqual([p.directory for p in targets], ["Profile 1", "Profile 2"])

    def test_plan_all_skips_profiles_in_sync(self):
        plan = self.exts.plan("all")
        self.assertEqual([(p.directory, [e.id for e in m]) for p, m in plan], [("Profile 1", ["bbb"])])

    def test_plan_by_name_and_source_rejected(self):
        self.assertEqual(self.exts.plan("Hobby"), [])  # Profile 2, already in sync
        self.assertEqual(len(self.exts.plan("Personal")), 1)
        with self.assertRaises(self.ext.BrowserError):
            self.exts.plan("Work")

    def test_picker_cancel_returns_none(self):
        with mock.patch.object(self.ext, "pick", return_value=None) as p:
            self.assertIsNone(self.exts.sync(None))
        choices = p.call_args.args[2]
        self.assertEqual([c.id for c in choices], ["all", "Profile 1", "Profile 2"])
        self.assertEqual(choices[1].detail, "Beta")
        self.assertEqual(choices[2].detail, "in sync")

    def test_sync_opens_store_pages_in_target_profile(self):
        with mock.patch.object(self.ext, "pick", return_value="all"), \
             mock.patch.object(self.ext.shutil, "which", side_effect=lambda c: "/x" if c == "chromium" else None), \
             mock.patch.object(self.ext, "spawn") as spawn:
            self.exts.sync(None)
        spawn.assert_called_once_with(["chromium", "--profile-directory=Profile 1",
                                       "https://chromewebstore.google.com/detail/bbb"])

    def test_list_marks_missing(self):
        table = self.exts.list()
        self.assertRegex(table, r"Beta\s+source\s+MISSING\s+yes")
        self.assertNotIn("omarchy", table)


class ProfilePickerExtensionsRowTests(ChromiumDirCase):
    """The browser picker's "Manage extensions" row, on the same fake config dir."""

    def browser(self):
        return Browser({"browser": {"configDir": self.tmp.name}})

    def test_row_is_last_and_counts_missing(self):
        from omaprofile import browser as bmod
        with mock.patch.object(bmod, "pick", return_value=None) as p:
            self.browser().open(["https://example.com"])
        last = p.call_args.args[2][-1]
        self.assertEqual((last.id, last.label, last.detail),
                         (bmod.MANAGE_EXTENSIONS, "Manage extensions", "1 missing in other profiles"))

    def test_manage_then_reshows_picker_for_the_link(self):
        from omaprofile import browser as bmod
        b = self.browser()
        with mock.patch.object(bmod, "pick", side_effect=[bmod.MANAGE_EXTENSIONS, "Profile 2"]) as p, \
             mock.patch.object(Browser, "manage_extensions") as manage, \
             mock.patch.object(Browser, "launch") as launch:
            b.open(["https://example.com"])
        manage.assert_called_once()
        self.assertEqual(p.call_count, 2)
        self.assertEqual(launch.call_args.args[0].directory, "Profile 2")

    def test_manage_without_link_opens_nothing(self):
        from omaprofile import browser as bmod
        with mock.patch.object(bmod, "pick", return_value=bmod.MANAGE_EXTENSIONS), \
             mock.patch.object(Browser, "manage_extensions") as manage, \
             mock.patch.object(Browser, "launch") as launch:
            self.browser().open([])
        manage.assert_called_once()
        launch.assert_not_called()


class RefreshTests(ChromiumDirCase):
    """Chromium writes Local State late; the open picker must pick that up."""

    def test_pick_rewrites_choices_file_only_when_refresh_has_rows(self):
        from omaprofile import picker
        payloads, snapshots, answers = [], [], iter([None, [Choice("x", "X")], None])

        def fake_ipc(*args):
            if args[:2] == ("shell", "summon"):
                payloads.append(json.loads(args[3]))
            return "ok"

        def refresh():
            choices = payloads[0]["choicesFile"]
            snapshots.append(json.loads(Path(choices).read_text())[0]["id"])
            row = next(answers, StopIteration)
            if row is StopIteration:
                Path(payloads[0]["doneFile"]).touch()  # the user picks
                return None
            return row

        with mock.patch.object(picker, "_ipc", side_effect=fake_ipc), \
             mock.patch.object(picker, "REFRESH_EVERY", 0):
            picker.pick("T", "", [Choice("a")], refresh=refresh, timeout=5)
        self.assertEqual(snapshots, ["a", "a", "x", "x"])
        self.assertFalse(Path(payloads[0]["choicesFile"]).exists())  # cleaned up

    def test_pick_without_refresh_has_no_choices_file(self):
        from omaprofile import picker
        payloads = []

        def fake_ipc(*args):
            payloads.append(json.loads(args[3]))
            Path(payloads[0]["doneFile"]).touch()
            return "ok"

        with mock.patch.object(picker, "_ipc", side_effect=fake_ipc):
            picker.pick("T", "", [Choice("a")])
        self.assertNotIn("choicesFile", payloads[0])

    def test_browser_refresh_sees_a_late_rename(self):
        from omaprofile import browser as bmod
        root = Path(self.tmp.name)
        seen = {}

        def fake_pick(title, subtitle, choices, refresh=None):
            self.assertIsNone(refresh())  # nothing written yet
            state = json.loads((root / "Local State").read_text())
            state["profile"]["info_cache"]["Profile 2"]["name"] = "Renamed"
            time.sleep(0.01)
            (root / "Local State").write_text(json.dumps(state))
            seen["rows"] = refresh()
            return None

        with mock.patch.object(bmod, "pick", side_effect=fake_pick):
            Browser({"browser": {"configDir": self.tmp.name}}).open(["https://example.com"])
        self.assertIn("Renamed", [c.label for c in seen["rows"]])
        self.assertEqual(seen["rows"][-1].id, bmod.MANAGE_EXTENSIONS)


# ---------------------------------------------------------------- readiness fixes

class RuleTests(unittest.TestCase):
    def setUp(self):
        self.browser = Browser({"browser": {"rules": [
            {"url": "https://x.com/*", "profile": "Work"},
            {"host": "*.Example.org", "profile": "Work"},
        ]}})
        patcher = mock.patch.object(Browser, "resolve", side_effect=lambda w: w)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_url_only_rule_does_not_catch_hostless_links(self):
        for url in ("about:blank", "file:///tmp/a.html", "mailto:a@b"):
            self.assertIsNone(self.browser.rule_match(url), url)
        self.assertEqual(self.browser.rule_match("https://x.com/home"), "Work")

    def test_host_patterns_are_case_insensitive(self):
        self.assertEqual(self.browser.rule_match("https://WWW.example.org/"), "Work")

    def test_bad_rules_are_config_errors(self):
        for rules in ([{"host": "a.b"}], [{"profile": "Work"}], [{"host": "", "profile": "W"}], ["x"], {}):
            with self.assertRaises(cfgmod.ConfigError, msg=rules):
                check_rules(rules)


class FreshBrowserTests(unittest.TestCase):
    """Chromium never ran: no Local State, no profiles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.browser = Browser({"browser": {"configDir": self.tmp.name,
                                            "rules": [{"host": "a.b", "profile": "Work"}]}})

    def test_links_open_without_a_profile_flag(self):
        self.assertEqual(self.browser.profiles(), [])
        with mock.patch("omaprofile.browser.pick") as picker, \
             mock.patch("omaprofile.browser.shutil.which", return_value="/usr/bin/chromium"), \
             mock.patch("omaprofile.browser.detached") as detached:
            self.browser.open(["https://a.b/"])
        picker.assert_not_called()
        self.assertEqual(detached.call_args.args[0], ["chromium", "https://a.b/"])
        self.assertIs(self.browser.choose_profile("", "t"), NO_PROFILE)

    def test_corrupt_local_state_is_still_an_error(self):
        (Path(self.tmp.name) / "Local State").write_text("{")
        with self.assertRaises(cli.BrowserError):
            self.browser.profiles()


class MissingRuleProfileTests(unittest.TestCase):
    def test_rule_for_a_gone_profile_falls_back_to_the_picker(self):
        cfg = {"browser": {"rules": [{"host": "a.b", "profile": "Gone"}]}}
        with mock.patch("pathlib.Path.read_text", return_value=json.dumps(LOCAL_STATE)), \
             mock.patch("pathlib.Path.exists", return_value=True), \
             mock.patch("omaprofile.browser.notify") as notify, \
             mock.patch("omaprofile.browser.pick", return_value="Profile 1") as picker, \
             mock.patch.object(Browser, "launch") as launch:
            Browser(cfg).open(["https://a.b/"])
        self.assertIn("Gone", notify.call_args.args[1])
        picker.assert_called_once()
        self.assertEqual(launch.call_args.args[0].directory, "Profile 1")


class ContextValidationTests(unittest.TestCase):
    def test_unknown_key_names_the_choice(self):
        cfg = {"contexts": {"t": {"choices": [{"id": "a", "command": "true", "icon": "x"}]}}}
        with self.assertRaisesRegex(ContextError, r"choice 'a': unknown key\(s\) icon"):
            Context("t", cfg)

    def test_choice_needs_id_and_command(self):
        for choice in ({"id": "a"}, {"command": "true"}, "a"):
            with self.assertRaises(ContextError, msg=choice):
                Context("t", {"contexts": {"t": {"choices": [choice]}}})


class ConfigValidationTests(unittest.TestCase):
    def test_top_level_must_be_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for text in ("[]", '{"browser": []}', '{"contexts": "x"}'):
                path.write_text(text)
                with self.assertRaises(cfgmod.ConfigError, msg=text):
                    cfgmod.load(path)


class ErrorReportingTests(unittest.TestCase):
    def run_main(self, argv, tty):
        err = io.StringIO()
        err.isatty = lambda: tty
        with mock.patch("omaprofile.cli.cfgmod.load", return_value=CFG), \
             mock.patch("omaprofile.cli.notify") as notify, mock.patch("sys.stderr", err):
            code = cli.main(argv)
        return code, notify, err.getvalue()

    def test_errors_notify_when_nobody_sees_stderr(self):
        code, notify, err = self.run_main(["bogus"], tty=False)
        self.assertEqual(code, 1)
        self.assertIn("unknown group 'bogus'", notify.call_args.args[1])
        self.assertIn("unknown group 'bogus'", err)

    def test_errors_stay_in_the_terminal(self):
        code, notify, _ = self.run_main(["bogus"], tty=True)
        self.assertEqual(code, 1)
        notify.assert_not_called()

    def test_unexpected_exceptions_are_reported_not_raised(self):
        with mock.patch.object(Browser, "open", side_effect=KeyError("boom")):
            code, notify, err = self.run_main(["https://a.b/"], tty=False)
        self.assertEqual(code, 1)
        self.assertIn("internal error: KeyError", notify.call_args.args[1])
        self.assertIn("Traceback", err)


class DesktopEntryTests(unittest.TestCase):
    def test_exec_arg_quoting(self):
        self.assertEqual(desktop_exec_arg("/h/a/bin/omaprofile"), "/h/a/bin/omaprofile")
        self.assertEqual(desktop_exec_arg("/h/a b/omaprofile"), '"/h/a b/omaprofile"')
        self.assertEqual(desktop_exec_arg('/h/$x/omaprofile'), '"/h/\\\\$x/omaprofile"')


class InstallFixtureCase(unittest.TestCase):
    """Every path install touches, inside a temp home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        apps = self.root / "share" / "applications"
        apps.mkdir(parents=True)
        self.apps = apps
        hypr = self.root / "config" / "hypr"
        hypr.mkdir(parents=True)
        patches = {
            (cfgmod, "DESKTOP_FILE"): apps / cfgmod.DESKTOP_ID,
            (cfgmod, "STATE_FILE"): self.root / "state" / "install.json",
            (inst, "HYPRLAND_LUA"): hypr / "hyprland.lua",
            (inst, "HYPR_HOOK"): hypr / "omaprofile.lua",
            (cfgmod, "BIN_LINK"): Path("/home/me/.local/bin/omaprofile"),
        }
        for (mod, name), value in patches.items():
            patcher = mock.patch.object(mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class WebappRewriteTests(InstallFixtureCase):
    def test_round_trip_and_moved_checkout(self):
        app = self.apps / "X.desktop"
        original = "[Desktop Entry]\nName=X\nExec=omarchy-launch-webapp https://x.com/\n"
        app.write_text(original)
        self.assertEqual(inst.rewrite_webapps(True), [app])
        self.assertIn("Exec=/home/me/.local/bin/omaprofile webapp open https://x.com/", app.read_text())
        self.assertEqual(inst.rewrite_webapps(True), [])  # idempotent
        inst.rewrite_webapps(False)
        self.assertEqual(app.read_text(), original)
        # An entry left by a checkout at another path is still recognised.
        app.write_text("Exec=/old/place/bin/omaprofile webapp open https://x.com/\n")
        inst.rewrite_webapps(False)
        self.assertEqual(app.read_text(), "Exec=omarchy-launch-webapp https://x.com/\n")


class DesktopEntryTargetTests(InstallFixtureCase):
    def test_entries_use_the_bin_link_not_a_checkout_path_with_spaces(self):
        with mock.patch.object(inst, "LAUNCHER", Path("/home/me/My Code/omaprofile/bin/omaprofile")), \
             mock.patch("omaprofile.browser.set_default_browser"), \
             mock.patch("omaprofile.browser.subprocess.run"):
            from omaprofile.browser import register
            register(cfgmod.BIN_LINK)
            entry = cfgmod.DESKTOP_FILE.read_text()
        self.assertIn("Exec=/home/me/.local/bin/omaprofile browser open %U", entry)
        self.assertNotIn("My Code", entry)
        self.assertNotIn("My Code", inst.omaprofile_webapp_exec())


class HyprRequireTests(InstallFixtureCase):
    STOCK = (
        'dofile("bootstrap.lua")\n'
        "\n"
        "-- Load Omarchy defaults.\n"
        'require("default.hypr.omarchy")\n'
        "\n"
        'require("hypr.bindings")\n'
    )

    def test_added_before_defaults_and_removed_cleanly(self):
        lua = inst.HYPRLAND_LUA
        lua.write_text(self.STOCK)
        self.assertEqual(inst.add_hypr_require(), "added")
        text = lua.read_text()
        self.assertLess(text.index('require("hypr.omaprofile")'), text.index("-- Load Omarchy defaults."))
        self.assertEqual(inst.add_hypr_require(), "present")
        self.assertTrue(inst.remove_hypr_require())
        self.assertEqual(lua.read_text(), self.STOCK)

    def test_unknown_layout_asks_for_a_manual_edit(self):
        inst.HYPRLAND_LUA.write_text("-- custom\n")
        self.assertEqual(inst.add_hypr_require(), "manual")
        inst.HYPRLAND_LUA.unlink()
        self.assertEqual(inst.add_hypr_require(), "manual")


class HandlerRestoreTests(InstallFixtureCase):
    HYPR = ("# routing\n[Default Applications]\nx-scheme-handler/http=omarchy-browser.desktop\n"
            "x-scheme-handler/https=omarchy-browser.desktop\n")
    MAIN = ("[Default Applications]\ntext/html=chromium.desktop\nx-scheme-handler/http=chromium.desktop\n"
            "x-scheme-handler/https=chromium.desktop\nx-scheme-handler/mailto=chromium.desktop\n")

    def setUp(self):
        super().setUp()
        self.hypr = self.root / "config" / "hyprland-mimeapps.list"
        self.main = self.root / "config" / "mimeapps.list"
        for patcher in (mock.patch.object(cfgmod, "CONFIG_HOME", self.root / "config"),
                        mock.patch("omaprofile.browser.desktop_mimeapps_lists", return_value=[self.hypr])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def install_like(self):
        """What set_default_browser does to the files (xdg-mime simulated)."""
        from omaprofile.browser import set_desktop_mimeapps_list
        set_desktop_mimeapps_list(self.hypr, "omaprofile.desktop")
        text = self.main.read_text() if self.main.exists() else "[Default Applications]\n"
        for mime in ("text/html", "x-scheme-handler/http", "x-scheme-handler/https", "x-scheme-handler/about"):
            if f"{mime}=" in text:
                text = re.sub(rf"^{re.escape(mime)}=.*$", f"{mime}=omaprofile.desktop", text, flags=re.M)
            else:
                text += f"{mime}=omaprofile.desktop\n"
        self.main.write_text(text)

    def test_each_list_gets_exactly_its_own_handlers_back(self):
        self.hypr.write_text(self.HYPR)
        self.main.write_text(self.MAIN)
        with mock.patch("omaprofile.browser.default_browser", return_value="omarchy-browser.desktop"):
            saved = current_handlers()
        self.install_like()
        with mock.patch("omaprofile.browser.subprocess.run") as run:
            restore_handlers(json.loads(json.dumps(saved)), "fallback.desktop")
        run.assert_not_called()
        self.assertEqual(self.hypr.read_text(), self.HYPR)
        self.assertEqual(self.main.read_text(), self.MAIN)  # about= line omaprofile added is gone

    def test_list_created_by_install_gets_the_default(self):
        self.hypr.write_text(self.HYPR)
        with mock.patch("omaprofile.browser.default_browser", return_value="omarchy-browser.desktop"):
            saved = current_handlers()
        self.install_like()
        restore_handlers(saved, "fallback.desktop")
        self.assertNotIn("omaprofile", self.main.read_text())
        self.assertIn("x-scheme-handler/https=omarchy-browser.desktop", self.main.read_text())

    def test_handlers_changed_after_install_are_kept(self):
        self.main.write_text(self.MAIN)
        saved = {"default": "chromium.desktop", "lists": {str(self.main): desktop_mimeapps_handlers(self.main)}}
        self.install_like()
        self.main.write_text(self.main.read_text().replace("text/html=omaprofile.desktop", "text/html=firefox.desktop"))
        restore_handlers(saved, "fallback.desktop")
        self.assertIn("text/html=firefox.desktop", self.main.read_text())
        self.assertIn("x-scheme-handler/http=chromium.desktop", self.main.read_text())

    def test_no_record_uses_the_fallback_and_omarchys_router(self):
        self.hypr.write_text(self.HYPR)
        self.main.write_text(self.MAIN)
        self.install_like()
        (self.apps / "omarchy-browser.desktop").write_text("[Desktop Entry]\n")
        with mock.patch("omaprofile.browser.subprocess.run") as run:
            restore_handlers({}, "brave-browser.desktop")
        run.assert_not_called()
        self.assertEqual(self.hypr.read_text(), self.HYPR)
        self.assertIn("x-scheme-handler/https=brave-browser.desktop", self.main.read_text())
        self.assertNotIn("omaprofile", self.main.read_text())

    def test_record_taken_while_installed_is_not_restored_to_itself(self):
        self.hypr.write_text(self.HYPR)
        self.main.write_text(self.MAIN)
        self.install_like()  # an older version installed without a record
        with mock.patch("omaprofile.browser.default_browser", return_value="omaprofile.desktop"):
            saved = current_handlers()
        self.assertEqual(saved["default"], "")
        restore_handlers(saved, "chromium.desktop")  # no omarchy-browser.desktop installed here
        self.assertNotIn("omaprofile", self.hypr.read_text() + self.main.read_text())
        self.assertIn("x-scheme-handler/https=chromium.desktop", self.main.read_text())


class InstallGuardTests(unittest.TestCase):
    def test_install_outside_a_checkout_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(inst, "SRC_ROOT", Path(tmp)):
            with self.assertRaisesRegex(inst.InstallError, "not an omaprofile checkout"):
                inst.install()


class EnablePluginTests(unittest.TestCase):
    def test_retries_until_the_rescan_has_landed(self):
        unknown = mock.Mock(returncode=1, stderr="plugin 'dry.omaprofile' is not known", stdout="")
        ok = mock.Mock(returncode=0, stderr="", stdout="Enabled")
        with mock.patch("omaprofile.install.subprocess.run", side_effect=[mock.Mock(), unknown, unknown, ok]) as run, \
             mock.patch("omaprofile.install.time.sleep"):
            self.assertEqual(inst.enable_plugin(), "")
        self.assertEqual(run.call_count, 4)

    def test_gives_up_with_the_error(self):
        unknown = mock.Mock(returncode=1, stderr="not known", stdout="")
        with mock.patch("omaprofile.install.subprocess.run", return_value=unknown), \
             mock.patch("omaprofile.install.time.sleep"):
            self.assertEqual(inst.enable_plugin(attempts=3), "not known")


class PluginModeTests(unittest.TestCase):
    """install from a separate checkout copies the overlay; from the plugin folder it does not."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = Path(__file__).resolve().parent.parent
        self.plugins = self.root / "plugins" / "dry.omaprofile"
        patcher = mock.patch.object(cfgmod, "PLUGIN_DIR", self.plugins)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_checkout_elsewhere_copies_overlay_and_drops_the_old_flat_file(self):
        self.plugins.mkdir(parents=True)
        (self.plugins / "Omaprofile.qml").write_text("old")
        with mock.patch.object(inst, "SRC_ROOT", self.repo):
            self.assertFalse(inst.managed_by_omarchy())
            inst.copy_plugin()
        self.assertTrue((self.plugins / "manifest.json").is_file())
        self.assertTrue((self.plugins / "plugin" / "Omaprofile.qml").is_file())
        self.assertFalse((self.plugins / "Omaprofile.qml").exists())
        manifest = json.loads((self.plugins / "manifest.json").read_text())
        self.assertEqual(manifest["entryPoints"]["overlay"], "plugin/Omaprofile.qml")
        inst.remove_plugin_copy()
        self.assertFalse(self.plugins.exists())

    def test_never_overwrites_a_clone_made_by_omarchy_plugin_add(self):
        (self.plugins / ".git").mkdir(parents=True)
        with mock.patch.object(inst, "SRC_ROOT", self.repo), \
             self.assertRaisesRegex(inst.InstallError, "run install from it"):
            inst.copy_plugin()

    def test_running_from_the_plugin_folder_is_managed_by_omarchy(self):
        self.plugins.mkdir(parents=True)
        with mock.patch.object(inst, "SRC_ROOT", self.plugins):
            self.assertTrue(inst.managed_by_omarchy())

    def test_uninstall_leaves_an_omarchy_managed_plugin_on_disk(self):
        self.plugins.mkdir(parents=True)
        (self.plugins / "manifest.json").write_text("{}")
        with mock.patch.object(inst, "SRC_ROOT", self.plugins), \
             mock.patch.object(inst, "unregister"), mock.patch.object(inst, "rewrite_webapps"), \
             mock.patch.object(inst, "remove_hypr_require"), mock.patch.object(inst, "reload_hyprland"), \
             mock.patch.object(inst, "HYPR_HOOK", self.root / "hook.lua"), \
             mock.patch.object(cfgmod, "STATE_FILE", self.root / "state.json"), \
             mock.patch.object(cfgmod, "BIN_LINK", self.root / "bin" / "omaprofile"), \
             mock.patch("omaprofile.install.subprocess.run") as run:
            self.assertTrue(inst.uninstall())
        self.assertTrue((self.plugins / "manifest.json").exists())
        self.assertIn(["omarchy", "plugin", "disable", "dry.omaprofile"], [c.args[0] for c in run.call_args_list])

    def test_repository_root_is_a_valid_plugin(self):
        if not shutil.which("omarchy"):
            self.skipTest("omarchy not installed")
        import subprocess
        proc = subprocess.run(["omarchy", "plugin", "validate", str(self.repo)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


class ShimTests(unittest.TestCase):
    """~/.local/bin/omaprofile: runs the checkout, or keeps links working when it is gone."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.log = self.root / "log"
        _stub(self.stubs, "fakebrowser", f'printf "browser %s\\n" "$*" >> {self.log}')
        _stub(self.stubs, "notify-send", f'printf "notify %s\\n" "$*" >> {self.log}; echo copy')
        _stub(self.stubs, "wl-copy", f'printf "clipboard %s\\n" "$(cat)" >> {self.log}')
        self.launcher = self.root / ".config" / "omarchy" / "plugins" / "dry.omaprofile" / "bin" / "omaprofile"
        self.link = self.root / "bin" / "omaprofile"
        for patcher in (mock.patch.object(inst, "LAUNCHER", self.launcher),
                        mock.patch.object(cfgmod, "BIN_LINK", self.link),
                        mock.patch.object(cfgmod, "HOME", self.root),
                        mock.patch.object(inst, "repo_url", return_value="https://example.invalid/omaprofile.git"),
                        mock.patch.object(inst, "managed_by_omarchy", return_value=True),
                        mock.patch("omaprofile.install.cfgmod.load",
                                   return_value={"browser": {"command": "fakebrowser"}})):
            patcher.start()
            self.addCleanup(patcher.stop)
        inst.write_shim()

    def run_shim(self, *args, wait_for=""):
        import subprocess
        env = {"PATH": f"{self.stubs}:/usr/bin:/bin", "HOME": str(self.root)}
        subprocess.run([str(self.link), *args], env=env, check=True)
        for _ in range(100):  # the notification runs in the background
            log = self.log.read_text().splitlines() if self.log.exists() else []
            if not wait_for or any(line.startswith(wait_for) for line in log):
                return log
            time.sleep(0.02)
        return log

    def test_runs_the_checkout_when_present(self):
        self.launcher.parent.mkdir(parents=True)
        _stub(self.launcher.parent, "omaprofile", f'printf "omaprofile %s\\n" "$*" >> {self.log}')
        self.assertEqual(self.run_shim("browser", "open", "https://a.b/"), ["omaprofile browser open https://a.b/"])

    def test_missing_checkout_still_opens_links_and_offers_the_fix(self):
        log = self.run_shim("browser", "open", "--private", "https://a.b/", wait_for="clipboard")
        self.assertIn("browser --incognito https://a.b/", log)
        notice = next(line for line in log if line.startswith("notify"))
        self.assertIn("copy=Copy fix command", notice)
        self.assertIn("clipboard omarchy plugin add https://example.invalid/omaprofile.git"
                      " && ~/.config/omarchy/plugins/dry.omaprofile/bin/omaprofile uninstall"
                      " && omarchy plugin remove dry.omaprofile", log)

    def test_missing_checkout_web_apps_and_bare_urls(self):
        self.run_shim("webapp", "open", "https://yt.example/", "--x")
        log = self.run_shim("https://c.d/")
        for _ in range(100):  # both background notifications, before the folder goes
            if sum(line.startswith("notify") for line in log) == 2:
                break
            time.sleep(0.02)
            log = self.log.read_text().splitlines()
        browsers = [line for line in log if line.startswith("browser")]
        self.assertEqual(browsers, ["browser --app=https://yt.example/ --x", "browser https://c.d/"])

    def test_remove_only_touches_our_launcher(self):
        inst.remove_shim()
        self.assertFalse(self.link.exists())
        self.link.write_text("#!/bin/sh\necho someone else's\n")
        inst.remove_shim()
        self.assertTrue(self.link.exists())
        self.link.unlink()
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("")
        self.link.symlink_to(self.launcher)  # what 0.2.0 made
        inst.remove_shim()
        self.assertFalse(self.link.is_symlink())


class AutoInstallTests(unittest.TestCase):
    """`install --auto`, which the overlay runs from the folder `omarchy plugin add` cloned."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / ".config" / "omarchy" / "plugins" / "dry.omaprofile"
        (self.plugin / "hypr").mkdir(parents=True)
        (self.plugin / "hypr" / "omaprofile.lua").write_text("-- new hook\n")
        self.launcher = self.plugin / "bin" / "omaprofile"
        self.launcher.parent.mkdir()
        self.launcher.write_text("")
        self.state = self.root / "state" / "install.json"
        self.link = self.root / "bin" / "omaprofile"
        self.hook = self.root / "hypr" / "omaprofile.lua"
        self.reload = mock.MagicMock()
        for patcher in (mock.patch.object(inst, "SRC_ROOT", self.plugin),
                        mock.patch.object(inst, "LAUNCHER", self.launcher),
                        mock.patch.object(inst, "HYPR_HOOK", self.hook),
                        mock.patch.object(inst, "reload_hyprland", self.reload),
                        mock.patch.object(inst, "repo_url", return_value=""),
                        mock.patch.object(cfgmod, "PLUGIN_DIR", self.plugin),
                        mock.patch.object(cfgmod, "STATE_FILE", self.state),
                        mock.patch.object(cfgmod, "BIN_LINK", self.link),
                        mock.patch.object(cfgmod, "HOME", self.root),
                        mock.patch("omaprofile.install.cfgmod.load",
                                   return_value={"browser": {"command": "chromium"}})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_asks_until_installed_or_declined(self):
        with mock.patch.object(inst, "DECLINED_FILE", self.root / "state" / "setup-declined"):
            self.assertEqual(inst.setup_state(), "ask")
            inst.decline_setup()
            self.assertEqual(inst.setup_state(), "")

    def test_a_checkout_that_is_not_the_plugin_folder_does_nothing(self):
        with mock.patch.object(inst, "managed_by_omarchy", return_value=False):
            self.assertEqual(inst.setup_state(), "")

    def test_installed_already_leaves_links_alone_and_refreshes_our_files(self):
        self.state.parent.mkdir()
        self.state.write_text("{}")
        inst.write_shim()
        self.hook.parent.mkdir()
        self.hook.write_text("-- old hook\n")
        with mock.patch.object(inst, "install") as install, mock.patch.object(inst, "shim_text", return_value="#!/bin/sh\n" + inst.SHIM_MARK + "\ntarget=" + str(self.launcher) + "\n# new\n"):
            self.assertEqual(inst.setup_state(), "")
            self.assertFalse(inst.refresh())  # nothing left to do
        install.assert_not_called()
        self.assertIn("# new", self.link.read_text())
        self.assertEqual(self.hook.read_text(), "-- new hook\n")
        self.reload.assert_called_once()

    def test_refresh_leaves_another_checkouts_launcher_and_a_removed_hook(self):
        self.state.parent.mkdir()
        self.state.write_text("{}")
        other = self.root / "Work" / "omaprofile" / "bin" / "omaprofile"
        other.parent.mkdir(parents=True)
        other.write_text("")
        self.link.parent.mkdir()
        self.link.symlink_to(other)
        self.assertEqual(inst.setup_state(), "")
        self.assertEqual(self.link.resolve(), other)
        self.assertFalse(self.hook.exists())

    def test_refresh_replaces_the_symlink_older_versions_made(self):
        self.state.parent.mkdir()
        self.state.write_text("{}")
        self.link.parent.mkdir()
        self.link.symlink_to(self.launcher)
        self.assertEqual(inst.setup_state(), "")
        self.assertFalse(self.link.is_symlink())
        self.assertEqual(inst.shim_target(), self.launcher)

    def test_install_and_uninstall_forget_dont_ask_again(self):
        declined = self.root / "state" / "setup-declined"
        with mock.patch.object(inst, "DECLINED_FILE", declined):
            inst.decline_setup()
            with mock.patch.object(inst, "unregister"), mock.patch.object(inst, "rewrite_webapps"), \
                 mock.patch.object(inst, "remove_hypr_require"), mock.patch("omaprofile.install.subprocess.run"):
                inst.uninstall()
            self.assertFalse(declined.exists())

    def test_overlay_runs_it_from_its_own_folder(self):
        qml = (Path(__file__).resolve().parent.parent / "plugin" / "Omaprofile.qml").read_text()
        self.assertIn('exec "$1" install --auto', qml)
        self.assertIn('dir + "/bin/omaprofile"', qml)


class AutoInstallCommandTests(unittest.TestCase):
    def run_auto(self, choice, state="ask"):
        with mock.patch("omaprofile.cli.setup_state", return_value=state), \
             mock.patch("omaprofile.cli.pick", return_value=choice) as pick, \
             mock.patch("omaprofile.cli.install", return_value=("added", "")) as install, \
             mock.patch("omaprofile.cli.decline_setup") as decline, \
             mock.patch("omaprofile.cli.notify") as notify, \
             mock.patch("omaprofile.cli.default_browser", return_value="omaprofile.desktop"), \
             mock.patch("omaprofile.cli.ask_policy") as self.ask_policy:
            cli.dispatch(["install", "--auto"])
        return pick, install, decline, notify

    def test_popup_lists_the_changes_and_installs_on_yes(self):
        pick, install, decline, notify = self.run_auto("install")
        self.assertIn("default browser for http, https", pick.call_args.kwargs["body"])
        self.assertEqual([c.id for c in pick.call_args.args[2]], ["install", "later", "never"])
        install.assert_called_once_with(enable=False)
        self.assertEqual(notify.call_args.args[0], "omaprofile is set up")
        self.ask_policy.assert_called_once()  # the theme question comes last
        decline.assert_not_called()

    def test_not_now_or_escape_changes_nothing(self):
        for choice in ("later", None):
            _, install, decline, notify = self.run_auto(choice)
            install.assert_not_called()
            decline.assert_not_called()
            notify.assert_not_called()

    def test_dont_ask_again_is_remembered(self):
        _, install, decline, _ = self.run_auto("never")
        install.assert_not_called()
        decline.assert_called_once()

    def test_no_popup_once_installed(self):
        pick, _, _, _ = self.run_auto("install", state="")
        pick.assert_not_called()


class PolicyTests(unittest.TestCase):
    """Omarchy's managed Chromium policy moved to recommended and back, by the real scripts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "chromium" / "policies"
        self.managed = self.root / "managed"
        self.managed.mkdir(parents=True)
        (self.managed / "omarchy.json").write_text('{"DefaultBrowserSettingEnabled": false}\n')
        (self.managed / "color.json").write_text('{"BrowserThemeColor": "#1c2027"}\n')
        patcher = mock.patch.dict("os.environ", {"OMAPROFILE_POLICY_ROOTS": f"{self.root}:{Path(tmp.name) / 'brave'}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recommend_moves_omarchy_json_and_drops_the_managed_folder(self):
        self.assertEqual(policy.managed_roots(), [self.root])
        self.assertEqual(policy.recommend(), [self.root])
        self.assertFalse(self.managed.exists())  # Omarchy's theme writer skips a missing folder
        recommended = self.root / "recommended"
        self.assertIn("DefaultBrowserSettingEnabled", (recommended / "omarchy.json").read_text())
        self.assertIn("BrowserThemeColor", (recommended / "color.json").read_text())  # a new profile's default
        self.assertTrue(policy.MOVED_MARKER.exists())
        self.assertEqual(policy.managed_roots(), [])
        self.assertEqual(policy.recommend(), [])  # nothing left to move

    def test_restore_puts_it_back(self):
        policy.recommend()
        self.assertEqual(policy.restore(), [self.root])
        self.assertEqual(sorted(p.name for p in self.managed.iterdir()), ["color.json", "omarchy.json"])
        self.assertFalse((self.root / "recommended").exists())
        self.assertFalse(policy.MOVED_MARKER.exists())

    def test_keeps_an_administrators_other_managed_files(self):
        (self.managed / "admin.json").write_text("{}")
        policy.recommend()
        self.assertEqual(sorted(p.name for p in self.managed.iterdir()), ["admin.json"])

    def run_setup(self, choice):
        with mock.patch("omaprofile.cli.pick", return_value=choice) as pick, \
             mock.patch("omaprofile.cli.notify") as notify:
            cli.ask_policy()
        return pick, notify

    def test_last_setup_step_defaults_to_no(self):
        for choice in ("keep", None):
            pick, _ = self.run_setup(choice)
            self.assertEqual(pick.call_args.args[2][0].id, "keep")  # first row, selected
            self.assertIn("theme", pick.call_args.kwargs["body"])
            self.assertTrue((self.managed / "omarchy.json").exists())

    def test_last_setup_step_moves_it_on_yes(self):
        _, notify = self.run_setup("move")
        self.assertFalse(self.managed.exists())
        notify.assert_not_called()

    def test_not_asked_when_already_recommended(self):
        policy.recommend()
        pick, _ = self.run_setup("move")
        pick.assert_not_called()

    def test_uninstall_restores_only_what_omaprofile_moved(self):
        policy.recommend()
        with mock.patch("omaprofile.cli.uninstall", return_value=True), \
             mock.patch("omaprofile.cli.default_browser", return_value="chromium.desktop"), \
             mock.patch("sys.stdout", new=io.StringIO()):
            cli.dispatch(["uninstall"])
        self.assertTrue((self.managed / "omarchy.json").is_file())
        policy.recommend()
        policy.MOVED_MARKER.unlink()  # as if moved by hand
        with mock.patch("omaprofile.cli.uninstall", return_value=True), \
             mock.patch("omaprofile.cli.default_browser", return_value="chromium.desktop"), \
             mock.patch("sys.stdout", new=io.StringIO()):
            cli.dispatch(["uninstall"])
        self.assertFalse(self.managed.exists())


class RemovedPluginTests(unittest.TestCase):
    """`omarchy plugin remove` before `omaprofile uninstall`: cleaned up from the copy."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / "plugins" / "dry.omaprofile"
        self.plugin.mkdir(parents=True)
        (self.plugin / "manifest.json").write_text("{}")
        self.state = self.root / "state" / "install.json"
        self.state.parent.mkdir()
        self.state.write_text("{}")
        self.cleanup_dir = self.root / "share" / "omaprofile" / "cleanup"
        for patcher in (mock.patch.object(cfgmod, "PLUGIN_DIR", self.plugin),
                        mock.patch.object(cfgmod, "STATE_FILE", self.state),
                        mock.patch.object(inst, "CLEANUP_DIR", self.cleanup_dir),
                        mock.patch.object(cli, "CLEANUP_DIR", self.cleanup_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cleanup_copy_holds_the_cli_and_follows_updates(self):
        self.assertTrue(inst.write_cleanup_copy())
        self.assertTrue(os.access(self.cleanup_dir / "bin" / "omaprofile", os.X_OK))
        self.assertTrue((self.cleanup_dir / "omaprofile" / "install.py").is_file())
        self.assertFalse(inst.write_cleanup_copy())  # unchanged
        inst.remove_cleanup_copy()
        self.assertFalse(self.cleanup_dir.parent.exists())

    def test_only_a_removed_plugin_with_an_install_record_counts(self):
        self.assertFalse(inst.plugin_was_removed())  # still there (shell restart, disable)
        (self.plugin / "manifest.json").unlink()
        self.assertTrue(inst.plugin_was_removed())
        self.state.unlink()  # uninstalled properly first
        self.assertFalse(inst.plugin_was_removed())

    def run_removed(self, answer="undo"):
        with mock.patch("omaprofile.cli.uninstall") as uninstall, mock.patch("omaprofile.cli.notify") as notify, \
             mock.patch("omaprofile.cli.ask_removal", return_value=answer) as ask, \
             mock.patch("omaprofile.cli.removal_asked_marker", return_value=self.root / "asked"):
            cli.dispatch(["uninstall", "--removed"])
        return uninstall, notify, ask

    def test_undo_uninstalls_without_touching_the_plugin(self):
        (self.plugin / "manifest.json").unlink()
        inst.write_cleanup_copy()
        uninstall, notify, _ = self.run_removed("undo")
        uninstall.assert_called_once_with(removed=True)
        self.assertEqual(notify.call_args.args[0], "omaprofile uninstalled")
        self.assertFalse(self.cleanup_dir.exists())

    def test_leave_changes_nothing_and_waits_for_the_next_login(self):
        (self.plugin / "manifest.json").unlink()
        uninstall, notify, ask = self.run_removed("leave")
        uninstall.assert_not_called()
        notify.assert_not_called()
        self.assertTrue((self.root / "asked").exists())
        _, _, ask = self.run_removed("undo")
        ask.assert_not_called()  # not again this login

    def test_no_shell_to_ask_means_ask_next_time(self):
        (self.plugin / "manifest.json").unlink()
        uninstall, _, _ = self.run_removed("")
        uninstall.assert_not_called()
        self.assertFalse((self.root / "asked").exists())

    def test_removed_mode_does_nothing_while_the_plugin_is_there(self):
        uninstall, notify, ask = self.run_removed()
        ask.assert_not_called()
        uninstall.assert_not_called()

    def run_prompt(self, gum_output: str):
        """The floating terminal's script, with a stubbed gum that answers `gum_output`."""
        import subprocess
        stubs = self.root / "stubs"
        stubs.mkdir(exist_ok=True)
        _stub(stubs, "gum", f'[ "$1" = choose ] && printf "{gum_output}"; [ "$1" = choose ] && [ -z "{gum_output}" ] && exit 130; exit 0')

        real_popen = subprocess.Popen

        def fake_terminal(argv, **kwargs):
            if argv[0] != "xdg-terminal-exec":
                return real_popen(argv, **kwargs)  # subprocess.run below goes through Popen too
            script_args = argv[argv.index("-e") + 1:]
            subprocess.run(script_args, env={"PATH": f"{stubs}:/usr/bin:/bin"}, stdout=subprocess.DEVNULL)
            return mock.Mock()

        with mock.patch("omaprofile.cli.shutil.which", return_value="/usr/bin/xdg-terminal-exec"), \
             mock.patch("omaprofile.cli.subprocess.Popen", side_effect=fake_terminal) as popen:
            answer = cli.ask_removal(timeout=5)
        return answer, popen.call_args_list[0].args[0]

    def test_question_is_a_floating_omarchy_terminal(self):
        answer, argv = self.run_prompt("Undo its changes")
        self.assertEqual(answer, "undo")
        self.assertEqual(argv[:3], ["xdg-terminal-exec", "--app-id=org.omarchy.terminal", "--title=omaprofile"])
        self.assertEqual(len(argv), 5)  # one space-free path after -e

    def test_leave_and_escape_both_leave(self):
        self.assertEqual(self.run_prompt("Leave them for now")[0], "leave")
        self.assertEqual(self.run_prompt("")[0], "leave")  # Esc: gum exits 130, the trap still answers

    def test_no_terminal_means_ask_next_time(self):
        with mock.patch("omaprofile.cli.shutil.which", return_value=None):
            self.assertEqual(cli.ask_removal(), "")

    def test_removed_uninstall_skips_plugin_disable_and_delete(self):
        (self.plugin / "manifest.json").unlink()
        with mock.patch.object(inst, "unregister"), mock.patch.object(inst, "rewrite_webapps"), \
             mock.patch.object(inst, "remove_hypr_require"), mock.patch.object(inst, "reload_hyprland"), \
             mock.patch.object(inst, "remove_plugin_copy") as remove_copy, \
             mock.patch.object(inst, "HYPR_HOOK", self.root / "hook.lua"), \
             mock.patch.object(cfgmod, "BIN_LINK", self.root / "bin" / "omaprofile"), \
             mock.patch("omaprofile.install.subprocess.run") as run:
            inst.uninstall(removed=True)
        self.assertEqual(run.call_args_list, [])
        remove_copy.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_launcher_hands_over_to_the_cleanup_copy(self):
        import subprocess
        stubs = self.root / "stubs"
        stubs.mkdir()
        log = self.root / "log"
        _stub(stubs, "fakebrowser", f'printf "browser %s\\n" "$*" >> {log}')
        cleanup = self.cleanup_dir / "bin" / "omaprofile"
        cleanup.parent.mkdir(parents=True)
        _stub(cleanup.parent, "omaprofile", f'printf "cleanup %s\\n" "$*" >> {log}')
        link = self.root / "bin" / "omaprofile"
        with mock.patch.object(inst, "LAUNCHER", self.plugin / "bin" / "omaprofile"), \
             mock.patch.object(cfgmod, "BIN_LINK", link), mock.patch.object(inst, "repo_url", return_value=""), \
             mock.patch("omaprofile.install.cfgmod.load", return_value={"browser": {"command": "fakebrowser"}}):
            inst.write_shim()
        subprocess.run([str(link), "https://a.b/"], env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(self.root)},
                       check=True)
        for _ in range(100):
            lines = log.read_text().splitlines() if log.exists() else []
            if len(lines) == 2:
                break
            time.sleep(0.02)
        self.assertEqual(sorted(lines), ["browser https://a.b/", "cleanup uninstall --removed"])


class RemovalWatchTests(unittest.TestCase):
    """The overlay's watch when it unloads, run as the overlay runs it."""

    def setUp(self):
        qml = (Path(__file__).resolve().parent.parent / "plugin" / "Omaprofile.qml").read_text()
        block = qml[qml.index("removalWatchScript: ["):]
        block = block[:block.index('].join("\\n")')]
        # Each line is a single-quoted JS string; undo its escapes as QML does.
        self.script = "\n".join(line.strip().rstrip(",")[1:-1].replace("\\\\", "\\")
                                for line in block.splitlines()[1:] if line.strip())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        (self.plugin / "manifest.json").write_text("{}")
        self.state = self.root / ".local" / "state" / "omaprofile" / "install.json"
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{}")
        self.log = self.root / "log"
        cleanup = self.root / ".local" / "share" / "omaprofile" / "cleanup" / "bin"
        cleanup.mkdir(parents=True)
        _stub(cleanup, "omaprofile", f'printf "%s\\n" "$*" >> {self.log}')

    def watch(self, during=None):
        import subprocess
        proc = subprocess.Popen(["sh", "-c", self.script, "omaprofile-removal-watch", str(self.plugin)],
                                env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)})
        if during:
            during()
        proc.wait(timeout=15)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_folder_removed_while_installed_runs_the_cleanup(self):
        self.assertEqual(self.watch(during=lambda: shutil.rmtree(self.plugin)), ["uninstall --removed"])

    def test_uninstalled_first_does_nothing(self):
        self.state.unlink()
        self.assertEqual(self.watch(during=lambda: shutil.rmtree(self.plugin)), [])

    def test_folder_left_in_place_ends_quietly(self):
        script = self.script.replace("-lt 20", "-lt 2")  # a restart or disable: wait it out quickly
        self.script = script
        self.assertEqual(self.watch(), [])


class InstallOrderTests(unittest.TestCase):
    def test_plugin_is_enabled_after_links_point_at_omaprofile(self):
        order = []
        names = ("write_shim", "copy_plugin", "save_handlers", "register", "rewrite_webapps",
                 "add_hypr_require", "reload_hyprland", "enable_plugin")
        patches = [mock.patch.object(inst, n, side_effect=lambda *a, n=n, **k: order.append(n) or ("" if n == "enable_plugin" else None))
                   for n in names]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(inst, "HYPR_HOOK", Path(tmp) / "hook.lua"), \
             mock.patch.object(cfgmod, "CONFIG_FILE", Path(tmp) / "config.json"), \
             mock.patch.object(cfgmod, "write_default"), mock.patch.object(inst, "managed_by_omarchy", return_value=False), \
             mock.patch.object(inst, "DECLINED_FILE", Path(tmp) / "setup-declined"):
            for p in patches:
                p.start()
            try:
                inst.install()
            finally:
                for p in patches:
                    p.stop()
        self.assertLess(order.index("register"), order.index("enable_plugin"))
        self.assertEqual(order[-1], "enable_plugin")

    def test_uninstall_disables_the_overlay_before_restoring_links(self):
        order = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(inst, "unregister", side_effect=lambda *a: order.append("unregister")), \
             mock.patch("omaprofile.install.subprocess.run",
                        side_effect=lambda argv, **k: order.append(" ".join(argv[:3]))), \
             mock.patch.object(inst, "rewrite_webapps"), mock.patch.object(inst, "remove_hypr_require"), \
             mock.patch.object(inst, "reload_hyprland"), mock.patch.object(inst, "remove_shim"), \
             mock.patch.object(inst, "remove_plugin_copy"), mock.patch.object(inst, "HYPR_HOOK", Path(tmp) / "h.lua"), \
             mock.patch.object(cfgmod, "STATE_FILE", Path(tmp) / "install.json"):
            inst.uninstall()
        self.assertLess(order.index("omarchy plugin disable"), order.index("unregister"))
