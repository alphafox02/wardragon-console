import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from wardragon_console import history as history_module
from wardragon_console.export import to_csv, to_kml
from wardragon_console.history import HistoryStore
from wardragon_console.tracks import TrackBuffer

KIT = {"lat": 40.0, "lon": -75.0}
T0 = time.time() - 600
WIDE = (T0 - 40 * 86400, T0 + 40 * 86400)


def drone(id_="d1", lat=40.001, lon=-75.0, **extra):
    base = {"id": id_, "track_type": "drone", "lat": lat, "lon": lon, "alt": 50, "speed": 5, "direction": 90,
            "rid": {"make": "DJI", "model": "Mini"}}
    base.update(extra)
    return base


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "data"
        self.store = HistoryStore(self.dir, max_bytes=500 * 1024 * 1024)
        self.tracks = TrackBuffer()

    def tearDown(self):
        self.store._close()
        self.tmp.cleanup()

    def feed(self, drones, now, kit=KIT, signals=None):
        recorded = self.tracks.ingest({"drones": drones}, {"signals": signals or []}, dict(kit, gps_fix=True) if kit else None, now=now)
        current_kit, current_signals = self.tracks.context()
        self.store.record(recorded, current_kit, current_signals, now=now)

    def enable(self, **extra):
        self.store.update_settings({"enabled": True, **extra})


