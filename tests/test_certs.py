import stat
import tempfile
import unittest
from pathlib import Path

from wardragon_console.certs import upload_tak_cert
from wardragon_console.settings import Settings


CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIB
-----END CERTIFICATE-----
"""

KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIB
-----END PRIVATE KEY-----
"""


class CertUploadTests(unittest.TestCase):
    def test_upload_p12_writes_under_dragonsync_certs_and_updates_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.ini").write_text("[SETTINGS]\ntak_tls_p12 =\n", encoding="utf-8")
            settings = Settings(dragonsync_dir=root, bind_host="127.0.0.1")

            result = upload_tak_cert(settings, "tak_p12", "../../client.p12", b"p12-bytes", "secret")

            cert_path = Path(result["path"])
            self.assertEqual(cert_path.parent, root / "certs")
            self.assertTrue(cert_path.exists())
            self.assertEqual(stat.S_IMODE(cert_path.stat().st_mode), 0o600)
            config = (root / "config.ini").read_text(encoding="utf-8")
            self.assertIn(f"tak_tls_p12 = {cert_path}", config)
            self.assertIn("tak_tls_p12_pass = secret", config)

    def test_upload_pem_cert_requires_certificate_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(dragonsync_dir=Path(temp_dir), bind_host="127.0.0.1")
            with self.assertRaisesRegex(ValueError, "CERTIFICATE"):
                upload_tak_cert(settings, "tak_cert", "client.pem", b"not a cert")

    def test_upload_key_requires_private_key_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(dragonsync_dir=Path(temp_dir), bind_host="127.0.0.1")
            with self.assertRaisesRegex(ValueError, "PRIVATE KEY"):
                upload_tak_cert(settings, "tak_key", "client.key", CERT_PEM)

    def test_upload_rejects_remote_when_config_write_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                dragonsync_dir=Path(temp_dir),
                bind_host="192.168.42.10",
                allow_remote_config_write=False,
            )
            with self.assertRaises(PermissionError):
                upload_tak_cert(settings, "tak_ca", "ca.pem", CERT_PEM)

    def test_upload_rejects_bad_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(dragonsync_dir=Path(temp_dir), bind_host="127.0.0.1")
            with self.assertRaisesRegex(ValueError, "must use one of"):
                upload_tak_cert(settings, "tak_p12", "client.txt", b"p12-bytes")


if __name__ == "__main__":
    unittest.main()
