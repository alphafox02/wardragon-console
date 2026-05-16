from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from .network import tether_candidates
from .server import ConsoleServer
from .settings import Settings
from .state import SnapshotStore

LOG = logging.getLogger(__name__)


class TetherAccessManager:
    def __init__(self, settings: Settings, store: SnapshotStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._server: ConsoleServer | None = None
        self._bound_ip: str | None = None
        self._active_interface: dict[str, str] | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        if not self.settings.tether_enabled:
            LOG.info("tether access disabled")
            return
        self._thread = threading.Thread(target=self._watch_loop, name="tether-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_server()
        if self._thread:
            self._thread.join(timeout=2.0)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.settings.tether_enabled,
                "active": self._server is not None,
                "url": f"http://{self._bound_ip}:{self.settings.tether_port}/" if self._bound_ip else "",
                "interface": self._active_interface or None,
            }

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            candidates = tether_candidates(self.settings.tether_cidrs)
            chosen = candidates[0] if candidates else None
            if chosen:
                ip = str(chosen["ipv4"])
                if ip != self._bound_ip:
                    self._stop_server()
                    self._start_server(chosen)
            elif self._server is not None:
                self._stop_server()
            self._stop.wait(max(self.settings.tether_poll_seconds, 1.0))

    def _start_server(self, interface: dict[str, str]) -> None:
        ip = str(interface["ipv4"])
        settings = replace(self.settings, bind_host=ip, bind_port=self.settings.tether_port)
        try:
            server = ConsoleServer(settings, self.store, self.status)
        except OSError as exc:
            LOG.warning("could not bind tether console on %s:%s: %s", ip, self.settings.tether_port, exc)
            return

        thread = threading.Thread(target=server.serve_forever, name=f"tether-http-{ip}", daemon=True)
        thread.start()
        with self._lock:
            self._server = server
            self._server_thread = thread
            self._bound_ip = ip
            self._active_interface = interface
        LOG.info("tether console available at http://%s:%s/ via %s", ip, self.settings.tether_port, interface.get("name"))

    def _stop_server(self) -> None:
        with self._lock:
            server = self._server
            thread = self._server_thread
            ip = self._bound_ip
            self._server = None
            self._server_thread = None
            self._bound_ip = None
            self._active_interface = None
        if server is None:
            return
        LOG.info("stopping tether console on %s", ip)
        server.shutdown()
        server.server_close()
        if thread:
            deadline = time.time() + 2.0
            while thread.is_alive() and time.time() < deadline:
                thread.join(timeout=0.2)
