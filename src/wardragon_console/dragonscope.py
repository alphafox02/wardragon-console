"""Curated read/write view of dragonscope.cfg.

DragonScope is a separate program (antsdr/dragonsdr DJI DroneID proxy) from
DragonSync, with its own JSON config file at <dragonscope_dir>/dragonscope.cfg.
The file holds a remote API URL and a license key; DragonScope re-reads it
every 30 s, so saving here takes effect without restarting the service.

Trust/masking model matches the DragonSync config: the license_key is masked
on non-loopback tablet listeners, and saving the masked placeholder
preserves the existing secret.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from . import __version__
from .config_schema import Field, Group
from .settings import Settings

LOG = logging.getLogger(__name__)

LICENSE_REQUEST_TIMEOUT = 5.0
LICENSE_RESPONSE_BYTE_CAP = 64 * 1024
LICENSE_PATH = "/me"

CONFIG_FILENAME = "dragonscope.cfg"
AUTO_RELOAD_SECONDS = 30
SECRET_PLACEHOLDER = "********"
SENSITIVE_NAMES = ("license_key", "password", "secret", "token", "api_key", "apikey")

_WRITE_LOCK = threading.RLock()

DRAGONSCOPE_GROUPS: tuple[Group, ...] = (
    Group("Remote", (
        Field(
            "remote",
            "Remote URL",
            "text",
            help="Upstream DragonScope API URL, typically https://api.dragonos.net. Leave blank to disable remote decoding.",
        ),
        Field(
            "license_key",
            "License key",
            "password",
            help="DragonScope API key (format dk_…). Required for O4 decode. Without it, DragonScope runs in detection-only mode.",
        ),
    )),
    Group("Listener", (
        Field(
            "listen_port",
            "Listen port",
            "int",
            help="Port DragonScope binds locally. Default 80. Ports below 1024 require DragonScope to be run as root.",
            min_value=1,
            max_value=65535,
        ),
        Field(
            "listen_addr",
            "Listen address",
            "text",
            help="Address DragonScope binds. Default 0.0.0.0 (all interfaces).",
        ),
    )),
)

_FIELDS = {field.key: field for group in DRAGONSCOPE_GROUPS for field in group.fields}


def read_dragonscope(settings: Settings) -> dict[str, Any]:
    path = settings.dragonscope_dir / CONFIG_FILENAME
    show_sensitive = settings.is_local_bind()
    values, parse_error = _load_values(path)
    form_groups: list[dict[str, Any]] = []
    for group in DRAGONSCOPE_GROUPS:
        fields_out = []
        for field in group.fields:
            value = values.get(field.key, "")
            display_value = "" if value is None else str(value)
            if display_value and not show_sensitive and _is_sensitive(field.key):
                display_value = SECRET_PLACEHOLDER
            fields_out.append({
                "key": field.key,
                "label": field.label,
                "kind": field.kind,
                "section": "",
                "options": list(field.options),
                "help": field.help,
                "value": display_value,
            })
        form_groups.append({"title": group.title, "fields": fields_out})
    return {
        "path": str(path),
        "dir": str(settings.dragonscope_dir),
        "exists": path.exists(),
        "write_allowed": settings.can_write_config(),
        "auto_reload_seconds": AUTO_RELOAD_SECONDS,
        "error": parse_error,
        "form": {
            "name": CONFIG_FILENAME,
            "path": str(path),
            "exists": path.exists(),
            "groups": form_groups,
        },
    }


def write_dragonscope(settings: Settings, updates: dict[str, Any]) -> dict[str, Any]:
    if not settings.can_write_config():
        raise PermissionError("config writes are disabled for this bind mode")
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object")

    unknown = set(updates) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unsupported keys: {', '.join(sorted(unknown))}")

    # Strip sensitive placeholders so we don't overwrite the existing secret.
    updates = {
        key: value
        for key, value in updates.items()
        if not (_is_sensitive(key) and str(value).strip() == SECRET_PLACEHOLDER)
    }

    path = settings.dragonscope_dir / CONFIG_FILENAME
    with _WRITE_LOCK:
        current, _parse_error = _load_values(path)
        merged: dict[str, Any] = dict(current)
        for key, value in updates.items():
            merged[key] = _coerce(_FIELDS[key], value)

        new_text = json.dumps(merged, indent=2) + "\n"
        existing_text = ""
        if path.exists():
            try:
                existing_text = path.read_text(encoding="utf-8")
            except OSError:
                existing_text = ""
        if existing_text == new_text:
            return {
                "ok": True,
                "path": str(path),
                "backup_path": None,
                "unchanged": True,
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if path.exists():
            backup_path = path.with_name(
                f"{path.name}.bak.{time.strftime('%Y%m%d%H%M%S')}"
            )
            backup_path.write_bytes(path.read_bytes())

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{CONFIG_FILENAME}.", suffix=".tmp", dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    return {
        "ok": True,
        "path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "unchanged": False,
    }


def _load_values(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"read failed: {exc}"
    if not raw.strip():
        return {}, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return {}, "config root must be a JSON object"
    return payload, None


def _coerce(field: Field, value: Any) -> Any:
    if field.kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{field.key} must be a number")
        if isinstance(value, str) and value.strip() == "":
            return ""
        number = int(value)
        if field.min_value is not None and number < field.min_value:
            raise ValueError(f"{field.key} must be at least {int(field.min_value)}")
        if field.max_value is not None and number > field.max_value:
            raise ValueError(f"{field.key} must be at most {int(field.max_value)}")
        return number
    if field.kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
    if field.kind == "float":
        if isinstance(value, str) and value.strip() == "":
            return ""
        return float(value)
    return "" if value is None else str(value)


def _is_sensitive(key: str) -> bool:
    lower = key.lower()
    return any(name in lower for name in SENSITIVE_NAMES)


def check_license(settings: Settings) -> dict[str, Any]:
    """Verify the license against /me on the configured remote."""
    if not settings.dragonscope_license_check_enabled:
        raise PermissionError("license check is disabled")

    path = settings.dragonscope_dir / CONFIG_FILENAME
    values, parse_error = _load_values(path)
    if parse_error:
        return {"ok": False, "error": f"could not read {CONFIG_FILENAME}: {parse_error}"}

    remote = str(values.get("remote", "")).strip().rstrip("/")
    key = str(values.get("license_key", "")).strip()

    if not remote or remote in ("https://CHANGE_ME", "CHANGE_ME"):
        return {"ok": False, "error": "no remote URL configured in dragonscope.cfg"}
    if not key or key in ("CHANGE_ME",):
        return {"ok": False, "error": "no license key configured in dragonscope.cfg"}

    try:
        parts = urlsplit(remote)
    except ValueError as exc:
        return {"ok": False, "error": f"invalid remote URL: {exc}"}
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return {"ok": False, "error": "remote URL must be http:// or https:// with a host"}
    check_url = urlunsplit((parts.scheme, parts.netloc, LICENSE_PATH, "", ""))

    req = Request(
        check_url,
        headers={
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": f"wardragon-console/{__version__}",
        },
    )
    try:
        with urlopen(req, timeout=LICENSE_REQUEST_TIMEOUT) as resp:
            raw = resp.read(LICENSE_RESPONSE_BYTE_CAP)
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read(1024).decode("utf-8", errors="replace")
        except Exception:
            pass
        if exc.code == 401:
            reason = "license key missing or invalid"
        elif exc.code == 403:
            reason = "license key rejected"
        else:
            reason = f"HTTP {exc.code}"
        LOG.info("license check %s -> %s", check_url, exc.code)
        return {"ok": False, "error": reason, "status": exc.code, "body": body[:256]}
    except (URLError, TimeoutError, OSError) as exc:
        LOG.info("license check %s -> %s", check_url, exc)
        return {"ok": False, "error": f"cannot reach license API: {exc}"}

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"invalid response: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "response was not a JSON object"}

    return {
        "ok": True,
        "checked_at": time.time(),
        "endpoint": check_url,
        "data": data,
    }
