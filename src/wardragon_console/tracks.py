"""In-memory recent tracks for the Map tab, fed by the DragonSync poller."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

TRAIL_SECONDS = 1800.0
TRAIL_MAX_POINTS = 720
MAX_TRACKS = 500
MIN_MOVE_METERS = 2.0
KEEPALIVE_SECONDS = 30.0


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _position(lat: Any, lon: Any) -> tuple[float, float] | None:
    lat_f, lon_f = _num(lat), _num(lon)
    if lat_f is None or lon_f is None:
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    if abs(lat_f) < 1e-7 and abs(lon_f) < 1e-7:
        return None
    return lat_f, lon_f


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def _point(lat: Any, lon: Any) -> dict[str, float] | None:
    pos = _position(lat, lon)
    return {"lat": pos[0], "lon": pos[1]} if pos else None


class TrackBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tracks: dict[str, dict[str, Any]] = {}
        self._kit: dict[str, Any] | None = None
        self._signals: list[dict[str, Any]] = []
        self._updated_at: float | None = None

    def ingest(
        self,
        drones: dict[str, Any] | None,
        signals: dict[str, Any] | None,
        status: dict[str, Any] | None,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Update tracks; returns a snapshot of each track that gained a trail point."""
        now = time.time() if now is None else now
        recorded: list[dict[str, Any]] = []
        with self._lock:
            if isinstance(drones, dict):
                seen: set[str] = set()
                for raw in drones.get("drones") or []:
                    if isinstance(raw, dict):
                        key, appended = self._ingest_track(raw, now)
                        if key:
                            seen.add(key)
                        if appended:
                            snap = {k: v for k, v in self._tracks[key].items() if k != "trail"}
                            snap["t"] = now
                            recorded.append(snap)
                for key, track in self._tracks.items():
                    track["live"] = key in seen
            else:
                for track in self._tracks.values():
                    track["live"] = False
            if isinstance(signals, dict):
                self._signals = [s for s in (self._signal(raw) for raw in signals.get("signals") or []) if s]
            if isinstance(status, dict):
                pos = _position(status.get("lat"), status.get("lon"))
                self._kit = {
                    "lat": pos[0], "lon": pos[1], "alt": _num(status.get("alt")),
                    "gps_fix": bool(status.get("gps_fix")),
                } if pos else None
            self._prune(now)
            self._updated_at = now
        return recorded

    def _ingest_track(self, raw: dict[str, Any], now: float) -> tuple[str | None, bool]:
        track_type = "aircraft" if raw.get("track_type") == "aircraft" else "drone"
        track_id = str(raw.get("id") or raw.get("hex") or "").strip()
        pos = _position(raw.get("lat"), raw.get("lon"))
        if not track_id or pos is None:
            return None, False
        key = f"{track_type}:{track_id}"
        track = self._tracks.get(key)
        if track is None:
            if len(self._tracks) >= MAX_TRACKS:
                return None, False
            track = {"key": key, "trail": deque(maxlen=TRAIL_MAX_POINTS)}
            self._tracks[key] = track
        trail: deque = track["trail"]
        appended = False
        if not trail or _distance_m((trail[-1][1], trail[-1][2]), pos) >= MIN_MOVE_METERS \
                or now - trail[-1][0] >= KEEPALIVE_SECONDS:
            trail.append((now, pos[0], pos[1]))
            appended = True
        track.update(self._describe(raw, track_type, track_id, pos))
        track["last_seen"] = now
        track["live"] = True
        return key, appended

    @staticmethod
    def _describe(raw: dict[str, Any], track_type: str, track_id: str, pos: tuple[float, float]) -> dict[str, Any]:
        if track_type == "aircraft":
            label = (raw.get("callsign") or raw.get("flight") or raw.get("reg") or raw.get("hex") or track_id)
            return {
                "id": track_id, "type": "aircraft", "label": str(label).strip(),
                "lat": pos[0], "lon": pos[1], "alt": _num(raw.get("alt")),
                "speed": _num(raw.get("ground_speed")), "heading": _num(raw.get("track")),
                "detail": {k: raw.get(k) for k in ("hex", "squawk", "reg", "category", "on_ground") if raw.get(k) not in (None, "")},
                "pilot": None, "home": None,
            }
        rid = raw.get("rid") if isinstance(raw.get("rid"), dict) else {}
        make_model = " ".join(str(v) for v in (rid.get("make"), rid.get("model")) if v)
        return {
            "id": track_id, "type": "drone",
            "label": str(raw.get("description") or make_model or track_id).strip(),
            "lat": pos[0], "lon": pos[1], "alt": _num(raw.get("alt")),
            "speed": _num(raw.get("speed")), "heading": _num(raw.get("direction")),
            "detail": {
                k: v for k, v in {
                    "make_model": make_model, "ua_type": raw.get("ua_type_name"),
                    "transport": raw.get("transport"), "rssi": raw.get("rssi"),
                    "height": _num(raw.get("height")), "operator_id": raw.get("operator_id"),
                }.items() if v not in (None, "")
            },
            "pilot": _point(raw.get("pilot_lat"), raw.get("pilot_lon")),
            "home": _point(raw.get("home_lat"), raw.get("home_lon")),
        }

    @staticmethod
    def _signal(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        pos = _position(raw.get("lat"), raw.get("lon"))
        if pos is None:
            return None
        signal_type = str(raw.get("signal_type") or "signal")
        center_hz = _num(raw.get("center_hz"))
        return {
            "id": str(raw.get("uid") or ""),
            "label": str(raw.get("callsign") or f"{signal_type.upper()} signal"),
            "type": signal_type,
            "lat": pos[0], "lon": pos[1],
            "radius_m": _num(raw.get("radius_m")),
            "freq_mhz": round(center_hz / 1e6, 3) if center_hz else None,
        }

    def _prune(self, now: float) -> None:
        cutoff = now - TRAIL_SECONDS
        for key in list(self._tracks):
            trail: deque = self._tracks[key]["trail"]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self._tracks[key]

    def context(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self._lock:
            return (dict(self._kit) if self._kit else None), [dict(s) for s in self._signals]

    def map_payload(self) -> dict[str, Any]:
        with self._lock:
            tracks = []
            for track in self._tracks.values():
                item = {k: v for k, v in track.items() if k != "trail"}
                item["trail"] = [[lat, lon] for _, lat, lon in track["trail"]]
                tracks.append(item)
            tracks.sort(key=lambda t: t.get("last_seen") or 0, reverse=True)
            return {
                "updated_at": self._updated_at,
                "kit": dict(self._kit) if self._kit else None,
                "tracks": tracks,
                "signals": [dict(s) for s in self._signals],
            }
