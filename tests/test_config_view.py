import configparser
import tempfile
import unittest
from pathlib import Path

from wardragon_console.config_view import read_config_files, write_config_file, write_curated_config
from wardragon_console.settings import Settings


class ConfigViewTests(unittest.TestCase):
    def test_read_config_masks_sensitive_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[mqtt]\nusername = user\npassword = secret\n", encoding="utf-8")
            (root / "gps.ini").write_text("[gps]\nmode = static\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root)

            payload = read_config_files(settings, include_raw=False)
            mqtt_items = payload["files"][0]["sections"][0]["items"]
            self.assertIn({"key": "password", "value": "********"}, mqtt_items)
            mqtt_form = next(g for g in payload["forms"][0]["groups"] if g["title"] == "MQTT")["fields"]
            password_field = [field for field in mqtt_form if field["key"] == "mqtt_password"][0]
            self.assertEqual(password_field["value"], "")

    def test_remote_config_form_masks_sensitive_values_even_when_writable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_password = secret\n", encoding="utf-8")
            (root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="0.0.0.0", allow_remote_config_write=True)

            payload = read_config_files(settings, include_raw=False)
            mqtt_form = next(g for g in payload["forms"][0]["groups"] if g["title"] == "MQTT")["fields"]
            password_field = [field for field in mqtt_form if field["key"] == "mqtt_password"][0]
            self.assertEqual(password_field["value"], "********")

    def test_write_config_is_atomic_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[old]\nvalue = 1\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            result = write_config_file(settings, "config.ini", "[new]\nvalue = 2\n")

            self.assertTrue(result["ok"])
            self.assertIn("[new]", (root / "config.ini").read_text(encoding="utf-8"))
            backups = list(root.glob("config.ini.bak.*"))
            self.assertEqual(len(backups), 1)

    def test_write_skips_backup_when_content_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[a]\nx = 0\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            first = write_config_file(settings, "config.ini", "[a]\nx = 1\n")
            second = write_config_file(settings, "config.ini", "[a]\nx = 1\n")

            self.assertFalse(first.get("unchanged"))
            self.assertTrue(second.get("unchanged"))
            self.assertIsNone(second.get("backup_path"))
            backups = list(root.glob("config.ini.bak.*"))
            self.assertEqual(len(backups), 1)

    def test_write_rejects_unknown_filename_and_invalid_ini(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(dragonsync_dir=Path(temp_dir), bind_host="127.0.0.1")
            with self.assertRaises(ValueError):
                write_config_file(settings, "../config.ini", "[x]\ny = z\n")
            with self.assertRaises(ValueError):
                write_config_file(settings, "config.ini", "not = valid without section\n")

    def test_remote_bind_disables_write_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                dragonsync_dir=Path(temp_dir),
                bind_host="0.0.0.0",
                allow_remote_config_write=False,
            )
            with self.assertRaises(PermissionError):
                write_config_file(settings, "config.ini", "[x]\ny = z\n")

    def test_curated_write_preserves_comments_and_omits_unknown_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text(
                "[SETTINGS]\n# keep this\nmqtt_enabled = false\nmqtt_port = 1883\nkismet_enabled = true\n",
                encoding="utf-8",
            )
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            write_curated_config(settings, "config.ini", {"mqtt_enabled": True, "mqtt_host": "broker.local"})
            content = (root / "config.ini").read_text(encoding="utf-8")

            self.assertIn("# keep this", content)
            self.assertIn("mqtt_enabled = true", content)
            self.assertIn("mqtt_host = broker.local", content)
            self.assertIn("kismet_enabled = true", content)

    def test_curated_write_preserves_masked_remote_secret_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_enabled = false\nmqtt_password = secret\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="192.168.42.10", allow_remote_config_write=True)

            write_curated_config(settings, "config.ini", {"rate_limit": "3.5", "mqtt_password": "********"})
            content = (root / "config.ini").read_text(encoding="utf-8")

            self.assertIn("rate_limit = 3.5", content)
            self.assertIn("mqtt_password = secret", content)

    def test_curated_write_rejects_adsb_and_kismet_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(dragonsync_dir=Path(temp_dir), bind_host="127.0.0.1")
            with self.assertRaises(ValueError):
                write_curated_config(settings, "config.ini", {"adsb_enabled": True})
            with self.assertRaises(ValueError):
                write_curated_config(settings, "config.ini", {"kismet_enabled": True})

    def test_curated_write_enforces_cross_field_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            with self.assertRaisesRegex(ValueError, "TAK host and TAK port"):
                write_curated_config(settings, "config.ini", {"tak_host": "tak.example.com"})
            with self.assertRaisesRegex(ValueError, "Home Assistant discovery"):
                write_curated_config(settings, "config.ini", {"mqtt_ha_enabled": True})

    def test_curated_write_can_clear_tak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text(
                "[SETTINGS]\ntak_host = tak.example.com\ntak_port = 8089\ntak_protocol = TCP\ntak_tls_p12 = /tmp/c.p12\n",
                encoding="utf-8",
            )
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            write_curated_config(settings, "config.ini", {"tak_host": "", "tak_port": "", "tak_protocol": ""})
            content = (root / "config.ini").read_text(encoding="utf-8")

            self.assertIn("tak_host = ", content)
            self.assertIn("tak_port = ", content)
            self.assertIn("tak_protocol = ", content)
            self.assertNotIn("tak.example.com", content)

    def test_curated_write_enforces_gps_ranges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            with self.assertRaisesRegex(ValueError, "static_lat"):
                write_curated_config(settings, "gps.ini", {"static_lat": "100"})


