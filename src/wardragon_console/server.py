from __future__ import annotations

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .actions import restart_dragonsync
from .config_view import read_config_files, write_config_file, write_curated_config
from .certs import upload_tak_cert
from .settings import Settings
from .state import SnapshotStore, SourceTiming
from .updates import check_for_updates

LOG = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")


class ConsoleServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, settings: Settings, store: SnapshotStore, tether_status_provider: Any | None = None) -> None:
        self.settings = settings
        self.store = store
        self.tether_status_provider = tether_status_provider
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
        if path == "/api/health":
            self._write_json({"ok": True, "bind": f"{self.server.settings.bind_host}:{self.server.settings.bind_port}"})
            return
        if path.startswith("/static/"):
            self._serve_static(path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/config/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._check_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return
        name = parsed.path.removeprefix("/api/config/")
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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/api/actions/restart-dragonsync", "/api/certs/tak", "/api/updates/check"} and not self._check_origin():
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

        self.send_error(HTTPStatus.NOT_FOUND)

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
        self.send_header("Cache-Control", "no-store" if requested.name == "index.html" else "public, max-age=60")
        if requested.name == "index.html":
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)
