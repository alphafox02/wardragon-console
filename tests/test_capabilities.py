import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wardragon_console.capabilities import _detect_dragonsig, detect_capabilities
from wardragon_console.state import SnapshotStore, SourceTiming


class DragonSigDetectionTests(unittest.TestCase):
    def test_env_override_yes_forces_true(self):
        with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "yes"}):
            self.assertTrue(_detect_dragonsig())

    def test_env_override_no_forces_false(self):
        with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "no"}):
            self.assertFalse(_detect_dragonsig())

    def test_env_override_various_truthy(self):
        for value in ("1", "true", "on", "YES", "True"):
            with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": value}):
                self.assertTrue(_detect_dragonsig(), value)

    def test_env_override_various_falsy(self):
        for value in ("0", "false", "off", "NO", "False"):
            with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": value}):
                self.assertFalse(_detect_dragonsig(), value)

    def test_auto_returns_true_when_unit_file_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit_dir = Path(tmp) / "systemd"
            unit_dir.mkdir()
            (unit_dir / "dragonsig.service").write_text("[Unit]\n")
            with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "auto"}), \
                 patch("wardragon_console.capabilities.SYSTEMD_UNIT_DIRS", (str(unit_dir),)):
                self.assertTrue(_detect_dragonsig())

    def test_auto_returns_false_when_unit_file_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "auto"}), \
                 patch("wardragon_console.capabilities.SYSTEMD_UNIT_DIRS", (tmp,)):
                self.assertFalse(_detect_dragonsig())

    def test_detect_capabilities_returns_has_dragonsig(self):
        with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "no"}):
            caps = detect_capabilities()
            self.assertIn("has_dragonsig", caps)
            self.assertFalse(caps["has_dragonsig"])


class SnapshotIncludesCapabilitiesTests(unittest.TestCase):
    def test_snapshot_carries_capabilities_dict(self):
        with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "no"}):
            store = SnapshotStore()
            snap = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
            self.assertIn("capabilities", snap)
            self.assertFalse(snap["capabilities"]["has_dragonsig"])

    def test_snapshot_capabilities_reflect_yes_override(self):
        with patch.dict(os.environ, {"WARDRAGON_HAS_DRAGONSIG": "yes"}):
            store = SnapshotStore()
            snap = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
            self.assertTrue(snap["capabilities"]["has_dragonsig"])


if __name__ == "__main__":
    unittest.main()
