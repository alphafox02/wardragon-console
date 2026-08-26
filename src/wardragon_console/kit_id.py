"""Read/write for the DragonSync kit-id override file.

The file lives adjacent to wardragon_monitor.py at
<dragonsync_dir>/kit-id-override and, when present, wins over the
dmidecode serial. See wardragon_monitor.py for the read side.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .settings import Settings


OVERRIDE_FILENAME = "kit-id-override"
# Alphanumeric plus dot, underscore, hyphen; 1-32 chars. Mirrors the
# monitor's read-side validation so callers see the same rule on both ends.
VALID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _path(settings: Settings) -> Path:
    return settings.dragonsync_dir / OVERRIDE_FILENAME


def read_override(settings: Settings) -> dict:
    """Return {value, path, exists}. value is "" when the file is absent."""
    p = _path(settings)
    try:
        text = p.read_text().strip()
    except FileNotFoundError:
        return {"value": "", "path": str(p), "exists": False}
    except OSError as exc:
        raise RuntimeError(f"cannot read {p}: {exc}") from exc
    return {"value": text, "path": str(p), "exists": True}


def write_override(settings: Settings, value: str) -> dict:
    """Set (non-empty value) or clear (empty/None value) the override.

    Returns {ok, value, path, cleared}. Raises PermissionError when the
    console is running in read-only mode, ValueError for bad input.
    """
    if not settings.can_write_config():
        raise PermissionError("config writes are disabled on this console")

    p = _path(settings)
    cleaned = (value or "").strip()

    if not cleaned:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"cannot remove {p}: {exc}") from exc
        return {"ok": True, "value": "", "path": str(p), "cleared": True}

    if not VALID_RE.match(cleaned):
        raise ValueError(
            f"invalid kit id suffix; must match {VALID_RE.pattern}"
        )

    # Atomic write via temp+rename in the same directory.
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kit-id-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(cleaned + "\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {"ok": True, "value": cleaned, "path": str(p), "cleared": False}
