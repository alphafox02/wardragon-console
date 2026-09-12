import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from wardragon_console.dragonscope import (
    SECRET_PLACEHOLDER,
    check_license,
    read_dragonscope,
    write_dragonscope,
)
from wardragon_console.settings import Settings


class DragonscopeTests(unittest.TestCase):
    def _settings(self, root: Path, **overrides) -> Settings:
        return Settings(
            dragonscope_dir=root,
            bind_host=overrides.pop("bind_host", "127.0.0.1"),
            **overrides,
        )

    def test_read_returns_groups_with_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dragonscope.cfg").write_text(json.dumps({
                "remote": "https://api.dragonos.net",
                "license_key": "dk_secret",
                "listen_port": 8080,
                "listen_addr": "127.0.0.1",
            }), encoding="utf-8")
            payload = read_dragonscope(self._settings(root))
            groups = {g["title"]: g for g in payload["form"]["groups"]}
            remote_fields = {f["key"]: f for f in groups["Remote"]["fields"]}
            self.assertEqual(remote_fields["remote"]["value"], "https://api.dragonos.net")
            # On loopback bind, license key is shown in full.
            self.assertEqual(remote_fields["license_key"]["value"], "dk_secret")

    def test_read_masks_license_on_remote_bind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dragonscope.cfg").write_text(json.dumps({
                "remote": "https://api.dragonos.net",
                "license_key": "dk_secret",
            }), encoding="utf-8")
            settings = self._settings(root, bind_host="192.168.42.10", allow_remote_config_write=True)
            payload = read_dragonscope(settings)
            remote_fields = {
                f["key"]: f
                for g in payload["form"]["groups"]
                for f in g["fields"]
            }
            self.assertEqual(remote_fields["license_key"]["value"], SECRET_PLACEHOLDER)
            # Non-sensitive field still in clear.
            self.assertEqual(remote_fields["remote"]["value"], "https://api.dragonos.net")

    def test_write_creates_file_atomically_with_backup_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dragonscope.cfg").write_text(
                json.dumps({"remote": "https://old", "license_key": "old"}),
                encoding="utf-8",
            )
            settings = self._settings(root)
            result = write_dragonscope(settings, {
                "remote": "https://api.dragonos.net",
                "license_key": "dk_new",
                "listen_port": 80,
                "listen_addr": "0.0.0.0",
            })
            self.assertTrue(result["ok"])
            self.assertFalse(result["unchanged"])
            data = json.loads((root / "dragonscope.cfg").read_text())
            self.assertEqual(data["remote"], "https://api.dragonos.net")
            self.assertEqual(data["license_key"], "dk_new")
            self.assertEqual(data["listen_port"], 80)
            backups = list(root.glob("dragonscope.cfg.bak.*"))
            self.assertEqual(len(backups), 1)

    def test_write_preserves_existing_license_when_placeholder_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dragonscope.cfg").write_text(
                json.dumps({"remote": "https://api.dragonos.net", "license_key": "dk_secret"}),
                encoding="utf-8",
            )
            settings = self._settings(root, bind_host="192.168.42.10", allow_remote_config_write=True)
            write_dragonscope(settings, {
                "remote": "https://api.dragonos.net",
                "license_key": SECRET_PLACEHOLDER,
                "listen_port": 80,
            })
            data = json.loads((root / "dragonscope.cfg").read_text())
            self.assertEqual(data["license_key"], "dk_secret")
            self.assertEqual(data["listen_port"], 80)

    def test_write_short_circuits_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            first = write_dragonscope(settings, {
                "remote": "https://api.dragonos.net",
                "license_key": "dk_a",
                "listen_port": 80,
                "listen_addr": "0.0.0.0",
            })
            second = write_dragonscope(settings, {
                "remote": "https://api.dragonos.net",
                "license_key": "dk_a",
                "listen_port": 80,
                "listen_addr": "0.0.0.0",
            })
            self.assertFalse(first["unchanged"])
            self.assertTrue(second["unchanged"])
            backups = list(root.glob("dragonscope.cfg.bak.*"))
            self.assertEqual(len(backups), 0)

    def test_write_rejects_unknown_keys_and_remote_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            with self.assertRaises(ValueError):
                write_dragonscope(settings, {"not_a_field": "x"})

            remote = self._settings(root, bind_host="0.0.0.0", allow_remote_config_write=False)
            with self.assertRaises(PermissionError):
                write_dragonscope(remote, {"remote": "https://example"})

    def test_write_rejects_out_of_range_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            with self.assertRaises(ValueError):
                write_dragonscope(settings, {"listen_port": 99999})


class CheckLicenseTests(unittest.TestCase):
    def _settings(self, root: Path, **overrides) -> Settings:
        return Settings(
            dragonscope_dir=root,
            bind_host="127.0.0.1",
            **overrides,
        )

    def _write_cfg(self, root: Path, remote="https://api.example.test", key="dk_valid"):
        (root / "dragonscope.cfg").write_text(json.dumps({
            "remote": remote,
            "license_key": key,
            "listen_port": 80,
            "listen_addr": "0.0.0.0",
        }), encoding="utf-8")

    def test_disabled_raises_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root)
            settings = self._settings(root, dragonscope_license_check_enabled=False)
            with self.assertRaises(PermissionError):
                check_license(settings)

    def test_missing_key_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root, key="CHANGE_ME")
            settings = self._settings(root)
            result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertIn("license key", result["error"].lower())

    def test_missing_remote_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root, remote="https://CHANGE_ME")
            settings = self._settings(root)
            result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertIn("remote", result["error"].lower())

    def test_invalid_remote_url_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root, remote="not-a-url")
            settings = self._settings(root)
            result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertIn("http", result["error"].lower())

    def test_happy_path_parses_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root)
            settings = self._settings(root)
            body = json.dumps({
                "owner": "acme", "tier": "enterprise", "active": True,
                "billing_status": "trial", "bound": False,
                "expires_in_days": None, "paid_through_in_days": None,
                "month": "2026-09",
                "cryp_used": 5, "cryp_quota": -1,
                "infp_used": 10, "infp_quota": -1,
                "drones_seen_30d": 3,
            }).encode("utf-8")

            class FakeResp:
                def __init__(self, data): self._data = data
                def read(self, n=None): return self._data
                def __enter__(self): return self
                def __exit__(self, *a): return False

            with patch("wardragon_console.dragonscope.urlopen", return_value=FakeResp(body)):
                result = check_license(settings)
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["owner"], "acme")
            self.assertTrue(result["data"]["active"])
            self.assertIsNone(result["data"]["expires_in_days"])
            self.assertEqual(result["data"]["cryp_quota"], -1)
            self.assertIn("/me", result["endpoint"])

    def test_http_401_returns_invalid_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root)
            settings = self._settings(root)
            err = HTTPError("https://api.example.test/me", 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"bad key"}'))
            with patch("wardragon_console.dragonscope.urlopen", side_effect=err):
                result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 401)
            self.assertIn("invalid", result["error"].lower())

    def test_http_403_returns_rejected_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root)
            settings = self._settings(root)
            err = HTTPError("https://api.example.test/me", 403, "Forbidden", {}, io.BytesIO(b""))
            with patch("wardragon_console.dragonscope.urlopen", side_effect=err):
                result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 403)
            self.assertIn("rejected", result["error"].lower())

    def test_network_failure_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cfg(root)
            settings = self._settings(root)
            with patch("wardragon_console.dragonscope.urlopen", side_effect=URLError("no route")):
                result = check_license(settings)
            self.assertFalse(result["ok"])
            self.assertIn("cannot reach", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
