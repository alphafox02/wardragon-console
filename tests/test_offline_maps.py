import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from wardragon_console import offline_maps as om
from wardragon_console.offline_maps import OfflineMaps

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_mbtiles(path: Path, tile: bytes = PNG, metadata=None, tiles=True) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    meta = {"name": "Test area", "format": "png", "minzoom": "10", "maxzoom": "12", "bounds": "-75.2,39.9,-75.1,40.0"}
    meta.update(metadata or {})
    conn.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    if tiles:
        # TMS row for z=10, y=388 is 1023 - 388 = 635.
        conn.execute("INSERT INTO tiles VALUES (10, 297, 635, ?)", (tile,))
        conn.execute("INSERT INTO tiles VALUES (12, 1190, 2540, ?)", (tile,))
    conn.commit()
    conn.close()
    return path


class OfflineMapsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.maps = OfflineMaps(self.data, max_upload_bytes=10 * 1024 * 1024)
        self.maps.dir.mkdir(parents=True)
        self.free = patch.object(OfflineMaps, "free_bytes", return_value=100 * 1024 ** 3)
        self.free.start()

    def tearDown(self):
        self.free.stop()
        for name in list(self.maps._conns):
            self.maps._close(name)
        self.tmp.cleanup()

    def test_valid_map_listed_and_tiles_served(self):
        make_mbtiles(self.maps.dir / "philly.mbtiles")
        [info] = self.maps.list()
        self.assertTrue(info["ok"], info)
        self.assertEqual((info["minzoom"], info["maxzoom"]), (10, 12))
        self.assertEqual(info["bounds"], [[39.9, -75.2], [40.0, -75.1]])
        self.assertEqual(self.maps.tile("philly.mbtiles", 10, 297, 388), (PNG, "image/png"))
        self.assertIsNone(self.maps.tile("philly.mbtiles", 10, 297, 389))
        self.assertIsNone(self.maps.tile("philly.mbtiles", 10, 5000, 1))
        self.assertIsNone(self.maps.tile("../etc/passwd", 10, 1, 1))

    def test_rejects_non_sqlite_vector_and_empty(self):
        (self.maps.dir / "junk.mbtiles").write_bytes(b"not a database at all" * 10)
        make_mbtiles(self.maps.dir / "vector.mbtiles", tile=b"\x1f\x8b\x08\x00gzipped-pbf")
        make_mbtiles(self.maps.dir / "empty.mbtiles", tiles=False)
        errors = {m["name"]: m.get("error", "") for m in self.maps.list()}
        self.assertFalse(any(m["ok"] for m in self.maps.list()))
        self.assertIn("not an MBTiles", errors["junk.mbtiles"])
        self.assertIn("vector", errors["vector.mbtiles"])
        self.assertIn("no map tiles", errors["empty.mbtiles"])
        self.assertIsNone(self.maps.tile("vector.mbtiles", 10, 297, 388))

    def test_hostile_view_cannot_hang_validation(self):
        path = self.maps.dir / "hostile.mbtiles"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        conn.execute(
            "CREATE VIEW tiles AS WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
            "SELECT 1 AS zoom_level, 1 AS tile_column, 1 AS tile_row, NULL AS tile_data FROM c WHERE x < 0"
        )
        conn.commit()
        conn.close()
        with patch.object(om, "QUERY_SECONDS", 0.3):
            started = time.monotonic()
            [info] = self.maps.list()
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(info["ok"])

    def test_symlinks_ignored(self):
        target = make_mbtiles(self.data / "elsewhere.mbtiles")
        os.symlink(target, self.maps.dir / "link.mbtiles")
        self.assertEqual(self.maps.list(), [])

    def test_upload_installs_valid_map(self):
        data = make_mbtiles(self.data / "src.mbtiles").read_bytes()
        info = self.maps.save_upload(io.BytesIO(data), len(data), "area-1.mbtiles")
        self.assertTrue(info["ok"])
        self.assertTrue((self.maps.dir / "area-1.mbtiles").is_file())
        self.assertEqual([p for p in os.listdir(self.maps.dir) if p.endswith(".partial")], [])

    def test_upload_rejections_leave_nothing_behind(self):
        good = make_mbtiles(self.data / "src.mbtiles").read_bytes()
        cases = [
            (io.BytesIO(good), len(good), "../evil.mbtiles", "file name"),
            (io.BytesIO(good), len(good), "area.zip", "file name"),
            (io.BytesIO(good), 11 * 1024 * 1024, "big.mbtiles", "upload limit"),
            (io.BytesIO(good[:100]), len(good), "cut.mbtiles", "interrupted"),
            (io.BytesIO(b"garbage" * 100), 700, "bad.mbtiles", "not an MBTiles"),
            (io.BytesIO(b""), 0, "empty.mbtiles", "empty"),
        ]
        for stream, length, name, message in cases:
            with self.assertRaises(ValueError) as ctx:
                self.maps.save_upload(stream, length, name)
            self.assertIn(message, str(ctx.exception), name)
        self.assertEqual(os.listdir(self.maps.dir), [])

    def test_upload_refuses_duplicates_limit_and_low_disk(self):
        good = make_mbtiles(self.data / "src.mbtiles").read_bytes()
        self.maps.save_upload(io.BytesIO(good), len(good), "a.mbtiles")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.maps.save_upload(io.BytesIO(good), len(good), "a.mbtiles")
        for i in range(om.MAX_FILES - 1):
            self.maps.save_upload(io.BytesIO(good), len(good), f"m{i}.mbtiles")
        with self.assertRaisesRegex(ValueError, "at most"):
            self.maps.save_upload(io.BytesIO(good), len(good), "extra.mbtiles")
        self.maps.delete("a.mbtiles")
        with patch.object(OfflineMaps, "free_bytes", return_value=om.MIN_FREE_BYTES):
            with self.assertRaisesRegex(ValueError, "free disk space"):
                self.maps.save_upload(io.BytesIO(good), len(good), "b.mbtiles")

    def test_concurrent_upload_refused(self):
        good = make_mbtiles(self.data / "src.mbtiles").read_bytes()
        self.maps._upload_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "in progress"):
                self.maps.save_upload(io.BytesIO(good), len(good), "a.mbtiles")
        finally:
            self.maps._upload_lock.release()

    def test_stale_partials_removed_on_start(self):
        (self.maps.dir / ".upload-abc.partial").write_bytes(b"x")
        OfflineMaps(self.data, max_upload_bytes=1)
        self.assertEqual(os.listdir(self.maps.dir), [])

    def test_delete_validates_name(self):
        with self.assertRaises(ValueError):
            self.maps.delete("../../etc/passwd")
        with self.assertRaises(FileNotFoundError):
            self.maps.delete("missing.mbtiles")


