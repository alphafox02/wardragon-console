from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import replace
from ipaddress import ip_address, ip_network

from .network import tether_candidates
from .server import ConsoleServer
from .settings import Settings
from .state import SnapshotStore

LOG = logging.getLogger(__name__)
ALIAS_HELPER = "/usr/local/bin/wardragon-tether-alias"


class TetherAccessManager:
    def __init__(self, settings: Settings, store: SnapshotStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._server: ConsoleServer | None = None
        self._alias_server: ConsoleServer | None = None
        self._alias_thread: threading.Thread | None = None
        self._alias_ip: str | None = None
        self._alias_prefix: int | None = None
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
            port = self.settings.tether_port
            return {
                "enabled": self.settings.tether_enabled,
                "active": self._server is not None,
                "url": f"http://{self._bound_ip}:{port}/" if self._bound_ip else "",
                "stable_url": f"http://{self._alias_ip}:{port}/" if self._alias_ip else "",
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
        primary_settings = replace(self.settings, bind_host=ip, bind_port=self.settings.tether_port)
        try:
            server = ConsoleServer(primary_settings, self.store, self.status)
        except OSError as exc:
            LOG.warning("could not bind tether console on %s:%s: %s", ip, self.settings.tether_port, exc)
            return

        thread = threading.Thread(target=server.serve_forever, name=f"tether-http-{ip}", daemon=True)
        thread.start()

        # Optional: claim a stable alias IP for shipped-tablet use cases.
        alias_match = self._matching_claim_profile(ip)
        alias_server: ConsoleServer | None = None
        alias_thread: threading.Thread | None = None
        alias_ip: str | None = None
        alias_prefix: int | None = None
        if alias_match is not None:
            candidate_ip, candidate_prefix = alias_match
            if candidate_ip == ip:
                LOG.info("alias %s already matches tether IP; skipping claim", candidate_ip)
            elif self._claim_alias(interface["name"], candidate_ip, candidate_prefix):
                try:
                    alias_server = ConsoleServer(
                        replace(primary_settings, bind_host=candidate_ip),
                        self.store,
                        self.status,
                    )
                except OSError as exc:
                    LOG.warning(
                        "alias listener bind on %s failed: %s; releasing alias",
                        candidate_ip,
                        exc,
                    )
                    self._release_alias(interface["name"], candidate_ip, candidate_prefix)
                else:
                    alias_thread = threading.Thread(
                        target=alias_server.serve_forever,
                        name=f"tether-http-alias-{candidate_ip}",
                        daemon=True,
                    )
                    alias_thread.start()
                    alias_ip = candidate_ip
                    alias_prefix = candidate_prefix
                    LOG.info(
                        "claimed stable tether alias http://%s:%s/ on %s",
                        candidate_ip,
                        self.settings.tether_port,
                        interface.get("name"),
                    )

        with self._lock:
            self._server = server
            self._server_thread = thread
            self._alias_server = alias_server
            self._alias_thread = alias_thread
            self._alias_ip = alias_ip
            self._alias_prefix = alias_prefix
            self._bound_ip = ip
            self._active_interface = interface
        LOG.info("tether console available at http://%s:%s/ via %s", ip, self.settings.tether_port, interface.get("name"))

    def _stop_server(self) -> None:
        with self._lock:
            server = self._server
            thread = self._server_thread
            alias_server = self._alias_server
            alias_thread = self._alias_thread
            alias_ip = self._alias_ip
            alias_prefix = self._alias_prefix
            interface = self._active_interface
            ip = self._bound_ip
            self._server = None
            self._server_thread = None
            self._alias_server = None
            self._alias_thread = None
            self._alias_ip = None
            self._alias_prefix = None
            self._bound_ip = None
            self._active_interface = None
        if server is None and alias_server is None:
            return
        if server is not None:
            LOG.info("stopping tether console on %s", ip)
            server.shutdown()
            server.server_close()
            if thread:
                self._join(thread)
        if alias_server is not None:
            alias_server.shutdown()
            alias_server.server_close()
            if alias_thread:
                self._join(alias_thread)
        if alias_ip and alias_prefix is not None and interface is not None:
            self._release_alias(str(interface.get("name", "")), alias_ip, alias_prefix)

    @staticmethod
    def _join(thread: threading.Thread) -> None:
        deadline = time.time() + 2.0
        while thread.is_alive() and time.time() < deadline:
            thread.join(timeout=0.2)

    def _matching_claim_profile(self, tether_ip: str) -> tuple[str, int] | None:
        try:
            address = ip_address(tether_ip)
        except ValueError:
            return None
        for network_str, alias_ip in self.settings.tether_claim_profiles:
            try:
                network = ip_network(network_str, strict=False)
                _ = ip_address(alias_ip)
            except ValueError:
                LOG.debug("ignoring invalid claim profile %s=%s", network_str, alias_ip)
                continue
            if address in network:
                return alias_ip, network.prefixlen
        return None

    @staticmethod
    def _run_helper(action: str, iface: str, cidr: str) -> bool:
        cmd = ["sudo", "-n", ALIAS_HELPER, action, iface, cidr]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOG.warning("alias helper %s subprocess error: %s", action, exc)
            return False
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or (result.stdout or "").strip()
            LOG.warning(
                "alias helper %s %s %s failed: %s",
                action,
                iface,
                cidr,
                stderr or f"rc={result.returncode}",
            )
            return False
        return True

    def _claim_alias(self, iface: str, alias_ip: str, prefix: int) -> bool:
        # If the alias is already on the interface (e.g. left over from a
        # previous run that crashed), treat that as success and just bind the
        # listener. Otherwise call the helper to add it.
        if self._alias_already_present(iface, alias_ip):
            LOG.info("alias %s already present on %s; reusing", alias_ip, iface)
            return True
        return self._run_helper("add", iface, f"{alias_ip}/{prefix}")

    @staticmethod
    def _alias_already_present(iface: str, alias_ip: str) -> bool:
        try:
            result = subprocess.run(
                ["/sbin/ip", "-4", "-o", "addr", "show", "dev", iface],
                capture_output=True, text=True, timeout=3, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        # Output lines look like: "5: enxXXXX    inet 10.152.47.250/24 ..."
        for line in result.stdout.splitlines():
            for token in line.split():
                if token.split("/", 1)[0] == alias_ip:
                    return True
        return False

    def _release_alias(self, iface: str, alias_ip: str, prefix: int) -> None:
        self._run_helper("del", iface, f"{alias_ip}/{prefix}")
