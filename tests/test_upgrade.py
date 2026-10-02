import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from wardragon_console import upgrade
from wardragon_console.settings import Settings
from wardragon_console.updates import check_for_updates


def _completed(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


class StartUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.helper = Path(self.tmp.name) / "wardragon-upgrade"
        self.helper.write_text("#!/bin/sh\n", encoding="utf-8")
        self.patches = [
            patch.object(upgrade, "HELPER", self.helper),
            patch.object(upgrade, "_last_start", 0.0),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_remote_bind_without_restart_permission_is_refused(self):
        settings = Settings(bind_host="0.0.0.0", allow_remote_restart=False)
        with self.assertRaises(PermissionError):
            upgrade.start_upgrade(settings, "dragonsync")

    def test_disabled_is_refused(self):
        with self.assertRaises(PermissionError):
            upgrade.start_upgrade(Settings(upgrade_enabled=False), "dragonsync")

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            upgrade.start_upgrade(Settings(), "../../etc")

    def test_missing_helper_reports_one_time_install(self):
        self.helper.unlink()
        result = upgrade.start_upgrade(Settings(), "console")
        self.assertFalse(result["ok"])
        self.assertIn("install.sh", result["error"])

    def test_runs_pinned_sudo_command(self):
        with patch("wardragon_console.upgrade.subprocess.run", return_value=_completed()) as run:
            result = upgrade.start_upgrade(Settings(), "droneid")
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", str(self.helper), "start", "droneid"])

    def test_already_running(self):
        with patch("wardragon_console.upgrade.subprocess.run", return_value=_completed(3)):
            result = upgrade.start_upgrade(Settings(), "dragonsync")
        self.assertFalse(result["ok"])
        self.assertIn("already running", result["error"])

    def test_rapid_repeat_requests_are_throttled(self):
        with patch("wardragon_console.upgrade.subprocess.run", return_value=_completed()) as run:
            first = upgrade.start_upgrade(Settings(), "dragonsync")
            second = upgrade.start_upgrade(Settings(), "dragonsync")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(run.call_count, 1)


class UpgradeStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)
        self.patch = patch.object(upgrade, "STATE_DIR", self.state_dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _write(self, state):
        (self.state_dir / "dragonsync.json").write_text(json.dumps({
            "target": "dragonsync", "state": state, "message": "x",
            "started_at": time.time(), "warnings": [], "notes": [],
        }), encoding="utf-8")

    def test_running_without_active_unit_is_interrupted(self):
        self._write("running")
        with patch("wardragon_console.upgrade._unit_active", return_value=False):
            result = upgrade.upgrade_status("dragonsync")
        self.assertEqual(result["status"]["state"], "interrupted")

    def test_running_with_active_unit_stays_running(self):
        self._write("running")
        with patch("wardragon_console.upgrade._unit_active", return_value=True):
            result = upgrade.upgrade_status("dragonsync")
        self.assertEqual(result["status"]["state"], "running")

    def test_no_status_yet(self):
        with patch("wardragon_console.upgrade._unit_active", return_value=False):
            result = upgrade.upgrade_status("console")
        self.assertIsNone(result["status"])
        self.assertEqual(result["log"], "")

    def test_log_is_tailed(self):
        (self.state_dir / "droneid.log").write_text("old line\n" * 10000 + "last line\n", encoding="utf-8")
        with patch("wardragon_console.upgrade._unit_active", return_value=False):
            log = upgrade.upgrade_status("droneid")["log"]
        self.assertLessEqual(len(log), upgrade.LOG_TAIL_BYTES)
        self.assertTrue(log.endswith("last line\n"))
        self.assertTrue(log.startswith("old line"))

    def test_log_color_codes_are_stripped(self):
        (self.state_dir / "droneid.log").write_text("\x1b[0;32m[INFO]\x1b[0m done\n", encoding="utf-8")
        with patch("wardragon_console.upgrade._unit_active", return_value=False):
            self.assertEqual(upgrade.upgrade_status("droneid")["log"], "[INFO] done\n")

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            upgrade.upgrade_status("nope")


class UpdateCheckIncludesDroneidTests(unittest.TestCase):
    def test_droneid_reported(self):
        with patch("wardragon_console.updates._api_get", return_value={"sha": "b" * 40}), \
             patch("wardragon_console.updates._read_local_sha", return_value="a" * 40):
            payload = check_for_updates(Settings())
        self.assertEqual(payload["droneid"]["repo"], "alphafox02/droneid-go")
        self.assertTrue(payload["droneid"]["update_available"])
        self.assertTrue(payload["console"]["update_available"])


if __name__ == "__main__":
    unittest.main()
