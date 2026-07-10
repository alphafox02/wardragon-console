"""Kit capability detection.

The console runs on two hardware SKUs in the WarDragon family:

* **Elite** (x86_64, second SDR): runs DragonSig for FPV/RF signal detection.
* **Pro** (ARM/Pi, single SDR): does not run DragonSig.

Rather than probing hardware directly, we key on whether the kit was
provisioned for DragonSig by looking for the systemd unit file. This is:

- More reliable than an ``uname -m`` architecture check (future kits may
  vary — someone could ship an ARM Elite tomorrow).
- More reliable than a binary path check — binaries can be stale or manually
  copied without the kit being properly provisioned.
- Overridable via ``WARDRAGON_HAS_DRAGONSIG=yes|no|auto`` for edge cases and
  testing. Default is ``auto`` (filesystem check).

The capability is detected once at process start and never mutates. If an
operator installs DragonSig while the console is running, they can restart
the console to pick up the change.
"""
from __future__ import annotations

import os
from pathlib import Path

SYSTEMD_UNIT_DIRS = (
    "/etc/systemd/system",
    "/lib/systemd/system",
    "/usr/lib/systemd/system",
    "/run/systemd/system",
)

DRAGONSIG_UNIT = "dragonsig.service"


def detect_capabilities() -> dict[str, bool]:
    return {
        "has_dragonsig": _detect_dragonsig(),
    }


def _detect_dragonsig() -> bool:
    override = os.environ.get("WARDRAGON_HAS_DRAGONSIG", "auto").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    for base in SYSTEMD_UNIT_DIRS:
        if (Path(base) / DRAGONSIG_UNIT).exists():
            return True
    return False
