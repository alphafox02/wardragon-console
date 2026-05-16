from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DRAGONSYNC_DIR = Path("/home/dragon/WarDragon/DragonSync")


@dataclass(frozen=True)
class Settings:
    bind_host: str = "127.0.0.1"
    bind_port: int = 4280
    dragonsync_url: str = "http://127.0.0.1:8088"
    dragonsync_dir: Path = DEFAULT_DRAGONSYNC_DIR
    monitor_endpoint: str = "tcp://127.0.0.1:4225"
    droneid_health_endpoint: str = "tcp://127.0.0.1:4227"
    dragonsig_health_endpoint: str = "tcp://127.0.0.1:4228"
    dragonsync_poll_seconds: float = 5.0
    service_grace_seconds: float = 90.0
    health_stale_seconds: float = 60.0
    config_write_enabled: bool = True
    allow_remote_config_write: bool = False
    tether_enabled: bool = False
    tether_port: int = 4280
    tether_poll_seconds: float = 3.0
    tether_cidrs: tuple[str, ...] = ("192.168.42.0/24", "192.168.43.0/24", "172.20.10.0/28")
    restart_enabled: bool = True
    allow_remote_restart: bool = False
    dragonsync_service_name: str = "dragonsync.service"
    cert_upload_enabled: bool = True
    cert_upload_max_bytes: int = 256 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        dragonsync_dir = Path(os.environ.get("WARDRAGON_DRAGONSYNC_DIR", str(DEFAULT_DRAGONSYNC_DIR)))
        return cls(
            bind_host=os.environ.get("WARDRAGON_CONSOLE_HOST", "127.0.0.1"),
            bind_port=_env_int("WARDRAGON_CONSOLE_PORT", 4280),
            dragonsync_url=os.environ.get("WARDRAGON_DRAGONSYNC_URL", "http://127.0.0.1:8088").rstrip("/"),
            dragonsync_dir=dragonsync_dir,
            monitor_endpoint=os.environ.get("WARDRAGON_MONITOR_ZMQ", "tcp://127.0.0.1:4225"),
            droneid_health_endpoint=os.environ.get("WARDRAGON_DRONEID_HEALTH_ZMQ", "tcp://127.0.0.1:4227"),
            dragonsig_health_endpoint=os.environ.get("WARDRAGON_DRAGONSIG_HEALTH_ZMQ", "tcp://127.0.0.1:4228"),
            dragonsync_poll_seconds=_env_float("WARDRAGON_DRAGONSYNC_POLL_SECONDS", 5.0),
            service_grace_seconds=_env_float("WARDRAGON_SERVICE_GRACE_SECONDS", 90.0),
            health_stale_seconds=_env_float("WARDRAGON_HEALTH_STALE_SECONDS", 60.0),
            config_write_enabled=_env_bool("WARDRAGON_CONSOLE_CONFIG_WRITE", True),
            allow_remote_config_write=_env_bool("WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE", False),
            tether_enabled=_env_bool("WARDRAGON_CONSOLE_TETHER_ENABLED", False),
            tether_port=_env_int("WARDRAGON_CONSOLE_TETHER_PORT", 4280),
            tether_poll_seconds=_env_float("WARDRAGON_CONSOLE_TETHER_POLL_SECONDS", 3.0),
            tether_cidrs=_env_tuple("WARDRAGON_CONSOLE_TETHER_CIDRS", ("192.168.42.0/24", "192.168.43.0/24", "172.20.10.0/28")),
            restart_enabled=_env_bool("WARDRAGON_CONSOLE_RESTART_ENABLED", True),
            allow_remote_restart=_env_bool("WARDRAGON_CONSOLE_REMOTE_RESTART", False),
            dragonsync_service_name=os.environ.get("WARDRAGON_DRAGONSYNC_SERVICE", "dragonsync.service"),
            cert_upload_enabled=_env_bool("WARDRAGON_CONSOLE_CERT_UPLOAD", True),
            cert_upload_max_bytes=_env_int("WARDRAGON_CONSOLE_CERT_MAX_BYTES", 256 * 1024),
        )

    def can_write_config(self) -> bool:
        if not self.config_write_enabled:
            return False
        if self.is_local_bind():
            return True
        return self.allow_remote_config_write

    def can_restart_dragonsync(self) -> bool:
        if not self.restart_enabled:
            return False
        if self.is_local_bind():
            return True
        return self.allow_remote_restart

    def is_local_bind(self) -> bool:
        return self.bind_host in {"127.0.0.1", "localhost", "::1"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    parts = tuple(part.strip() for part in value.split(",") if part.strip())
    return parts or default
