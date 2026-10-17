import unittest

from wardragon_console import tracks as tracks_module
from wardragon_console.settings import Settings
from wardragon_console.tracks import TrackBuffer


def drone(id_="drone-1", lat=40.0, lon=-75.0, **extra):
    base = {"id": id_, "track_type": "drone", "lat": lat, "lon": lon, "alt": 50, "speed": 5, "direction": 90}
    base.update(extra)
    return base


class TrackBufferTests(unittest.TestCase):
    def test_trail_accumulates_moving_drone(self):
        buf = TrackBuffer()
        for i in range(5):
            buf.ingest({"drones": [drone(lat=40.0 + i * 0.001)]}, None, None, now=1000 + i * 5)
        track = buf.map_payload()["tracks"][0]
        self.assertEqual(len(track["trail"]), 5)
        self.assertTrue(track["live"])
        self.assertEqual(track["heading"], 90)

    def test_stationary_drone_does_not_grow_trail_until_keepalive(self):
        buf = TrackBuffer()
        for i in range(5):
            buf.ingest({"drones": [drone()]}, None, None, now=1000 + i * 5)
        self.assertEqual(len(buf.map_payload()["tracks"][0]["trail"]), 1)
        buf.ingest({"drones": [drone()]}, None, None, now=1000 + tracks_module.KEEPALIVE_SECONDS + 1)
        self.assertEqual(len(buf.map_payload()["tracks"][0]["trail"]), 2)

    def test_invalid_positions_ignored(self):
        buf = TrackBuffer()
        buf.ingest({"drones": [
            drone("zero", lat=0, lon=0),
            drone("none", lat=None, lon=None),
            drone("bad", lat=95, lon=10),
            drone("nan", lat="nan", lon=1),
            {"lat": 40, "lon": -75},
        ]}, None, None, now=1000)
        self.assertEqual(buf.map_payload()["tracks"], [])

    def test_track_no_longer_reported_is_marked_not_live_then_pruned(self):
        buf = TrackBuffer()
        buf.ingest({"drones": [drone()]}, None, None, now=1000)
        buf.ingest({"drones": []}, None, None, now=1005)
        self.assertFalse(buf.map_payload()["tracks"][0]["live"])
        buf.ingest({"drones": []}, None, None, now=1000 + tracks_module.TRAIL_SECONDS + 1)
        self.assertEqual(buf.map_payload()["tracks"], [])

    def test_dragonsync_unreachable_marks_tracks_not_live(self):
        buf = TrackBuffer()
        buf.ingest({"drones": [drone()]}, None, None, now=1000)
        buf.ingest(None, None, None, now=1005)
        self.assertFalse(buf.map_payload()["tracks"][0]["live"])

    def test_pilot_home_and_aircraft(self):
        buf = TrackBuffer()
        buf.ingest({"drones": [
            drone(pilot_lat=40.01, pilot_lon=-75.01, home_lat=0, home_lon=0, rid={"make": "DJI", "model": "Mini 4"}),
            {"id": "a1b2c3", "track_type": "aircraft", "lat": 40.5, "lon": -75.5, "callsign": "UAL12",
             "ground_speed": 220, "track": 270, "hex": "A1B2C3"},
        ]}, None, None, now=1000)
        by_type = {t["type"]: t for t in buf.map_payload()["tracks"]}
        self.assertEqual(by_type["drone"]["pilot"], {"lat": 40.01, "lon": -75.01})
        self.assertIsNone(by_type["drone"]["home"])
        self.assertEqual(by_type["drone"]["label"], "DJI Mini 4")
        self.assertEqual(by_type["aircraft"]["label"], "UAL12")
        self.assertEqual(by_type["aircraft"]["heading"], 270)

    def test_kit_and_signals(self):
        buf = TrackBuffer()
        buf.ingest(
            {"drones": []},
            {"signals": [{"uid": "s1", "signal_type": "fpv", "lat": 40, "lon": -75, "radius_m": 300, "center_hz": 5800e6},
                         {"uid": "s2", "lat": None, "lon": None}]},
            {"lat": 40.1, "lon": -75.1, "alt": 10, "gps_fix": True},
            now=1000,
        )
        payload = buf.map_payload()
        self.assertEqual(payload["kit"]["lat"], 40.1)
        self.assertEqual(len(payload["signals"]), 1)
        self.assertEqual(payload["signals"][0]["freq_mhz"], 5800.0)

    def test_track_count_is_capped(self):
        buf = TrackBuffer()
        many = [drone(f"d{i}", lat=40 + i * 1e-4) for i in range(tracks_module.MAX_TRACKS + 50)]
        buf.ingest({"drones": many}, None, None, now=1000)
        self.assertEqual(len(buf.map_payload()["tracks"]), tracks_module.MAX_TRACKS)


class TileOriginTests(unittest.TestCase):
    def test_default_osm(self):
        self.assertEqual(Settings().map_tile_origin(), "https://tile.openstreetmap.org")

    def test_rejects_unusable_or_unsafe_urls(self):
        for url in ("", "https://example.com/tiles.png", "file:///x/{z}/{x}/{y}", "https://a.com;x/{z}/{x}/{y}"):
            self.assertEqual(Settings(map_tile_url=url).map_tile_origin(), "", url)


if __name__ == "__main__":
    unittest.main()