class SettingsTests(HistoryTestCase):
    def test_off_by_default_and_records_nothing(self):
        self.feed([drone()], now=T0)
        self.store.flush()
        self.assertFalse(self.store.status()["enabled"])
        self.assertFalse(self.store.db_path.exists())

    def test_settings_persist_across_restart(self):
        self.enable(retention_days=30, include_aircraft=True)
        reloaded = HistoryStore(self.dir, max_bytes=1)
        self.assertTrue(reloaded.settings["enabled"])
        self.assertEqual(reloaded.settings["retention_days"], 30)
        self.assertTrue(reloaded.settings["include_aircraft"])

    def test_invalid_settings_rejected(self):
        for bad in ({"enabled": "yes"}, {"retention_days": 9}, {"retention_days": True}, {"bogus": 1}):
            with self.assertRaises(ValueError):
                self.store.update_settings(bad)
        self.assertFalse(self.store.settings["enabled"])

    def test_corrupt_settings_file_falls_back_to_defaults(self):
        self.dir.mkdir(parents=True)
        (self.dir / "map-history.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(HistoryStore(self.dir, max_bytes=1).settings["enabled"])


class RecordingTests(HistoryTestCase):
    def test_flight_summary(self):
        self.enable()
        for i in range(5):
            self.feed([drone(lat=40.001 + i * 0.001, alt=50 + i * 10, pilot_lat=40.0005, pilot_lon=-75.0005)], now=T0 + i * 5)
        result = self.store.query(*WIDE)
        self.assertEqual(len(result["flights"]), 1)
        flight = result["flights"][0]
        self.assertEqual(flight["points"], 5)
        self.assertEqual(flight["max_alt"], 90)
        self.assertTrue(flight["pilot_seen"])
        self.assertEqual(flight["make_model"], "DJI Mini")
        self.assertAlmostEqual(flight["min_kit_dist"], 111, delta=2)
        self.assertEqual(len(flight["path"]), 5)

    def test_gap_starts_new_flight(self):
        self.enable()
        self.feed([drone()], now=T0)
        self.feed([drone(lat=40.002)], now=T0 + history_module.FLIGHT_GAP_SECONDS + 60)
        self.assertEqual(len(self.store.query(*WIDE)["flights"]), 2)

    def test_continues_flight_after_console_restart(self):
        self.enable()
        self.feed([drone()], now=T0)
        self.store.flush()
        self.store._close()
        restarted = HistoryStore(self.dir, max_bytes=500 * 1024 * 1024)
        tracks = TrackBuffer()
        recorded = tracks.ingest({"drones": [drone(lat=40.003)]}, None, None, now=T0 + 30)
        restarted.record(recorded, None, [], now=T0 + 30)
        self.assertEqual(len(restarted.query(*WIDE)["flights"]), 1)
        restarted._close()

    def test_aircraft_only_when_enabled(self):
        self.enable()
        plane = {"id": "abc", "track_type": "aircraft", "lat": 40.1, "lon": -75.1, "callsign": "UAL1"}
        self.feed([drone(), plane], now=T0)
        types = {f["type"] for f in self.store.query(*WIDE)["flights"]}
        self.assertEqual(types, {"drone"})
        self.store.update_settings({"include_aircraft": True})
        self.feed([drone(), dict(plane, lat=40.2)], now=T0 + 10)
        types = {f["type"] for f in self.store.query(*WIDE)["flights"]}
        self.assertEqual(types, {"drone", "aircraft"})

    def test_signals_and_kit_recorded(self):
        self.enable()
        self.feed([], now=T0, signals=[{"uid": "s1", "signal_type": "fpv", "lat": 40, "lon": -75, "radius_m": 300}])
        result = self.store.query(*WIDE)
        self.assertEqual(len(result["signals"]), 1)
        self.assertEqual(result["kit"], {"lat": 40.0, "lon": -75.0})

    def test_retention_prunes_old_data(self):
        self.enable(retention_days=1)
        self.feed([drone("old")], now=T0 - 2 * 86400)
        self.feed([drone("new", lat=40.01)], now=T0)
        self.store.flush()
        self.store._prune(self.store._connect(), now=T0)
        ids = [f["track_id"] for f in self.store.query(*WIDE)["flights"]]
        self.assertEqual(ids, ["new"])

    def test_old_data_ages_out_while_recording_is_off(self):
        self.enable(retention_days=1)
        self.feed([drone("old")], now=T0 - 2 * 86400)
        self.store.flush()
        self.store.update_settings({"enabled": False})
        self.store._last_prune = 0
        self.store.record([], None, [], now=T0)
        self.assertEqual(self.store.query(*WIDE)["flights"], [])

    def test_shorter_retention_prunes_immediately(self):
        self.enable(retention_days=7)
        self.feed([drone("old")], now=time.time() - 3 * 86400)
        self.feed([drone("new", lat=40.01)], now=time.time())
        self.store.flush()
        self.store.update_settings({"retention_days": 1})
        self.assertEqual([f["track_id"] for f in self.store.query(*WIDE)["flights"]], ["new"])

    def test_size_cap_drops_oldest_flights(self):
        self.enable()
        for i in range(40):
            self.feed([drone(f"d{i}", lat=40 + i * 0.01)], now=T0 + i * 400)
        self.store.flush()
        self.store.max_bytes = 1
        self.store._prune(self.store._connect(), now=T0 + 40 * 400)
        self.assertEqual(self.store.query(*WIDE)["flights"], [])

    def test_disk_error_pauses_history_without_raising(self):
        self.enable()
        with patch.object(HistoryStore, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")):
            self.feed([drone()], now=T0)
            self.store.flush()
        self.assertIn("disk I/O error", self.store.status()["error"])
        self.feed([drone(lat=40.01)], now=T0 + 10)

    def test_newer_schema_is_left_untouched(self):
        self.dir.mkdir(parents=True)
        conn = sqlite3.connect(self.dir / "history.sqlite")
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        self.enable()
        self.feed([drone()], now=T0)
        self.store.flush()
        self.assertIn("newer console", self.store.status()["error"])
        conn = sqlite3.connect(self.dir / "history.sqlite")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 99)
        conn.close()

    def test_clear_removes_database(self):
        self.enable()
        self.feed([drone()], now=T0)
        self.store.flush()
        self.store.clear()
        self.assertFalse(self.store.db_path.exists())
        self.assertEqual(self.store.query(*WIDE)["flights"], [])
        self.assertTrue(self.store.settings["enabled"])

    def test_flight_detail(self):
        self.enable()
        for i in range(3):
            self.feed([drone(lat=40.001 + i * 0.001, home_lat=40.0, home_lon=-75.0)], now=T0 + i * 5)
        flight_id = self.store.query(*WIDE)["flights"][0]["id"]
        detail = self.store.flight(flight_id)
        self.assertEqual(len(detail["path"]), 3)
        self.assertEqual(detail["home"], {"lat": 40.0, "lon": -75.0})
        self.assertIsNone(self.store.flight(99999))


class ExportTests(HistoryTestCase):
    def test_csv_neutralizes_formulas(self):
        self.enable()
        self.feed([drone(description='=HYPERLINK("http://x","y")', operator_id="+cmd")], now=T0)
        rows, truncated = self.store.export_points(*WIDE)
        text = to_csv(rows).decode()
        self.assertFalse(truncated)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'+cmd", text)
        self.assertTrue(text.startswith("time_utc,flight,type,id,label"))

    def test_kml_escapes_and_has_track(self):
        self.enable()
        for i in range(2):
            self.feed([drone(description="<script>x</script>", lat=40.001 + i * 0.001, pilot_lat=40.0, pilot_lon=-75.0)], now=T0 + i * 5)
        rows, _ = self.store.export_points(*WIDE)
        kml = to_kml(rows, "test").decode()
        self.assertNotIn("<script>", kml)
        self.assertIn("&lt;script&gt;", kml)
        self.assertEqual(kml.count("<when>"), 2)
        self.assertIn("Pilot of", kml)

    def test_export_single_flight(self):
        self.enable()
        self.feed([drone("a"), drone("b", lat=40.01)], now=T0)
        flights = self.store.query(*WIDE)["flights"]
        rows, _ = self.store.export_points(*WIDE, flights[0]["id"])
        self.assertEqual({r.track_id for r in rows}, {flights[0]["track_id"]})


if __name__ == "__main__":
    unittest.main()
