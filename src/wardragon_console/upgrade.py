"""Operator-triggered in-place upgrades via the wardragon-upgrade helper."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .settings import Settings

TARGETS = ("console", "droneid", "dragonsync")
HELPER = Path("/usr/local/bin/wardragon-upgrade")
STATE_DIR = Path("/var/lib/wardragon-upgrade")
UNIT = "wardragon-upgrade.service"
STATUS_MAX_BYTES = 64 * 1024
LOG_TAIL_BYTES = 32 * 1024
START_COOLDOWN_SECONDS = 15.0
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_start_lock = threading.Lock()
_last_start = 0.0


def helper_installed() -> bool:
    return HELPER.is_file()


def start_upgrade(settings: Settings, target: str) -> dict[str, Any]:
    global _last_start
    if not settings.can_upgrade():
        raise PermissionError("in-place upgrade is disabled for this bind mode")
    if target not in TARGETS:
        raise ValueError("unknown upgrade target")
    if not helper_installed():
        return {
            "ok": False,
            "error": "upgrade helper is not installed; run 'sudo packaging/install.sh' once from the console repo",
        }
    with _start_lock:
        now = time.monotonic()
        if now - _last_start < START_COOLDOWN_SECONDS:
            return {"ok": False, "error": "an upgrade was just requested; wait a moment"}
        _last_start = now
        requested_at = time.time()
        try:
            completed = subprocess.run(
                ["sudo", "-n", str(HELPER), "start", target],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"could not start upgrade: {exc}"}
    if completed.returncode == 3:
        return {"ok": False, "error": "another upgrade is already running"}
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        return {"ok": False, "error": f"could not start upgrade: {detail}"}
    return {"ok": True, "target": target, "requested_at": requested_at}


def upgrade_status(target: str) -> dict[str, Any]:
    if target not in TARGETS:
        raise ValueError("unknown upgrade target")
    status = _read_status(STATE_DIR / f"{target}.json")
    running = _unit_active()
    if status and status.get("state") == "running" and not running:
        status["state"] = "interrupted"
        status["message"] = "the upgrade stopped before finishing; see the log"
    return {
        "target": target,
        "helper_installed": helper_installed(),
        "unit_active": running,
        "status": status,
        "log": _tail(STATE_DIR / f"{target}.log"),
    }


def _read_status(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(STATUS_MAX_BYTES)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            data = handle.read()
    except OSError:
        return ""
    text = _ANSI_RE.sub("", data.decode("utf-8", errors="replace"))
    if size > LOG_TAIL_BYTES:
        text = text.split("\n", 1)[-1]
    return text


def _unit_active() -> bool:
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", "--quiet", UNIT],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
