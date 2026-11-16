"""The rules editor's edges that rules-editor.feature does not spell out."""
import json
import tempfile
import unittest
from pathlib import Path

from sandbox import SANDBOX  # noqa: F401  - the throwaway home, before omaprofile is imported

from omaprofile import config as cfgmod  # noqa: E402
from omaprofile import rules  # noqa: E402


class EditorCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="omaprofile-rules-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "config.json"
        self.chromium = self.dir / "chromium"
        self.profiles({"Default": "Personal", "Profile 1": "Work"})

    def profiles(self, names: dict) -> None:
        self.chromium.mkdir(exist_ok=True)
        (self.chromium / "Local State").write_text(json.dumps({"profile": {"info_cache": {
            d: {"name": n, "profile_color_seed": -16776961} for d, n in names.items()}}}))

    def write(self, rule_list, **extra) -> None:
        self.path.write_text(json.dumps({"browser": {"rules": rule_list}, **extra}))

    def saved(self) -> list[dict]:
        return json.loads(self.path.read_text())["browser"]["rules"]

    def editor(self) -> rules.Editor:
        return rules.Editor({"browser": {"configDir": str(self.chromium)}}, self.path)

    def keys(self, ed, *keys) -> None:
        for k in keys:
            for key in ([k] if len(k) > 1 and not k.startswith("=") else list(k.lstrip("="))):
                ed.handle(key)


class PatternTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(rules.check_pattern(" GitHub.com "), ("host", "github.com"))
        self.assertEqual(rules.check_pattern("*.github.com"), ("host", "*.github.com"))
        self.assertEqual(rules.check_pattern("https://X.com/*"), ("url", "https://X.com/*"))

    def test_refused(self):
        for text, says in (("", "Type a host"), ("git hub.com", "no spaces"),
                           ("github.com/llstrk/", "https://github.com/llstrk/*")):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, says):
                rules.check_pattern(text)

    def test_keys(self):
        self.assertEqual(rules.parse_keys("ab\x1b[A\x1b[B\x1bOA\x1b[3~\r\x7f\x15J"),
                         ["a", "b", "up", "down", "up", "delete", "enter", "backspace", "ctrl+u", "J"])
        self.assertEqual(rules.parse_keys("\x1b"), ["esc"])


