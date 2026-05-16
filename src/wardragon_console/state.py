from __future__ import annotations

import copy
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import __version__
from .network import ipv4_interfaces


@dataclass(frozen=True)
class SourceTiming:
    stale_after: float
    grace_seconds: float


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._data: dict[str, dict[str, Any]] = {
            "monitor": {"payload": None, "seen_at": None, "error": None},
            "droneid": {"payload": None, "seen_at": None, "error": None},
            "dragonsig": {"payload": None, "seen_at": None, "error": None},
            "dragonsync_status": {"payload": None, "seen_at": None, "error": None},
            "drones": {"payload": {"drones": []}, "seen_at": None, "error": None},
            "signals": {"payload": {"signals": []}, "seen_at": None, "error": None},
            "updates": {"payload": None, "seen_at": None, "error": None},
        }

    @property
    def started_at(self) -> float:
        return self._started_at

    def update(self, key: str, payload: Any) -> None:
        with self._lock:
            self._data[key] = {"payload": payload, "seen_at": time.time(), "error": None}

    def set_error(self, key: str, error: str) -> None:
        with self._lock:
            current = self._data.setdefault(key, {"payload": None, "seen_at": None, "error": None})
            current["error"] = error

    def snapshot(self, timing: SourceTiming) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            data = copy.deepcopy(self._data)

        services = {
            "monitor": self._service_state(data["monitor"]["seen_at"], now, timing),
            "droneid": self._service_state(data["droneid"]["seen_at"], now, timing),
            "dragonsig": self._service_state(data["dragonsig"]["seen_at"], now, timing),
        }

        status = data["dragonsync_status"]["payload"] or {}
        drones = data["drones"]["payload"] or {"drones": []}
        signals = data["signals"]["payload"] or {"signals": []}

        return {
            "generated_at": now,
            "console": {
                "version": __version__,
                "hostname": socket.gethostname(),
                "uptime_seconds": now - self._started_at,
                "interfaces": ipv4_interfaces(),
            },
            "services": services,
            "monitor": data["monitor"],
            "droneid": data["droneid"],
            "dragonsig": data["dragonsig"],
            "dragonsync": {
                "status": data["dragonsync_status"],
                "drones": data["drones"],
                "signals": data["signals"],
            },
            "summary": {
                "kit_id": status.get("kit_id") or status.get("uid") or "",
                "gps_fix": _coerce_bool(status.get("gps_fix")),
                "drone_count": len(drones.get("drones", [])) if isinstance(drones, dict) else 0,
                "signal_count": len(signals.get("signals", [])) if isinstance(signals, dict) else 0,
            },
            "updates": data["updates"]["payload"] or {},
        }

    def _service_state(self, seen_at: float | None, now: float, timing: SourceTiming) -> dict[str, Any]:
        if seen_at is None:
            age_from_start = now - self._started_at
            state = "STARTING" if age_from_start < timing.grace_seconds else "NOT_PRESENT"
            return {"state": state, "seen_at": None, "age_seconds": None}

        age = now - seen_at
        state = "HEALTHY" if age <= timing.stale_after else "DEGRADED"
        return {"state": state, "seen_at": seen_at, "age_seconds": age}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)
