from __future__ import annotations

import json
import logging
import math
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .actions import restart_dragonsync
from .config_view import read_config_files, write_config_file, write_curated_config
from .certs import upload_tak_cert
from .dragonscope import check_license, read_dragonscope, write_dragonscope
from .kit_id import read_override as read_kit_id_override, write_override as write_kit_id_override
from .settings import Settings
from .state import SnapshotStore, SourceTiming
from .export import to_csv, to_kml
from .history import HistoryStore
from .offline_maps import OfflineMaps
from .tracks import TrackBuffer
from .updates import check_for_updates
from .upgrade import TARGETS as UPGRADE_TARGETS, helper_installed, start_upgrade, upgrade_status

LOG = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")


HISTORY_MAX_WINDOW_SECONDS = 31 * 86400


def _time_window(query: dict[str, list[str]]) -> tuple[float, float]:
    now = time.time()
    try:
        end = float((query.get("to") or [now])[0])
        start = float((query.get("from") or [end - 3600])[0])
    except (TypeError, ValueError):
        raise ValueError("'from' and 'to' must be unix timestamps") from None
    if not (math.isfinite(start) and math.isfinite(end)) or start > end:
        raise ValueError("invalid time window")
    if end - start > HISTORY_MAX_WINDOW_SECONDS:
        raise ValueError("time window is limited to 31 days")
    return start, end


def _optional_int(query: dict[str, list[str]], key: str) -> int | None:
    values = query.get(key)
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        raise ValueError(f"'{key}' must be an integer") from None


class ConsoleServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        settings: Settings,
        store: SnapshotStore,
        tether_status_provider: Any | None = None,
        tracks: TrackBuffer | None = None,
        history: HistoryStore | None = None,
        offline_maps: OfflineMaps | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tether_status_provider = tether_status_provider
        self.tracks = tracks
        self.history = history
        self.offline_maps = offline_maps
        super().__init__((settings.bind_host, settings.bind_port), ConsoleRequestHandler)


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    server: ConsoleServer
    timeout = 10

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return self.client_address[0] in ("127.0.0.1", "::1")
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        return bool(parsed.netloc) and parsed.netloc == self.headers.get("Host", "")

    def _read_content_length(self, max_bytes: int) -> int | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(HTTPStatus.LENGTH_REQUIRED)
            return None
        if length < 0:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return None
        if length > max_bytes:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        return length

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._serve_static("index.html")
            return
        if path == "/api/snapshot":
            self._write_json(self._snapshot())
            return
        if path == "/api/config":
            include_raw = self.server.settings.is_local_bind()
            self._write_json(read_config_files(self.server.settings, include_raw=include_raw))
            return
        if path == "/api/dragonscope/config":
            self._write_json(read_dragonscope(self.server.settings))
            return
        if path == "/api/kit-id-override":
            self._write_json(read_kit_id_override(self.server.settings))
            return
        if path == "/api/health":
            self._write_json({"ok": True, "bind": f"{self.server.settings.bind_host}:{self.server.settings.bind_port}"})
            return
        if path.startswith("/api/upgrade/"):
            target = path.removeprefix("/api/upgrade/")
            if target not in UPGRADE_TARGETS:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._write_json(upgrade_status(target))
            return
        if path == "/api/map":
            if not self.server.settings.map_enabled or self.server.tracks is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._write_json(self._map_payload())
            return
        if path.startswith("/api/map/history"):
            self._handle_history_get(path, parse_qs(parsed.query))
            return
        if path == "/api/map/offline":
            maps = self._offline_or_404()
            if maps is None:
                return
            self._write_json({
                "maps": maps.list(),
                "can_change": self.server.settings.can_write_config(),
                "max_upload_bytes": maps.max_upload_bytes,
                "free_bytes": maps.free_bytes(),
                "dir": str(maps.dir),
            })
            return
        if path.startswith("/api/map/offline/tile/"):
            self._handle_offline_tile(path.removeprefix("/api/map/offline/tile/"))
            return
        if path.startswith("/static/"):
            self._serve_static(path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/config/"):
            self._handle_config_put(parsed.path.removeprefix("/api/config/"))
            return
        if parsed.path == "/api/dragonscope/config":
            self._handle_dragonscope_put()
            return
        if parsed.path == "/api/kit-id-override":
            self._handle_kit_id_override_put()
            return
        if parsed.path == "/api/map/history/settings":
            self._handle_history_settings_put()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_config_put(self, name: str) -> None:
        if not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        length = self._read_content_length(256 * 1024)
        if length is None:
            return
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            if "application/json" in self.headers.get("Content-Type", ""):
                payload = json.loads(raw_body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
                result = write_curated_config(self.server.settings, name, payload)
            else:
                result = write_config_file(self.server.settings, name, raw_body)
        except PermissionError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            LOG.exception("config write failed")
            self._write_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._write_json(result)

    def _handle_dragonscope_put(self) -> None:
        if not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        length = self._read_content_length(64 * 1024)
        if length is None:
            return
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            result = write_dragonscope(self.server.settings, payload)
        except PermissionError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            LOG.exception("dragonscope write failed")
            self._write_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._write_json(result)

    def _handle_kit_id_override_put(self) -> None:
        if not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        length = self._read_content_length(4 * 1024)
        if length is None:
            return
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            value = payload.get("value", "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("'value' must be a string")
            result = write_kit_id_override(self.server.settings, value)
        except PermissionError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            LOG.exception("kit-id-override write failed")
            self._write_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._write_json(result)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {
            "/api/actions/restart-dragonsync",
            "/api/certs/tak",
            "/api/updates/check",
            "/api/dragonscope/license/check",
            "/api/map/history/clear",
            "/api/map/offline/upload",
            "/api/map/offline/delete",
        } | {f"/api/upgrade/{t}" for t in UPGRADE_TARGETS} and not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        if path == "/api/actions/restart-dragonsync":
            try:
                result = restart_dragonsync(self.server.settings)
            except PermissionError as exc:
                self._write_json({"ok": False, "error": str(exc)}, status=403)
                return
            except Exception as exc:
                LOG.exception("restart failed")
                self._write_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._write_json(result, status=200 if result.get("ok") else 500)
            return

        if path == "/api/certs/tak":
            self._handle_cert_upload()
            return

        if path == "/api/updates/check":
            try:
                result = check_for_updates(self.server.settings, self.server.store)
            except PermissionError as exc:
                self._write_json({"ok": False, "error": str(exc)}, status=403)
                return
            except Exception as exc:
                LOG.exception("update check failed")
                self._write_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._write_json({"ok": True, **result})
            return

        if path == "/api/dragonscope/license/check":
            try:
                result = check_license(self.server.settings)
            except PermissionError as exc:
                self._write_json({"ok": False, "error": str(exc)}, status=403)
                return
            except Exception as exc:
                LOG.exception("license check failed")
                self._write_json({"ok": False, "error": str(exc)}, status=500)
                return
            self.server.store.update("dragonscope_license", result)
            self._write_json(result)
            return

        if path in ("/api/map/offline/upload", "/api/map/offline/delete"):
            self._handle_offline_post(path)
            return

        if path == "/api/map/history/clear":
            history = self._history_or_404()
            if history is None:
                return
            if not self.server.settings.can_write_config():
                self._write_json({"ok": False, "error": "changing history is disabled for this bind mode"}, status=403)
                return
            self._write_json({"ok": True, **history.clear()})
            return

        if path.startswith("/api/upgrade/"):
            target = path.removeprefix("/api/upgrade/")
            if target not in UPGRADE_TARGETS:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                result = start_upgrade(self.server.settings, target)
            except PermissionError as exc:
                self._write_json({"ok": False, "error": str(exc)}, status=403)
                return
            except Exception as exc:
                LOG.exception("upgrade start failed")
                self._write_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._write_json(result, status=200 if result.get("ok") else 409)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _offline_or_404(self) -> OfflineMaps | None:
        if not self.server.settings.map_enabled or self.server.offline_maps is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return self.server.offline_maps

    def _handle_offline_tile(self, rest: str) -> None:
        maps = self._offline_or_404()
        if maps is None:
            return
        parts = rest.split("/")
        try:
            name, z, x, y = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        except (IndexError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if len(parts) != 4:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        tile = maps.tile(name, z, x, y)
        if tile is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body, ctype = tile
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _handle_offline_post(self, path: str) -> None:
        maps = self._offline_or_404()
        if maps is None:
            return
        if not self.server.settings.can_write_config():
            self.close_connection = True
            self._write_json({"ok": False, "error": "changing offline maps is disabled for this bind mode"}, status=403)
            return
        if path == "/api/map/offline/delete":
            length = self._read_content_length(1024)
            if length is None:
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                maps.delete(payload.get("name", "") if isinstance(payload, dict) else "")
            except FileNotFoundError:
                self._write_json({"ok": False, "error": "no such map"}, status=404)
                return
            except ValueError as exc:
                self._write_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._write_json({"ok": True})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(HTTPStatus.LENGTH_REQUIRED)
            return
        try:
            info = maps.save_upload(self.rfile, length, self.headers.get("X-Filename", ""))
        except ValueError as exc:
            # The body may be partly unread; do not reuse this connection.
            self.close_connection = True
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except OSError as exc:
            self.close_connection = True
            LOG.warning("offline map upload failed: %s", exc)
            self._write_json({"ok": False, "error": f"could not save the map: {exc.strerror or exc}"}, status=500)
            return
        self._write_json({"ok": True, "map": info})

    def _history_or_404(self) -> HistoryStore | None:
        if not self.server.settings.map_enabled or self.server.history is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return self.server.history

    def _history_status(self, history: HistoryStore) -> dict[str, Any]:
        return {**history.status(), "can_change": self.server.settings.can_write_config()}

    def _handle_history_get(self, path: str, query: dict[str, list[str]]) -> None:
        history = self._history_or_404()
        if history is None:
            return
        if path == "/api/map/history/status":
            self._write_json(self._history_status(history))
            return
        try:
            start, end = _time_window(query)
            flight_id = _optional_int(query, "flight")
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        if path == "/api/map/history":
            self._write_json({"ok": True, "from": start, "to": end, **history.query(start, end)})
            return
        if path == "/api/map/history/flight":
            if flight_id is None:
                self._write_json({"ok": False, "error": "'flight' is required"}, status=400)
                return
            flight = history.flight(flight_id)
            if flight is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._write_json({"ok": True, "flight": flight})
            return
        if path == "/api/map/history/export":
            fmt = (query.get("format") or ["csv"])[0]
            if fmt not in ("csv", "kml"):
                self._write_json({"ok": False, "error": "format must be csv or kml"}, status=400)
                return
            rows, truncated = history.export_points(start, end, flight_id)
            stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(end))
            name = f"wardragon-history-{stamp}" + (f"-flight{flight_id}" if flight_id is not None else "")
            if truncated:
                name += "-truncated"
            if fmt == "csv":
                body, ctype = to_csv(rows), "text/csv; charset=utf-8"
            else:
                body, ctype = to_kml(rows, f"WarDragon history {stamp} UTC"), "application/vnd.google-earth.kml+xml"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{name}.{fmt}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Export-Truncated", "1" if truncated else "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_history_settings_put(self) -> None:
        if not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        history = self._history_or_404()
        if history is None:
            return
        if not self.server.settings.can_write_config():
            self._write_json({"ok": False, "error": "changing history is disabled for this bind mode"}, status=403)
            return
        length = self._read_content_length(4 * 1024)
        if length is None:
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            history.update_settings(payload)
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except OSError as exc:
            LOG.warning("history settings write failed: %s", exc)
            self._write_json({
                "ok": False,
                "error": f"cannot write to {history.data_dir}; re-run 'sudo packaging/install.sh' to create it",
            }, status=500)
            return
        self._write_json({"ok": True, **self._history_status(history)})

    def _map_payload(self) -> dict[str, Any]:
        settings = self.server.settings
        payload = self.server.tracks.map_payload()
        payload["tiles"] = {
            "url": settings.map_tile_url if settings.map_tile_origin() else "",
            "attribution": settings.map_tile_attribution,
        }
        return payload

    def _snapshot(self) -> dict[str, Any]:
        settings = self.server.settings
        snapshot = self.server.store.snapshot(
            SourceTiming(
                stale_after=settings.health_stale_seconds,
                grace_seconds=settings.service_grace_seconds,
            )
        )
        tether_status = {}
        if self.server.tether_status_provider:
            tether_status = self.server.tether_status_provider()
        snapshot["access"] = {
            "local_url": f"http://127.0.0.1:{settings.bind_port}/",
            "current_bind": f"{settings.bind_host}:{settings.bind_port}",
            "config_write_allowed": settings.can_write_config(),
            "restart_allowed": settings.can_restart_dragonsync(),
            "upgrade_allowed": settings.can_upgrade(),
            "upgrade_helper_installed": helper_installed(),
            "map_enabled": settings.map_enabled and self.server.tracks is not None,
            "tether": tether_status,
        }
        return snapshot

    def _write_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_cert_upload(self) -> None:
        length = self._read_content_length(self.server.settings.cert_upload_max_bytes)
        if length is None:
            return
        role = self.headers.get("X-Cert-Role", "")
        filename = self.headers.get("X-Filename", "")
        p12_password = self.headers.get("X-P12-Password", "")
        data = self.rfile.read(length)
        try:
            result = upload_tak_cert(self.server.settings, role, filename, data, p12_password)
        except PermissionError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self._write_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            LOG.exception("certificate upload failed")
            self._write_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._write_json(result)

    def _serve_static(self, relative: str) -> None:
        try:
            requested = (STATIC_DIR / relative).resolve()
            requested.relative_to(STATIC_DIR.resolve())
        except Exception:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not requested.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        body = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if requested.name == "index.html":
            cache = "no-store"
        elif relative.startswith("vendor/"):
            cache = "public, max-age=86400"
        else:
            # Re-fetched on every load so a console upgrade never pairs a new page with an old script.
            cache = "no-cache"
        self.send_header("Cache-Control", cache)
        if requested.name == "index.html":
            tile_origin = self.server.settings.map_tile_origin() if self.server.settings.map_enabled else ""
            img_src = f"'self' data: {tile_origin}".strip()
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'self'; img-src {img_src}; style-src 'self'; script-src 'self'",
            )
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)
