import unittest

from wardragon_console.ini_reconcile import ReconcileError, parse_ini, reconcile


BASE = """\
# DragonSync config
[SETTINGS]
tak_host =
tak_port =
mqtt_enabled = false
rate_limit = 3.0

[gps]
use_static_gps = false
"""


class ReconcileTests(unittest.TestCase):
    def test_operator_change_survives_untouched_upstream_key(self):
        local = BASE.replace("tak_host =", "tak_host = tak.example.com")
        new = BASE
        result = reconcile(BASE, local, new)
        self.assertEqual(parse_ini(result.text)[("SETTINGS", "tak_host")], "tak.example.com")
        self.assertEqual(result.preserved, [("SETTINGS", "tak_host")])
        self.assertEqual(result.conflicts, [])

    def test_new_upstream_key_appears(self):
        local = BASE.replace("mqtt_enabled = false", "mqtt_enabled = true")
        new = BASE.replace("rate_limit = 3.0", "rate_limit = 3.0\nnew_feature = on")
        merged = parse_ini(reconcile(BASE, local, new).text)
        self.assertEqual(merged[("SETTINGS", "new_feature")], "on")
        self.assertEqual(merged[("SETTINGS", "mqtt_enabled")], "true")

    def test_upstream_default_change_applies_when_operator_untouched(self):
        new = BASE.replace("rate_limit = 3.0", "rate_limit = 5.0")
        merged = parse_ini(reconcile(BASE, BASE, new).text)
        self.assertEqual(merged[("SETTINGS", "rate_limit")], "5.0")

    def test_both_changed_operator_wins_and_conflict_reported(self):
        local = BASE.replace("rate_limit = 3.0", "rate_limit = 1.0")
        new = BASE.replace("rate_limit = 3.0", "rate_limit = 5.0")
        result = reconcile(BASE, local, new)
        self.assertEqual(parse_ini(result.text)[("SETTINGS", "rate_limit")], "1.0")
        self.assertEqual(result.conflicts, [("SETTINGS", "rate_limit")])

    def test_operator_added_key_and_section_kept(self):
        local = BASE.replace("rate_limit = 3.0", "rate_limit = 3.0\nmy_key = mine") + "\n[custom]\nx = 1\n"
        merged = parse_ini(reconcile(BASE, local, BASE).text)
        self.assertEqual(merged[("SETTINGS", "my_key")], "mine")
        self.assertEqual(merged[("custom", "x")], "1")

    def test_upstream_comments_and_order_preserved(self):
        new = BASE.replace("[gps]", "# GPS block\n[gps]")
        local = BASE.replace("use_static_gps = false", "use_static_gps = true")
        text = reconcile(BASE, local, new).text
        self.assertIn("# GPS block", text)
        self.assertLess(text.index("[SETTINGS]"), text.index("[gps]"))

    def test_percent_values_do_not_break_parsing(self):
        local = BASE.replace("tak_host =", "tak_host = p%40ss")
        merged = parse_ini(reconcile(BASE, local, BASE).text)
        self.assertEqual(merged[("SETTINGS", "tak_host")], "p%40ss")

    def test_duplicate_keys_all_replaced(self):
        new = BASE.replace("rate_limit = 3.0", "rate_limit = 3.0\nrate_limit = 3.0")
        local = BASE.replace("rate_limit = 3.0", "rate_limit = 9.0")
        merged = parse_ini(reconcile(BASE, local, new).text)
        self.assertEqual(merged[("SETTINGS", "rate_limit")], "9.0")

    def test_multiline_value_round_trips(self):
        base = "[s]\nlist = a\n    b\nother = 1\n"
        local = "[s]\nlist = a\n    c\nother = 1\n"
        merged = parse_ini(reconcile(base, local, base).text)
        self.assertEqual(merged[("s", "list")], "a\nc")
        self.assertEqual(merged[("s", "other")], "1")

    def test_colon_separator_preserved(self):
        base = "[s]\nkey: one\n"
        local = "[s]\nkey: two\n"
        text = reconcile(base, local, base).text
        self.assertIn("key: two", text)

    def test_unparseable_input_raises(self):
        with self.assertRaises(ReconcileError):
            reconcile("no section header\n", BASE, BASE)

    def test_no_operator_changes_returns_new_unchanged(self):
        new = BASE.replace("rate_limit = 3.0", "rate_limit = 4.0")
        result = reconcile(BASE, BASE, new)
        self.assertEqual(result.text, new)
        self.assertEqual(result.preserved, [])


if __name__ == "__main__":
    unittest.main()
