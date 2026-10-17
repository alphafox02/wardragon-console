"""Optional on-disk history for the Map tab. Off unless the operator turns it on."""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator, NamedTuple

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FLIGHT_GAP_SECONDS = 300.0
FLUSH_SECONDS = 60.0
FLUSH_MAX_ROWS = 2000
PRUNE_SECONDS = 3600.0
RETRY_SECONDS = 600.0
KIT_MOVE_METERS = 25.0
RETENTION_CHOICES = (1, 7, 30)
DEFAULT_SETTINGS = {"enabled": False, "retention_days": 7, "include_aircraft": False}
QUERY_MAX_FLIGHTS = 500
QUERY_DRAWN_FLIGHTS = 100
OVERVIEW_POINTS = 200
FLIGHT_MAX_POINTS = 20000
EXPORT_MAX_POINTS = 50000

_SCHEMA = """
CREATE TABLE flights (
    id INTEGER PRIMARY KEY,
    track_key TEXT NOT NULL,
    type TEXT NOT NULL,
    track_id TEXT NOT NULL,
    label TEXT,
    make_model TEXT,
    ua_type TEXT,
    transport TEXT,
    operator_id TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    max_alt REAL,
    min_kit_dist REAL,
    pilot_seen INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX flights_last_seen ON flights(last_seen);
CREATE INDEX flights_key ON flights(track_key, last_seen);
CREATE TABLE points (
    flight_id INTEGER NOT NULL,
    t REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    alt REAL,
    speed REAL,
    heading REAL,
    pilot_lat REAL,
    pilot_lon REAL,
    home_lat REAL,
    home_lon REAL,
    rssi REAL
);
CREATE INDEX points_flight ON points(flight_id, t);
CREATE INDEX points_t ON points(t);
CREATE TABLE signals (
    id INTEGER PRIMARY KEY,
    uid TEXT,
    label TEXT,
    type TEXT,
    lat REAL,
    lon REAL,
    radius_m REAL,
    freq_mhz REAL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE INDEX signals_last_seen ON signals(last_seen);
CREATE TABLE kit (
    t REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);
CREATE INDEX kit_t ON kit(t);
"""


class ExportRow(NamedTuple):
    t: float
    flight_id: int
    type: str
    track_id: str
    label: str | None
    make_model: str | None
    ua_type: str | None
    transport: str | None
    operator_id: str | None
    lat: float
    lon: float
    alt: float | None
    speed: float | None
    heading: float | None
    pilot_lat: float | None
    pilot_lon: float | None
    home_lat: float | None
    home_lon: float | None
    rssi: float | None


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def _decimate(rows: list[Any], limit: int) -> list[Any]:
    if len(rows) <= limit:
        return rows
    step = len(rows) / (limit - 1)
    picked = [rows[int(i * step)] for i in range(limit - 1)]
    picked.append(rows[-1])
    return picked


