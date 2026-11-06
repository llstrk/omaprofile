import json
import unittest
from unittest import mock

from omaprofile import cli
from omaprofile.browser import Browser, color_from_seed, looks_like_url, split_args, url_host
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
        patcher = mock.patch("pathlib.Path.read_text", return_value=json.dumps(LOCAL_STATE))
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
        patcher = mock.patch("omaprofile.cli.cfgmod.load", return_value=CFG)
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


if __name__ == "__main__":
    unittest.main()
