import json
import tempfile
import unittest
from pathlib import Path

from wardragon_console.dragonscope import (
    SECRET_PLACEHOLDER,
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


if __name__ == "__main__":
    unittest.main()
