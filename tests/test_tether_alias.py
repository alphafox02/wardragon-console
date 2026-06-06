import os
import unittest
from unittest.mock import patch

from wardragon_console.settings import Settings, _env_claim_profiles
from wardragon_console.state import SnapshotStore
from wardragon_console.tether import TetherAccessManager


class ClaimProfileEnvParseTests(unittest.TestCase):
    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _env_claim_profiles("VAR", (("10.152.47.0/24", "10.152.47.250"),)),
                (("10.152.47.0/24", "10.152.47.250"),),
            )

    def test_empty_string_disables(self):
        with patch.dict(os.environ, {"VAR": ""}, clear=True):
            self.assertEqual(_env_claim_profiles("VAR", (("10.0.0.0/8", "10.0.0.1"),)), ())

    def test_explicit_value_replaces_default(self):
        with patch.dict(os.environ, {"VAR": "192.168.42.0/24=192.168.42.250"}, clear=True):
            self.assertEqual(
                _env_claim_profiles("VAR", (("10.152.47.0/24", "10.152.47.250"),)),
                (("192.168.42.0/24", "192.168.42.250"),),
            )

    def test_multiple_profiles_comma_separated(self):
        value = "192.168.42.0/24=192.168.42.250, 10.152.47.0/24=10.152.47.250"
        with patch.dict(os.environ, {"VAR": value}, clear=True):
            self.assertEqual(
                _env_claim_profiles("VAR", ()),
                (
                    ("192.168.42.0/24", "192.168.42.250"),
                    ("10.152.47.0/24", "10.152.47.250"),
                ),
            )

    def test_malformed_entries_skipped(self):
        with patch.dict(os.environ, {"VAR": "garbage,192.168.42.0/24=192.168.42.250,=,foo="}, clear=True):
            self.assertEqual(
                _env_claim_profiles("VAR", ()),
                (("192.168.42.0/24", "192.168.42.250"),),
            )


class MatchingClaimProfileTests(unittest.TestCase):
    def _manager(self, profiles):
        settings = Settings(tether_claim_profiles=profiles)
        return TetherAccessManager(settings, SnapshotStore())

    def test_returns_alias_and_prefix_when_in_network(self):
        mgr = self._manager((("10.152.47.0/24", "10.152.47.250"),))
        self.assertEqual(mgr._matching_claim_profile("10.152.47.95"), ("10.152.47.250", 24))

    def test_returns_none_when_outside_any_network(self):
        mgr = self._manager((("10.152.47.0/24", "10.152.47.250"),))
        # Apple-style tether subnet has no profile by default.
        self.assertIsNone(mgr._matching_claim_profile("172.20.10.5"))

    def test_first_matching_profile_wins(self):
        mgr = self._manager((
            ("10.0.0.0/8", "10.0.0.250"),
            ("10.152.47.0/24", "10.152.47.250"),
        ))
        self.assertEqual(mgr._matching_claim_profile("10.152.47.95"), ("10.0.0.250", 8))

    def test_invalid_profiles_are_skipped(self):
        mgr = self._manager((
            ("not-an-ip", "also-not-an-ip"),
            ("10.152.47.0/24", "10.152.47.250"),
        ))
        self.assertEqual(mgr._matching_claim_profile("10.152.47.95"), ("10.152.47.250", 24))

    def test_no_profiles_returns_none(self):
        mgr = self._manager(())
        self.assertIsNone(mgr._matching_claim_profile("10.152.47.95"))


if __name__ == "__main__":
    unittest.main()
