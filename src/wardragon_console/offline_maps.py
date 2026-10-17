"""Offline raster map files (.mbtiles) for the Map tab."""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

LOG = logging.getLogger(__name__)

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.mbtiles")
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_FILES = 5
MIN_FREE_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
TILE_MAX_BYTES = 1024 * 1024
QUERY_SECONDS = 2.0
PARTIAL_PREFIX = ".upload-"


def _image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class OfflineMaps:
    def __init__(self, data_dir: Path, max_upload_bytes: int) -> None:
        self.dir = data_dir / "maps"
        self.max_upload_bytes = max_upload_bytes
        self._lock = threading.Lock()
        self._upload_lock = threading.Lock()
        self._info: dict[str, tuple[tuple[float, int], dict[str, Any]]] = {}
        self._conns: dict[str, tuple[sqlite3.Connection, threading.Lock]] = {}
        self._remove_partials()


    def _open(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1, check_same_thread=False)
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    @staticmethod
    def _bounded(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[Any]:
        """Run a query that is aborted if it takes too long (guards against hostile views)."""
        deadline = time.monotonic() + QUERY_SECONDS
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.set_progress_handler(None, 0)

    def _validate(self, path: Path) -> dict[str, Any]:
        info: dict[str, Any] = {"name": path.name, "size": path.stat().st_size, "ok": False}
        try:
            with path.open("rb") as handle:
                if handle.read(16) != SQLITE_MAGIC:
                    raise ValueError("not an MBTiles (SQLite) file")
            conn = self._open(path)
            try:
                meta = {
                    str(k): str(v) for k, v in self._bounded(conn, "SELECT name, value FROM metadata LIMIT 200")
                    if isinstance(k, str) and isinstance(v, (str, int, float))
                }
                sample = self._bounded(conn, "SELECT tile_data FROM tiles LIMIT 1")
                if not sample or not isinstance(sample[0][0], bytes):
                    raise ValueError("file contains no map tiles")
                mime = _image_type(sample[0][0])
                if mime is None:
                    raise ValueError("tiles are not PNG, JPEG or WebP images (vector maps are not supported)")
                zooms = self._bounded(conn, "SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles")
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            info["error"] = f"cannot read map file: {exc}"
            return info
        except ValueError as exc:
            info["error"] = str(exc)
            return info

        def zoom(key: str, fallback: Any) -> int | None:
            try:
                value = int(meta.get(key, fallback))
            except (TypeError, ValueError):
                return None
            return value if 0 <= value <= 22 else None

        minzoom = zoom("minzoom", zooms[0][0] if zooms else None)
        maxzoom = zoom("maxzoom", zooms[0][1] if zooms else None)
        if minzoom is None or maxzoom is None or minzoom > maxzoom:
            info["error"] = "map file has no usable zoom levels"
            return info
        bounds = None
        try:
            west, south, east, north = (float(v) for v in meta.get("bounds", "").split(","))
            if -180 <= west < east <= 180 and -90 <= south < north <= 90:
                bounds = [[south, west], [north, east]]
        except ValueError:
            pass
        info.update({
            "ok": True,
            "label": (meta.get("name") or path.stem)[:80],
            "format": mime.split("/")[1],
            "minzoom": minzoom,
            "maxzoom": maxzoom,
            "bounds": bounds,
            "attribution": meta.get("attribution", "")[:200],
        })
        return info

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        maps = []
        with self._lock:
            names = sorted(p for p in os.listdir(self.dir) if NAME_RE.fullmatch(p))
            for name in names[: MAX_FILES * 2]:
                path = self.dir / name
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                key = (stat.st_mtime, stat.st_size)
                cached = self._info.get(name)
                if cached is None or cached[0] != key:
                    self._close(name)
                    cached = (key, self._validate(path))
                    self._info[name] = cached
                maps.append(dict(cached[1]))
            for name in list(self._info):
                if name not in names:
                    self._info.pop(name, None)
                    self._close(name)
        return maps

    def tile(self, name: str, z: int, x: int, y: int) -> tuple[bytes, str] | None:
        if not NAME_RE.fullmatch(name) or not (0 <= z <= 22) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
            return None
        with self._lock:
            cached = self._info.get(name)
            if cached is None or not cached[1].get("ok"):
                return None
            entry = self._conns.get(name)
            if entry is None:
                try:
                    entry = (self._open(self.dir / name), threading.Lock())
                except sqlite3.Error:
                    return None
                self._conns[name] = entry
        conn, conn_lock = entry
        with conn_lock:
            try:
                rows = self._bounded(
                    conn,
                    "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
                    (z, x, (2 ** z) - 1 - y),
                )
            except sqlite3.Error:
                return None
        if not rows or not isinstance(rows[0][0], bytes) or len(rows[0][0]) > TILE_MAX_BYTES:
            return None
        mime = _image_type(rows[0][0])
        return (rows[0][0], mime) if mime else None

    def _close(self, name: str) -> None:
        entry = self._conns.pop(name, None)
        if entry:
            try:
                entry[0].close()
            except sqlite3.Error:
                pass

    def free_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.dir if self.dir.is_dir() else self.dir.parent).free
        except OSError:
            return None


    def save_upload(self, stream: BinaryIO, length: int, filename: str) -> dict[str, Any]:
        name = (filename or "").strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError("file name must end in .mbtiles and use only letters, digits, '.', '-' or '_'")
        if length <= 0:
            raise ValueError("uploaded file is empty")
        if length > self.max_upload_bytes:
            raise ValueError(f"file is larger than the {self.max_upload_bytes // (1024 * 1024)} MB upload limit; copy it to {self.dir} instead")
        if not self._upload_lock.acquire(blocking=False):
            raise ValueError("another map upload is in progress")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            existing = [p for p in os.listdir(self.dir) if NAME_RE.fullmatch(p)]
            if name in existing:
                raise ValueError(f"a map named {name} already exists; delete it first")
            if len(existing) >= MAX_FILES:
                raise ValueError(f"at most {MAX_FILES} offline maps can be installed")
            free = self.free_bytes()
            if free is None or free - length < MIN_FREE_BYTES:
                raise ValueError("not enough free disk space on the kit for this map")
            fd, tmp = tempfile.mkstemp(prefix=PARTIAL_PREFIX, suffix=".partial", dir=self.dir)
            tmp_path = Path(tmp)
            try:
                received = 0
                with os.fdopen(fd, "wb") as handle:
                    while received < length:
                        chunk = stream.read(min(CHUNK_BYTES, length - received))
                        if not chunk:
                            raise ValueError("upload was interrupted")
                        handle.write(chunk)
                        received += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp_path, 0o640)
                info = self._validate(tmp_path)
                if not info["ok"]:
                    raise ValueError(info["error"])
                os.replace(tmp_path, self.dir / name)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        finally:
            self._upload_lock.release()
        LOG.info("offline map installed: %s (%d bytes)", name, length)
        return next((m for m in self.list() if m["name"] == name), {"name": name})

    def delete(self, name: str) -> None:
        if not NAME_RE.fullmatch(name or ""):
            raise ValueError("invalid map name")
        path = self.dir / name
        with self._lock:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(name)
            self._close(name)
            self._info.pop(name, None)
            path.unlink()

    def _remove_partials(self) -> None:
        try:
            for entry in os.listdir(self.dir):
                if entry.startswith(PARTIAL_PREFIX) and entry.endswith(".partial"):
                    (self.dir / entry).unlink(missing_ok=True)
        except OSError:
            pass
