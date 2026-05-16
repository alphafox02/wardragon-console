from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import Settings
from .state import SnapshotStore

LOG = logging.getLogger(__name__)


class CollectorGroup:
    def __init__(self, settings: Settings, store: SnapshotStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._start("zmq-monitor", self._zmq_json_subscriber, self.settings.monitor_endpoint, "monitor", None)
        self._start(
            "zmq-droneid-health",
            self._zmq_json_subscriber,
            self.settings.droneid_health_endpoint,
            "droneid",
            "Health",
        )
        self._start(
            "zmq-dragonsig-health",
            self._zmq_json_subscriber,
            self.settings.dragonsig_health_endpoint,
            "dragonsig",
            "Health",
        )
        self._start("dragonsync-poller", self._dragonsync_poller)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)

    def _start(self, name: str, target: Any, *args: Any) -> None:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _zmq_json_subscriber(self, endpoint: str, store_key: str, top_key: str | None) -> None:
        try:
            import zmq
        except Exception as exc:
            self.store.set_error(store_key, f"pyzmq unavailable: {exc}")
            LOG.warning("pyzmq unavailable for %s: %s", store_key, exc)
            return

        ctx = zmq.Context.instance()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.CONFLATE, 1)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(endpoint)
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        LOG.info("subscribed to %s for %s", endpoint, store_key)

        while not self._stop.is_set():
            try:
                events = dict(poller.poll(timeout=1000))
                if sub not in events:
                    continue
                payload = json.loads(sub.recv().decode("utf-8", errors="replace"))
                if top_key is not None:
                    if not isinstance(payload, dict) or top_key not in payload:
                        continue
                    payload = payload[top_key]
                self.store.update(store_key, payload)
            except Exception as exc:
                self.store.set_error(store_key, str(exc))
                LOG.debug("subscriber error for %s: %s", store_key, exc)
                time.sleep(1.0)

        sub.close(linger=0)

    def _dragonsync_poller(self) -> None:
        endpoints = {
            "dragonsync_status": "/status",
            "drones": "/drones",
            "signals": "/signals",
        }
        while not self._stop.is_set():
            for store_key, path in endpoints.items():
                payload, error = self._fetch_json(f"{self.settings.dragonsync_url}{path}")
                if error:
                    self.store.set_error(store_key, error)
                else:
                    self.store.update(store_key, payload)
            self._stop.wait(max(self.settings.dragonsync_poll_seconds, 5.0))

    def _fetch_json(self, url: str) -> tuple[dict[str, Any], str | None]:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "wardragon-console/0.1"})
        try:
            with urlopen(request, timeout=2.0) as response:
                raw = response.read(1024 * 1024)
        except HTTPError as exc:
            return {}, f"HTTP {exc.code}"
        except URLError as exc:
            return {}, str(exc.reason)
        except TimeoutError:
            return {}, "timeout"
        except Exception as exc:
            return {}, str(exc)

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return {}, f"invalid JSON: {exc}"
        if not isinstance(decoded, dict):
            return {}, "JSON response was not an object"
        return decoded, None
