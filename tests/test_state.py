import time
import unittest

from wardragon_console.state import SnapshotStore, SourceTiming


class SnapshotStoreTests(unittest.TestCase):
    def test_service_state_transitions_from_starting_to_not_present(self):
        store = SnapshotStore()
        snapshot = store.snapshot(SourceTiming(stale_after=60, grace_seconds=999))
        self.assertEqual(snapshot["services"]["droneid"]["state"], "STARTING")

        snapshot = store.snapshot(SourceTiming(stale_after=60, grace_seconds=-1))
        self.assertEqual(snapshot["services"]["droneid"]["state"], "NOT_PRESENT")

    def test_seen_source_goes_healthy_then_degraded(self):
        store = SnapshotStore()
        store.update("droneid", {"healthy": True})
        snapshot = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
        self.assertEqual(snapshot["services"]["droneid"]["state"], "HEALTHY")

        with store._lock:
            store._data["droneid"]["seen_at"] = time.time() - 120
        snapshot = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
        self.assertEqual(snapshot["services"]["droneid"]["state"], "DEGRADED")

    def test_drone_and_signal_counts_use_dragonsync_payloads(self):
        store = SnapshotStore()
        store.update("drones", {"drones": [{"id": "a"}, {"id": "b"}]})
        store.update("signals", {"signals": [{"id": "s"}]})
        snapshot = store.snapshot(SourceTiming(stale_after=60, grace_seconds=0))
        self.assertEqual(snapshot["summary"]["drone_count"], 2)
        self.assertEqual(snapshot["summary"]["signal_count"], 1)


if __name__ == "__main__":
    unittest.main()
