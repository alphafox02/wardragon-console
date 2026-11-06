"""Read-only view of the DragonSync kit-id override file.

The file lives adjacent to wardragon_monitor.py at
<dragonsync_dir>/kit-id-override and, when present, wins over the
dmidecode serial. See wardragon_monitor.py for the read side.

The console no longer writes it. The Kit ID is the kit's fixed identity:
TAK, MQTT, Lattice and the ATAK plugin track the kit by it, so renaming it
from a settings page split a kit into two. The name shown in TAK is the
kit callsign (config.ini kit_callsign) instead. An override that already
exists keeps working; the console only reports it.
"""
from __future__ import annotations

from pathlib import Path

from .settings import Settings


OVERRIDE_FILENAME = "kit-id-override"


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