class PercentInConfigTests(unittest.TestCase):
    """configparser treats % as interpolation; a plain % in a value (a
    password, say) used to break the Config tab. Read it literally, the same
    way DragonSync does, and store % as %% when saving."""

    def _settings(self, root):
        return Settings(dragonsync_dir=root, bind_host="127.0.0.1")

    def _form_value(self, payload, key):
        for group in payload["forms"][0]["groups"]:
            for field in group["fields"]:
                if field["key"] == key:
                    return field["value"]
        raise KeyError(key)

    def test_plain_percent_reads_literally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_password = pa%ss\nmqtt_host = h%%1\n", encoding="utf-8")
            (root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
            payload = read_config_files(self._settings(root), include_raw=False)
            self.assertIsNone(payload["files"][0]["error"])
            self.assertEqual(self._form_value(payload, "mqtt_password"), "pa%ss")
            self.assertEqual(self._form_value(payload, "mqtt_host"), "h%1")

    def test_escaped_percent_still_reads_as_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_host = h%%1\n", encoding="utf-8")
            (root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
            payload = read_config_files(self._settings(root), include_raw=False)
            self.assertEqual(self._form_value(payload, "mqtt_host"), "h%1")

    def test_saving_a_percent_writes_the_ini_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_enabled = false\n", encoding="utf-8")
            write_curated_config(self._settings(root), "config.ini", {"mqtt_password": "pa%ss"})
            text = (root / "config.ini").read_text(encoding="utf-8")
            self.assertIn("mqtt_password = pa%%ss", text)
            # Any DragonSync, old or new, reads it back as typed.
            parser = configparser.ConfigParser()
            parser.read_string(text)
            self.assertEqual(parser["SETTINGS"]["mqtt_password"], "pa%ss")

    def test_saving_works_when_another_value_has_a_plain_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\nmqtt_password = pa%ss\nmqtt_enabled = false\n", encoding="utf-8")
            write_curated_config(self._settings(root), "config.ini", {"mqtt_port": "1883"})
            text = (root / "config.ini").read_text(encoding="utf-8")
            self.assertIn("mqtt_port = 1883", text)
            self.assertIn("mqtt_password = pa%ss", text)  # untouched keys stay as they were


if __name__ == "__main__":
    unittest.main()
