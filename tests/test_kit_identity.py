"""Kit ID is fixed; the kit callsign is the editable display name.

The Kit ID (wardragon-<serial>) is how TAK, MQTT, Lattice and the ATAK plugin
track a kit, so the console no longer edits it. The name shown in TAK is
config.ini kit_callsign, edited through the normal curated config path.
"""

import configparser
import json
import re
import tempfile
import unittest
from pathlib import Path

from wardragon_console.config_schema import (
    CONFIG_GROUPS,
    KIT_CALLSIGN_PATTERN,
    field_map,
    validate_updates,
)
from wardragon_console.config_view import write_curated_config
from wardragon_console.settings import Settings
from wardragon_console.state import SnapshotStore, SourceTiming

from test_server import ServerHarness, _request

STATIC = Path(__file__).resolve().parent.parent / "src" / "wardragon_console" / "static"


class KitCallsignFieldTests(unittest.TestCase):
    def test_field_lives_in_a_cot_group(self):
        groups = {group.title: [f.key for f in group.fields] for group in CONFIG_GROUPS}
        self.assertIn("CoT", groups)
        self.assertIn("kit_callsign", groups["CoT"])
        self.assertEqual(field_map("config.ini")["kit_callsign"].kind, "text")

    def test_pattern_matches_dragonsync(self):
        # DragonSync utils/config.py KIT_CALLSIGN_RE. If one changes, change both.
        self.assertEqual(KIT_CALLSIGN_PATTERN, r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$")

    def test_valid_and_blank_values_are_accepted(self):
        for value in ("Alpha 1", "WD-North", "kit.2", "A", "X" * 32, "", "  Alpha 1  "):
            with self.subTest(value=value):
                result = validate_updates("config.ini", {"kit_callsign": value})
                self.assertEqual(result["kit_callsign"], value.strip())

    def test_invalid_values_are_rejected(self):
        for value in ("50% done", "a;b", "a#b", "line\nbreak", "X" * 33, "-dash", "<tag>", "café"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    validate_updates("config.ini", {"kit_callsign": value})
                self.assertIn("Kit callsign", str(ctx.exception))

    def test_saving_adds_the_key_and_the_file_still_parses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_enabled = false\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")
            write_curated_config(settings, "config.ini", {"kit_callsign": "Alpha 1"})
            parser = configparser.ConfigParser()  # default interpolation, as both apps read it
            parser.read(root / "config.ini")
            self.assertEqual(dict(parser["SETTINGS"])["kit_callsign"], "Alpha 1")
            self.assertEqual(parser["SETTINGS"]["mqtt_enabled"], "false")


class SummaryCallsignTests(unittest.TestCase):
    def _summary(self, status):
        store = SnapshotStore()
        store.update("dragonsync_status", status)
        return store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))["summary"]

    def test_callsign_comes_from_dragonsync_status(self):
        summary = self._summary({"kit_id": "wardragon-ABC", "uid": "wardragon-ABC", "callsign": "Alpha 1"})
        self.assertEqual(summary["kit_id"], "wardragon-ABC")
        self.assertEqual(summary["callsign"], "Alpha 1")

    def test_older_dragonsync_without_callsign_falls_back_to_kit_id(self):
        summary = self._summary({"uid": "wardragon-ABC"})
        self.assertEqual(summary["kit_id"], "wardragon-ABC")
        self.assertEqual(summary["callsign"], "wardragon-ABC")


class KitIdOverrideApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config.ini").write_text("[SETTINGS]\nmqtt_enabled = false\n", encoding="utf-8")
        (self.root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
        self.harness = ServerHarness(self.root)

    def tearDown(self):
        self.harness.stop()
        self.tmp.cleanup()

    def test_existing_override_is_still_reported(self):
        (self.root / "kit-id-override").write_text("rooftop-1\n", encoding="utf-8")
        status, body = _request("GET", self.harness.url("/api/kit-id-override"))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["value"], "rooftop-1")

    def test_kit_id_can_no_longer_be_written(self):
        (self.root / "kit-id-override").write_text("rooftop-1\n", encoding="utf-8")
        status, _ = _request(
            "PUT",
            self.harness.url("/api/kit-id-override"),
            body=json.dumps({"value": "renamed"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.harness.port}"},
        )
        self.assertEqual(status, 404)
        self.assertEqual((self.root / "kit-id-override").read_text(encoding="utf-8"), "rooftop-1\n")


class StaticAssetTests(unittest.TestCase):
    def test_kit_id_edit_controls_are_gone(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        for element_id in ("kit-id-override-input", "kit-id-override-save", "kit-id-override-clear"):
            self.assertNotIn(element_id, html)
            self.assertNotIn(element_id, script)
        self.assertIsNone(re.search(r'method:\s*"PUT"[^}]*kit-id-override|kit-id-override[^;]*method:\s*"PUT"', script))
        self.assertIn('id="kit-identity-details"', html)


if __name__ == "__main__":
    unittest.main()