class HistoryStore:
    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / "history.sqlite"
        self.settings_path = data_dir / "map-history.json"
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._pending_points: list[dict[str, Any]] = []
        self._pending_signals: dict[str, dict[str, Any]] = {}
        self._pending_kit: list[tuple[float, float, float]] = []
        self._open_flights: dict[str, tuple[int, float]] = {}
        self._last_kit: tuple[float, float] | None = None
        self._last_flush = time.monotonic()
        self._last_prune = 0.0
        self._failed_at: float | None = None
        self.error: str | None = None
        self.settings = self._load_settings()


    def _load_settings(self) -> dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, ValueError) as exc:
            LOG.warning("ignoring unreadable %s: %s", self.settings_path, exc)
            return settings
        if isinstance(raw, dict):
            try:
                settings.update(self._validate(raw))
            except ValueError as exc:
                LOG.warning("ignoring invalid %s: %s", self.settings_path, exc)
        return settings

    @staticmethod
    def _validate(changes: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in changes.items():
            if key in ("enabled", "include_aircraft"):
                if not isinstance(value, bool):
                    raise ValueError(f"'{key}' must be true or false")
                out[key] = value
            elif key == "retention_days":
                if not isinstance(value, int) or isinstance(value, bool) or value not in RETENTION_CHOICES:
                    raise ValueError(f"'retention_days' must be one of {list(RETENTION_CHOICES)}")
                out[key] = value
            else:
                raise ValueError(f"unknown setting '{key}'")
        return out

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.settings,
                "retention_choices": list(RETENTION_CHOICES),
                "error": self.error,
                "size_bytes": self._file_size(),
                "max_bytes": self.max_bytes,
                "oldest": self._oldest(),
                "pending": len(self._pending_points),
            }

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("JSON body must be an object")
        valid = self._validate(changes)
        with self._lock:
            new = {**self.settings, **valid}
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".map-history.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(new, handle)
                os.chmod(tmp, 0o640)
                os.replace(tmp, self.settings_path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            was_enabled = self.settings["enabled"]
            shorter = new["retention_days"] < self.settings["retention_days"]
            self.settings = new
            if was_enabled and not new["enabled"]:
                self._flush_locked()
                self._close()
            if new["enabled"]:
                self.error = None
                self._failed_at = None
            if shorter:
                self._prune_if_due(time.time(), force=True)
        return self.status()

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._pending_points.clear()
            self._pending_signals.clear()
            self._pending_kit.clear()
            self._open_flights.clear()
            self._last_kit = None
            self._close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(f"{self.db_path}{suffix}")
                except FileNotFoundError:
                    pass
            self.error = None
            self._failed_at = None
        return self.status()


    def record(
        self,
        points: list[dict[str, Any]],
        kit: dict[str, Any] | None,
        signals: list[dict[str, Any]],
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if not self.settings["enabled"]:
                self._prune_if_due(now)
                return
            if self.error is not None:
                if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_SECONDS:
                    return
                self.error = None
            include_aircraft = self.settings["include_aircraft"]
            for point in points:
                if point.get("type") == "aircraft" and not include_aircraft:
                    continue
                self._pending_points.append(dict(point, kit_lat=kit["lat"] if kit else None, kit_lon=kit["lon"] if kit else None))
            if kit and (self._last_kit is None or _distance_m(*self._last_kit, kit["lat"], kit["lon"]) >= KIT_MOVE_METERS):
                self._pending_kit.append((now, kit["lat"], kit["lon"]))
                self._last_kit = (kit["lat"], kit["lon"])
            for signal in signals:
                uid = signal.get("id") or f"{signal.get('lat')},{signal.get('lon')}"
                pending = self._pending_signals.get(uid)
                if pending is None:
                    self._pending_signals[uid] = dict(signal, first_seen=now, last_seen=now)
                else:
                    pending["last_seen"] = now
            due = time.monotonic() - self._last_flush >= FLUSH_SECONDS
            if due or len(self._pending_points) >= FLUSH_MAX_ROWS:
                self._flush_locked(now)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._last_flush = time.monotonic()
        if not (self._pending_points or self._pending_signals or self._pending_kit):
            return
        try:
            conn = self._connect()
            with conn:
                for point in self._pending_points:
                    self._write_point(conn, point)
                for signal in self._pending_signals.values():
                    self._write_signal(conn, signal)
                conn.executemany("INSERT INTO kit (t, lat, lon) VALUES (?, ?, ?)", self._pending_kit)
        except (sqlite3.Error, OSError) as exc:
            self._fail(f"history paused: {exc}")
        finally:
            self._pending_points.clear()
            self._pending_signals.clear()
            self._pending_kit.clear()
        self._prune_if_due(now)

    def _prune_if_due(self, now: float, force: bool = False) -> None:
        # Old data ages out even while recording is switched off.
        if not force and now - self._last_prune < PRUNE_SECONDS:
            return
        self._last_prune = now
        conn = self._reader()
        if conn is None:
            return
        try:
            self._prune(conn, now)
        except sqlite3.Error as exc:
            self._fail(f"history paused: {exc}")

    def _write_point(self, conn: sqlite3.Connection, point: dict[str, Any]) -> None:
        t = point["t"]
        key = point["key"]
        detail = point.get("detail") or {}
        pilot = point.get("pilot") or {}
        home = point.get("home") or {}
        dist = None
        if point.get("kit_lat") is not None:
            dist = _distance_m(point["kit_lat"], point["kit_lon"], point["lat"], point["lon"])
        flight_id = self._flight_for(conn, point, t)
        conn.execute(
            "INSERT INTO points (flight_id, t, lat, lon, alt, speed, heading, pilot_lat, pilot_lon, home_lat, home_lon, rssi)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (flight_id, t, point["lat"], point["lon"], _num(point.get("alt")), _num(point.get("speed")),
             _num(point.get("heading")), pilot.get("lat"), pilot.get("lon"), home.get("lat"), home.get("lon"),
             _num(detail.get("rssi"))),
        )
        conn.execute(
            "UPDATE flights SET last_seen = MAX(last_seen, ?), label = ?, points = points + 1,"
            " max_alt = CASE WHEN ? IS NULL THEN max_alt WHEN max_alt IS NULL OR ? > max_alt THEN ? ELSE max_alt END,"
            " min_kit_dist = CASE WHEN ? IS NULL THEN min_kit_dist WHEN min_kit_dist IS NULL OR ? < min_kit_dist THEN ? ELSE min_kit_dist END,"
            " pilot_seen = MAX(pilot_seen, ?),"
            " make_model = COALESCE(NULLIF(?, ''), make_model), operator_id = COALESCE(NULLIF(?, ''), operator_id)"
            " WHERE id = ?",
            (t, point.get("label"), *([_num(point.get("alt"))] * 3), dist, dist, dist, 1 if pilot else 0,
             str(detail.get("make_model") or ""), str(detail.get("operator_id") or ""), flight_id),
        )
        self._open_flights[key] = (flight_id, t)

    def _flight_for(self, conn: sqlite3.Connection, point: dict[str, Any], t: float) -> int:
        key = point["key"]
        known = self._open_flights.get(key)
        if known and t - known[1] <= FLIGHT_GAP_SECONDS:
            return known[0]
        row = conn.execute(
            "SELECT id FROM flights WHERE track_key = ? AND last_seen >= ? ORDER BY last_seen DESC LIMIT 1",
            (key, t - FLIGHT_GAP_SECONDS),
        ).fetchone()
        if row:
            return int(row[0])
        detail = point.get("detail") or {}
        cur = conn.execute(
            "INSERT INTO flights (track_key, type, track_id, label, make_model, ua_type, transport, operator_id, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, point.get("type"), point.get("id"), point.get("label"), detail.get("make_model"),
             detail.get("ua_type"), detail.get("transport"), detail.get("operator_id"), t, t),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _write_signal(conn: sqlite3.Connection, signal: dict[str, Any]) -> None:
        row = conn.execute(
            "SELECT id FROM signals WHERE uid = ? AND last_seen >= ? ORDER BY last_seen DESC LIMIT 1",
            (signal.get("id"), signal["first_seen"] - FLIGHT_GAP_SECONDS),
        ).fetchone()
        if row:
            conn.execute("UPDATE signals SET last_seen = MAX(last_seen, ?) WHERE id = ?", (signal["last_seen"], row[0]))
            return
        conn.execute(
            "INSERT INTO signals (uid, label, type, lat, lon, radius_m, freq_mhz, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (signal.get("id"), signal.get("label"), signal.get("type"), signal.get("lat"), signal.get("lon"),
             signal.get("radius_m"), signal.get("freq_mhz"), signal["first_seen"], signal["last_seen"]),
        )

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.settings["retention_days"] * 86400
        with conn:
            conn.execute("DELETE FROM points WHERE t < ?", (cutoff,))
            conn.execute("DELETE FROM flights WHERE last_seen < ?", (cutoff,))
            conn.execute("DELETE FROM signals WHERE last_seen < ?", (cutoff,))
            conn.execute("DELETE FROM kit WHERE t < ?", (cutoff,))
        # Free pages are reused by SQLite, so cap on bytes in use, not file size.
        while self._used_bytes(conn) > self.max_bytes:
            row = conn.execute("SELECT COUNT(*) FROM flights").fetchone()
            if not row or not row[0]:
                break
            drop = max(1, row[0] // 10)
            with conn:
                conn.execute(
                    "DELETE FROM points WHERE flight_id IN (SELECT id FROM flights ORDER BY last_seen LIMIT ?)", (drop,)
                )
                conn.execute("DELETE FROM flights WHERE id IN (SELECT id FROM flights ORDER BY last_seen LIMIT ?)", (drop,))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @staticmethod
    def _used_bytes(conn: sqlite3.Connection) -> int:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return int((pages - free) * page_size)


    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5, check_same_thread=False)
        try:
            os.chmod(self.db_path, 0o640)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                with conn:
                    conn.executescript(_SCHEMA)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version > SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"history was written by a newer console (format {version}); leaving it untouched"
                )
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        return conn

    def _reader(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if not self.db_path.exists():
            return None
        try:
            return self._connect()
        except (sqlite3.Error, OSError) as exc:
            self._fail(f"history unavailable: {exc}")
            return None

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        self._open_flights.clear()

    def _fail(self, message: str) -> None:
        LOG.warning("%s", message)
        self.error = message
        self._failed_at = time.monotonic()
        self._close()

    def _file_size(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(f"{self.db_path}{suffix}")
            except OSError:
                pass
        return total

    def _oldest(self) -> float | None:
        conn = self._reader()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT MIN(first_seen) FROM flights").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None


    def query(self, start: float, end: float) -> dict[str, Any]:
        with self._lock:
            self._flush_locked()
            conn = self._reader()
            if conn is None:
                return {"flights": [], "signals": [], "kit": None, "truncated": False}
            try:
                flights = [self._flight_row(r) for r in conn.execute(
                    "SELECT id, type, track_id, label, make_model, ua_type, transport, operator_id, first_seen,"
                    " last_seen, max_alt, min_kit_dist, pilot_seen, points FROM flights"
                    " WHERE last_seen >= ? AND first_seen <= ? ORDER BY last_seen DESC LIMIT ?",
                    (start, end, QUERY_MAX_FLIGHTS + 1),
                )]
                truncated = len(flights) > QUERY_MAX_FLIGHTS
                flights = flights[:QUERY_MAX_FLIGHTS]
                for flight in flights[:QUERY_DRAWN_FLIGHTS]:
                    rows = conn.execute(
                        "SELECT lat, lon FROM points WHERE flight_id = ? AND t BETWEEN ? AND ? ORDER BY t",
                        (flight["id"], start, end),
                    ).fetchall()
                    flight["path"] = [[r[0], r[1]] for r in _decimate(rows, OVERVIEW_POINTS)]
                signals = [
                    {"label": r[0], "type": r[1], "lat": r[2], "lon": r[3], "radius_m": r[4], "freq_mhz": r[5],
                     "first_seen": r[6], "last_seen": r[7]}
                    for r in conn.execute(
                        "SELECT label, type, lat, lon, radius_m, freq_mhz, first_seen, last_seen FROM signals"
                        " WHERE last_seen >= ? AND first_seen <= ? ORDER BY last_seen DESC LIMIT 500",
                        (start, end),
                    )
                ]
                kit_row = conn.execute(
                    "SELECT t, lat, lon FROM kit WHERE t <= ? ORDER BY t DESC LIMIT 1", (end,)
                ).fetchone()
            except sqlite3.Error as exc:
                self._fail(f"history unavailable: {exc}")
                return {"flights": [], "signals": [], "kit": None, "truncated": False}
            kit = {"lat": kit_row[1], "lon": kit_row[2]} if kit_row else None
            return {"flights": flights, "signals": signals, "kit": kit, "truncated": truncated}

    def flight(self, flight_id: int) -> dict[str, Any] | None:
        with self._lock:
            self._flush_locked()
            conn = self._reader()
            if conn is None:
                return None
            try:
                row = conn.execute(
                    "SELECT id, type, track_id, label, make_model, ua_type, transport, operator_id, first_seen,"
                    " last_seen, max_alt, min_kit_dist, pilot_seen, points FROM flights WHERE id = ?",
                    (flight_id,),
                ).fetchone()
                if row is None:
                    return None
                flight = self._flight_row(row)
                points = conn.execute(
                    "SELECT t, lat, lon, alt, speed, heading, pilot_lat, pilot_lon, home_lat, home_lon FROM points"
                    " WHERE flight_id = ? ORDER BY t",
                    (flight_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                self._fail(f"history unavailable: {exc}")
                return None
        points = _decimate(points, FLIGHT_MAX_POINTS)
        flight["path"] = [[p[1], p[2]] for p in points]
        flight["times"] = [p[0] for p in points]
        pilots = [(p[6], p[7]) for p in points if p[6] is not None]
        homes = [(p[8], p[9]) for p in points if p[8] is not None]
        flight["pilot"] = {"lat": pilots[-1][0], "lon": pilots[-1][1]} if pilots else None
        flight["home"] = {"lat": homes[-1][0], "lon": homes[-1][1]} if homes else None
        return flight

    def export_points(self, start: float, end: float, flight_id: int | None = None) -> tuple[list[ExportRow], bool]:
        """Rows for CSV/KML export, oldest first, with flight metadata joined in."""
        with self._lock:
            self._flush_locked()
            conn = self._reader()
            if conn is None:
                return [], False
            sql = (
                "SELECT p.t, f.id, f.type, f.track_id, f.label, f.make_model, f.ua_type, f.transport, f.operator_id,"
                " p.lat, p.lon, p.alt, p.speed, p.heading, p.pilot_lat, p.pilot_lon, p.home_lat, p.home_lon, p.rssi"
                " FROM points p JOIN flights f ON f.id = p.flight_id WHERE p.t BETWEEN ? AND ?"
            )
            params: list[Any] = [start, end]
            if flight_id is not None:
                sql += " AND f.id = ?"
                params.append(flight_id)
            sql += " ORDER BY f.id, p.t LIMIT ?"
            params.append(EXPORT_MAX_POINTS + 1)
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                self._fail(f"history unavailable: {exc}")
                return [], False
        truncated = len(rows) > EXPORT_MAX_POINTS
        del rows[EXPORT_MAX_POINTS:]
        return [ExportRow(*r) for r in rows], truncated

    @staticmethod
    def _flight_row(r: Any) -> dict[str, Any]:
        return {
            "id": r[0], "type": r[1], "track_id": r[2], "label": r[3], "make_model": r[4], "ua_type": r[5],
            "transport": r[6], "operator_id": r[7], "first_seen": r[8], "last_seen": r[9], "max_alt": r[10],
            "min_kit_dist": r[11], "pilot_seen": bool(r[12]), "points": r[13],
        }


def iter_export_flights(rows: list[ExportRow]) -> Iterator[tuple[ExportRow, list[ExportRow]]]:
    current: list[ExportRow] = []
    for row in rows:
        if current and row.flight_id != current[0].flight_id:
            yield current[0], current
            current = []
        current.append(row)
    if current:
        yield current[0], current
