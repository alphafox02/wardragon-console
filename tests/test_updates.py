import unittest
from unittest.mock import patch

from wardragon_console.settings import Settings
from wardragon_console.state import SnapshotStore, SourceTiming
from wardragon_console.updates import _version_lt, check_for_updates


class VersionCompareTests(unittest.TestCase):
    def test_numeric_less_than(self):
        self.assertTrue(_version_lt("0.1.0", "0.2.0"))
        self.assertTrue(_version_lt("0.1.0", "1.0.0"))

    def test_numeric_not_less_than(self):
        self.assertFalse(_version_lt("0.2.0", "0.1.0"))
        self.assertFalse(_version_lt("1.0.0", "1.0.0"))

    def test_strips_v_prefix(self):
        self.assertTrue(_version_lt("0.1.0", "v0.2.0"))


class CheckForUpdatesTests(unittest.TestCase):
    def _settings(self, **overrides):
        return Settings(update_check_enabled=True, **overrides)

    def test_disabled_raises_permission_error(self):
        settings = Settings(update_check_enabled=False)
        with self.assertRaises(PermissionError):
            check_for_updates(settings, SnapshotStore())

    def test_marks_update_available_when_release_newer_than_local(self):
        settings = self._settings()
        store = SnapshotStore()

        def fake_api_get(path):
            if path.endswith("/commits/main"):
                return {"sha": "a" * 40}
            if path.endswith("/releases/latest"):
                return {"tag_name": "v999.0.0", "html_url": "https://example/release"}
            return None

        with patch("wardragon_console.updates._api_get", side_effect=fake_api_get), \
             patch("wardragon_console.updates._read_local_sha", return_value=None):
            payload = check_for_updates(settings, store)

        self.assertTrue(payload["console"]["update_available"])
        self.assertEqual(payload["console"]["latest_release_tag"], "v999.0.0")
        snap = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
        self.assertEqual(snap["updates"]["console"]["latest_release_tag"], "v999.0.0")

    def test_marks_update_available_when_local_sha_differs_from_upstream(self):
        settings = self._settings()
        store = SnapshotStore()

        def fake_api_get(path):
            if path.endswith("/commits/main"):
                return {"sha": "b" * 40}
            return None

        with patch("wardragon_console.updates._api_get", side_effect=fake_api_get), \
             patch("wardragon_console.updates._read_local_sha", return_value="a" * 40):
            payload = check_for_updates(settings, store)

        ds = payload["dragonsync"]
        self.assertTrue(ds["update_available"])
        self.assertEqual(ds["local_sha"], "a" * 7)
        self.assertEqual(ds["upstream_sha"], "b" * 7)
        self.assertIn("/compare/", ds["compare_url"])

    def test_up_to_date_when_shas_match(self):
        settings = self._settings()
        store = SnapshotStore()

        def fake_api_get(path):
            if path.endswith("/commits/main"):
                return {"sha": "c" * 40}
            return None

        with patch("wardragon_console.updates._api_get", side_effect=fake_api_get), \
             patch("wardragon_console.updates._read_local_sha", return_value="c" * 40):
            payload = check_for_updates(settings, store)

        self.assertFalse(payload["dragonsync"]["update_available"])
        self.assertIsNone(payload["dragonsync"]["compare_url"])

    def test_error_when_github_unreachable(self):
        settings = self._settings()
        store = SnapshotStore()

        with patch("wardragon_console.updates._api_get", return_value=None), \
             patch("wardragon_console.updates._read_local_sha", return_value=None):
            payload = check_for_updates(settings, store)

        self.assertEqual(payload["console"]["error"], "could not fetch upstream commit")
        self.assertFalse(payload["console"]["update_available"])


if __name__ == "__main__":
    unittest.main()
