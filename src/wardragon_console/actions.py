from __future__ import annotations

import subprocess
from typing import Any

from .settings import Settings


def restart_dragonsync(settings: Settings) -> dict[str, Any]:
    if not settings.can_restart_dragonsync():
        raise PermissionError("DragonSync restart is disabled for this bind mode")
    if "/" in settings.dragonsync_service_name or settings.dragonsync_service_name.startswith("-"):
        raise ValueError("invalid DragonSync service name")

    cmd = ["sudo", "-n", "/bin/systemctl", "restart", settings.dragonsync_service_name]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "command": " ".join(cmd),
    }