class OfflineMapsServerTests(unittest.TestCase):
    def setUp(self):
        from test_server import ServerHarness

        self.tmp = tempfile.TemporaryDirectory()
        self.harness = ServerHarness(Path(self.tmp.name))
        self.harness.server.offline_maps = OfflineMaps(Path(self.tmp.name) / "data", max_upload_bytes=10 * 1024 * 1024)
        self.maps = self.harness.server.offline_maps
        self.maps.dir.mkdir(parents=True)
        self.origin = f"http://127.0.0.1:{self.harness.port}"

    def tearDown(self):
        self.harness.history._close()
        self.harness.stop()
        self.tmp.cleanup()

    def _post(self, path, body, headers):
        req = urllib.request.Request(self.harness.url(path), data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_upload_requires_same_origin(self):
        data = make_mbtiles(Path(self.tmp.name) / "src.mbtiles").read_bytes()
        status, _ = self._post("/api/map/offline/upload", data, {"Origin": "http://evil.example", "X-Filename": "a.mbtiles"})
        self.assertEqual(status, 403)
        self.assertEqual(os.listdir(self.maps.dir), [])

    def test_read_only_connection_cannot_upload(self):
        self.harness.server.settings = replace(self.harness.settings, bind_host="0.0.0.0", allow_remote_config_write=False)
        data = make_mbtiles(Path(self.tmp.name) / "src.mbtiles").read_bytes()
        status, _ = self._post("/api/map/offline/upload", data, {"Origin": self.origin, "X-Filename": "a.mbtiles"})
        self.assertEqual(status, 403)

    def test_upload_list_tile_delete(self):
        data = make_mbtiles(Path(self.tmp.name) / "src.mbtiles").read_bytes()
        with patch.object(OfflineMaps, "free_bytes", return_value=100 * 1024 ** 3):
            status, payload = self._post("/api/map/offline/upload", data, {"Origin": self.origin, "X-Filename": "area.mbtiles"})
        self.assertEqual(status, 200, payload)
        with urllib.request.urlopen(self.harness.url("/api/map/offline/tile/area.mbtiles/10/297/388"), timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(), PNG)
        status, _ = self._post("/api/map/offline/delete", json.dumps({"name": "area.mbtiles"}).encode(),
                               {"Origin": self.origin, "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(os.listdir(self.maps.dir), [])


if __name__ == "__main__":
    unittest.main()