class EditorTests(EditorCase):
    def test_changing_a_host_and_url_rule_keeps_the_other_half(self):
        self.write([{"host": "x.com", "url": "https://x.com/i/*", "profile": "Work"}])
        ed = self.editor()
        self.keys(ed, "enter", "ctrl+u", "=y.com", "enter", "enter")
        self.assertEqual(self.saved(), [{"host": "y.com", "url": "https://x.com/i/*", "profile": "Work"}])

    def test_other_keys_of_a_rule_are_kept(self):
        self.write([{"host": "x.com", "profile": "Work", "comment": "mine"}])
        ed = self.editor()
        self.keys(ed, "enter", "enter", "k", "enter")
        self.assertEqual(self.saved(), [{"host": "x.com", "profile": "Personal", "comment": "mine"}])

    def test_changing_a_rule_to_another_rules_pattern_leaves_one(self):
        self.write([{"host": "a.com", "profile": "Work"}, {"host": "b.com", "profile": "Work"}])
        ed = self.editor()
        self.keys(ed, "j", "enter", "ctrl+u", "=a.com", "enter", "k", "enter")
        self.assertEqual(self.saved(), [{"host": "a.com", "profile": "Personal"}])

    def test_a_rule_deleted_elsewhere_is_not_resurrected(self):
        self.write([{"host": "a.com", "profile": "Work"}, {"host": "b.com", "profile": "Work"}])
        ed = self.editor()
        self.write([{"host": "b.com", "profile": "Work"}])
        self.keys(ed, "d", "y")
        self.assertEqual(self.saved(), [{"host": "b.com", "profile": "Work"}])
        self.assertIn("changed outside the editor", ed.status())
        self.assertEqual([r["pattern"] for r in ed.rows()], ["b.com"])

    def test_two_profiles_with_one_name_store_the_directory(self):
        self.profiles({"Default": "Work", "Profile 1": "Work"})
        ed = self.editor()
        self.keys(ed, "a", "=a.com", "enter", "j", "enter")
        self.assertEqual(self.saved(), [{"host": "a.com", "profile": "Profile 1"}])

    def test_a_digit_picks_the_profile(self):
        ed = self.editor()
        self.keys(ed, "a", "=a.com", "enter", "2")
        self.assertEqual(self.saved(), [{"host": "a.com", "profile": "Work"}])

    def test_the_rest_of_the_config_and_its_mode_are_kept(self):
        self.write([], contexts={"x": {"choices": []}})
        self.path.chmod(0o600)
        ed = self.editor()
        self.keys(ed, "a", "=a.com", "enter", "enter")
        self.assertEqual(json.loads(self.path.read_text())["contexts"], {"x": {"choices": []}})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_a_broken_config_is_an_error_not_an_empty_list(self):
        self.path.write_text("{")
        with self.assertRaises(cfgmod.ConfigError):
            self.editor()

    def test_moving_past_the_ends_does_nothing(self):
        self.write([{"host": "a.com", "profile": "Work"}, {"host": "b.com", "profile": "Work"}])
        ed = self.editor()
        self.keys(ed, "K", "j", "J")
        self.assertEqual([r["host"] for r in self.saved()], ["a.com", "b.com"])
        self.assertEqual(ed.cursor, 1)


class ScreenTests(EditorCase):
    def test_a_long_list_scrolls_to_the_cursor_and_fits(self):
        self.write([{"host": f"site{i}.com", "profile": "Work"} for i in range(60)])
        ed = self.editor()
        self.keys(ed, "G")
        lines = ed.lines(40, 20)
        self.assertEqual(len(lines), 20)
        self.assertTrue(all(len(t) <= 40 for t, _ in lines))
        self.assertTrue(any("Add a rule" in t and s == "cursor" for t, s in lines))
        self.keys(ed, "g")
        self.assertTrue(any("site0.com" in t and s == "cursor" for t, s in ed.lines(40, 20)))

    def test_theme_colours_or_plain_without_a_theme(self):
        colors = self.dir / "colors.toml"
        colors.write_text('accent = "#7aa2f7"\nyellow = "#e0af68"\nselection = "#292e42"\n')
        themed = rules.Theme(colors)
        self.assertIn("38;2;122;162;247", themed.styles["accent"])
        self.assertIn("38;2;224;175;104", themed.styles["warn"])
        plain = rules.Theme(self.dir / "missing.toml")
        self.assertEqual(plain.styles["warn"], "\x1b[33m")


class DesktopEntryRefreshTests(unittest.TestCase):
    def setUp(self):
        from omaprofile import install
        self.install = install
        self.entry = cfgmod.DESKTOP_FILE
        self.entry.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.entry.unlink, missing_ok=True)

    def test_an_entry_that_is_not_ours_is_left_alone(self):
        self.entry.write_text("[Desktop Entry]\nName=Something else\nExec=/usr/bin/other %U\n")
        self.assertFalse(self.install.refresh_desktop_entry())
        self.assertIn("other", self.entry.read_text())

    def test_a_current_entry_is_not_rewritten(self):
        from omaprofile.browser import desktop_entry
        self.entry.write_text(desktop_entry(cfgmod.BIN_LINK))
        self.assertFalse(self.install.refresh_desktop_entry())

    def test_no_entry_is_not_created(self):
        self.entry.unlink(missing_ok=True)
        self.assertFalse(self.install.refresh_desktop_entry())
        self.assertFalse(self.entry.exists())


if __name__ == "__main__":
    unittest.main()
