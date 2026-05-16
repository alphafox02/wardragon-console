from __future__ import annotations

import argparse
import logging
from dataclasses import replace

from .collectors import CollectorGroup
from .server import ConsoleServer
from .settings import Settings
from .state import SnapshotStore
from .tether import TetherAccessManager


def main() -> None:
    parser = argparse.ArgumentParser(description="WarDragon local read-only web console")
    parser.add_argument("--host", default=None, help="Bind host. Default: WARDRAGON_CONSOLE_HOST or 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="Bind port. Default: WARDRAGON_CONSOLE_PORT or 4280")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings.from_env()
    overrides: dict[str, object] = {}
    if args.host is not None:
        overrides["bind_host"] = args.host
    if args.port is not None:
        overrides["bind_port"] = args.port
    if overrides:
        settings = replace(settings, **overrides)

    store = SnapshotStore()
    collectors = CollectorGroup(settings, store)
    collectors.start()
    tether = TetherAccessManager(settings, store)
    tether.start()

    server = ConsoleServer(settings, store, tether.status)
    logging.getLogger(__name__).info("WarDragon Console listening on http://%s:%s/", settings.bind_host, settings.bind_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tether.stop()
        collectors.stop()
        server.server_close()


if __name__ == "__main__":
    main()
