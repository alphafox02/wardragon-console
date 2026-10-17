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
from wardragon_console.history import HistoryStore
from wardragon_console.tracks import TrackBuffer


class ServerHarness:
    def __init__(self, dragonsync_dir: Path, **settings_overrides) -> None:
        self.store = SnapshotStore()
        self.tracks = TrackBuffer()
        self.settings = Settings(
            bind_host="127.0.0.1",
            bind_port=0,
            dragonsync_dir=dragonsync_dir,
            **settings_overrides,
        )
        self.history = HistoryStore(dragonsync_dir / "console-data", max_bytes=50 * 1024 * 1024)
        self.server = ConsoleServer(self.settings, self.store, tracks=self.tracks, history=self.history)
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

    def test_upgrade_post_rejects_cross_origin(self) -> None:
        status, _ = _request(
            "POST",
            self.harness.url("/api/upgrade/dragonsync"),
            body=b"",
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_upgrade_unknown_target_is_not_found(self) -> None:
        status, _ = _request("GET", self.harness.url("/api/upgrade/..%2f..%2fetc"))
        self.assertEqual(status, 404)
        status, _ = _request(
            "POST",
            self.harness.url("/api/upgrade/bogus"),
            body=b"",
            headers={"Origin": f"http://127.0.0.1:{self.harness.port}"},
        )
        self.assertEqual(status, 404)

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
            self.assertIn("img-src 'self' data: https://tile.openstreetmap.org;", response.headers["Content-Security-Policy"])

    def test_map_endpoint_returns_tracks_and_tiles(self) -> None:
        self.harness.tracks.ingest({"drones": [{"id": "d1", "lat": 40, "lon": -75}]}, None, None)
        status, body = _request("GET", self.harness.url("/api/map"))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["tracks"][0]["id"], "d1")
        self.assertEqual(payload["tiles"]["url"], "https://tile.openstreetmap.org/{z}/{x}/{y}.png")


class HistoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.harness = ServerHarness(Path(self.tmp.name))
        self.origin = {"Origin": f"http://127.0.0.1:{self.harness.port}", "Content-Type": "application/json"}

    def tearDown(self) -> None:
        self.harness.history._close()
        self.harness.stop()
        self.tmp.cleanup()

    def test_settings_put_rejects_cross_origin(self) -> None:
        status, _ = _request("PUT", self.harness.url("/api/map/history/settings"), json.dumps({"enabled": True}).encode(),
                             {"Origin": "http://evil.example", "Content-Type": "application/json"})
        self.assertEqual(status, 403)
        self.assertFalse(self.harness.history.settings["enabled"])

    def test_enable_record_query_export(self) -> None:
        status, body = _request("PUT", self.harness.url("/api/map/history/settings"), json.dumps({"enabled": True}).encode(), self.origin)
        self.assertEqual(status, 200, body)
        recorded = self.harness.tracks.ingest({"drones": [{"id": "d1", "lat": 40.001, "lon": -75}]}, None, None)
        self.harness.history.record(recorded, None, [])
        status, body = _request("GET", self.harness.url("/api/map/history"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["flights"][0]["track_id"], "d1")
        req = urllib.request.Request(self.harness.url("/api/map/history/export?format=kml"))
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertIn("attachment;", response.headers["Content-Disposition"])
            self.assertIn(b"<gx:Track>", response.read())

    def test_bad_windows_and_formats_rejected(self) -> None:
        for query in ("from=10&to=5", "from=abc", "from=0&to=99999999", "format=exe"):
            path = "/api/map/history/export?" + query if "format" in query else "/api/map/history?" + query
            status, _ = _request("GET", self.harness.url(path))
            self.assertEqual(status, 400, query)

    def test_read_only_connection_cannot_change_history(self) -> None:
        self.harness.server.settings = replace(self.harness.settings, bind_host="0.0.0.0", allow_remote_config_write=False)
        status, _ = _request("PUT", self.harness.url("/api/map/history/settings"), json.dumps({"enabled": True}).encode(), self.origin)
        self.assertEqual(status, 403)
        status, _ = _request("POST", self.harness.url("/api/map/history/clear"), b"", self.origin)
        self.assertEqual(status, 403)


class MapDisabledTests(unittest.TestCase):
    def test_map_disabled_hides_endpoint_and_tile_origin(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        harness = ServerHarness(Path(tmp.name), map_enabled=False)
        try:
            status, _ = _request("GET", harness.url("/api/map"))
            self.assertEqual(status, 404)
            with urllib.request.urlopen(harness.url("/"), timeout=5) as response:
                self.assertIn("img-src 'self' data:;", response.headers["Content-Security-Policy"])
        finally:
            harness.stop()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
