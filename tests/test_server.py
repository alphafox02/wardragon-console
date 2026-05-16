import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from wardragon_console.server import ConsoleServer
from wardragon_console.settings import Settings
from wardragon_console.state import SnapshotStore


class ServerHarness:
    def __init__(self, dragonsync_dir: Path) -> None:
        self.store = SnapshotStore()
        self.settings = Settings(
            bind_host="127.0.0.1",
            bind_port=0,
            dragonsync_dir=dragonsync_dir,
        )
        self.server = ConsoleServer(self.settings, self.store)
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        # ConsoleServer captured bind_port=0 in settings; the OS assigned an
        # actual port. Patch settings so origin/host comparisons reflect reality.
        self.settings = replace(self.settings, bind_port=self.port)
        self.server.settings = self.settings
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _request(method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class ServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config.ini").write_text("[SETTINGS]\nmqtt_enabled = false\n", encoding="utf-8")
        (root / "gps.ini").write_text("[gps]\nuse_static_gps = false\n", encoding="utf-8")
        self.harness = ServerHarness(root)

    def tearDown(self) -> None:
        self.harness.stop()
        self.tmp.cleanup()

    def test_put_rejects_cross_origin(self) -> None:
        body = json.dumps({"mqtt_enabled": False}).encode("utf-8")
        status, _ = _request(
            "PUT",
            self.harness.url("/api/config/config.ini"),
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://evil.example",
                "Host": f"127.0.0.1:{self.harness.port}",
            },
        )
        self.assertEqual(status, 403)

    def test_put_accepts_matching_origin(self) -> None:
        body = json.dumps({"mqtt_enabled": False}).encode("utf-8")
        status, _ = _request(
            "PUT",
            self.harness.url("/api/config/config.ini"),
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.harness.port}",
            },
        )
        self.assertEqual(status, 200)

    def test_put_with_no_origin_allowed_from_loopback(self) -> None:
        body = json.dumps({"mqtt_enabled": False}).encode("utf-8")
        status, _ = _request(
            "PUT",
            self.harness.url("/api/config/config.ini"),
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)

    def test_negative_content_length_is_rejected(self) -> None:
        # urllib won't let us send a negative Content-Length, so hit the
        # handler with a hand-built request through a raw socket.
        import socket

        with socket.create_connection((self.harness.host, self.harness.port), timeout=2) as sock:
            sock.sendall(
                b"PUT /api/config/config.ini HTTP/1.0\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: -1\r\n"
                b"Content-Type: application/json\r\n\r\n"
            )
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        self.assertIn(b"400", data.split(b"\r\n", 1)[0])

    def test_index_html_has_security_headers(self) -> None:
        req = urllib.request.Request(self.harness.url("/"))
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("default-src", response.headers.get("Content-Security-Policy", ""))
            self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")


if __name__ == "__main__":
    unittest.main()
