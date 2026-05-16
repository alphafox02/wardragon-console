from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .config_view import write_curated_config
from .settings import Settings


CERT_ROLES = {
    "tak_p12": {
        "field": "tak_tls_p12",
        "extensions": {".p12", ".pfx"},
        "clear": {"tak_tls_certfile": "", "tak_tls_keyfile": ""},
    },
    "tak_cert": {
        "field": "tak_tls_certfile",
        "extensions": {".pem", ".crt", ".cer"},
        "clear": {"tak_tls_p12": "", "tak_tls_p12_pass": ""},
    },
    "tak_key": {
        "field": "tak_tls_keyfile",
        "extensions": {".pem", ".key"},
        "clear": {"tak_tls_p12": "", "tak_tls_p12_pass": ""},
    },
    "tak_ca": {
        "field": "tak_tls_cafile",
        "extensions": {".pem", ".crt", ".cer"},
        "clear": {},
    },
}


def upload_tak_cert(
    settings: Settings,
    role: str,
    filename: str,
    data: bytes,
    p12_password: str = "",
) -> dict[str, Any]:
    if not settings.cert_upload_enabled:
        raise PermissionError("certificate upload is disabled")
    if not settings.can_write_config():
        raise PermissionError("certificate upload is disabled for this bind mode")
    if role not in CERT_ROLES:
        raise ValueError("unsupported certificate role")
    if not data:
        raise ValueError("uploaded file is empty")
    if len(data) > settings.cert_upload_max_bytes:
        raise ValueError(f"uploaded file is larger than {settings.cert_upload_max_bytes} bytes")

    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    spec = CERT_ROLES[role]
    if suffix not in spec["extensions"]:
        allowed = ", ".join(sorted(spec["extensions"]))
        raise ValueError(f"{role} must use one of: {allowed}")

    if role != "tak_p12":
        _validate_pem_like(data, role)

    cert_dir = (settings.dragonsync_dir / "certs").resolve()
    cert_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_inside(cert_dir, cert_dir)
    target = (cert_dir / f"{time.strftime('%Y%m%d%H%M%S')}-{safe_name}").resolve()
    _assert_inside(target, cert_dir)

    fd, tmp_name = tempfile.mkstemp(prefix=".upload.", suffix=".tmp", dir=str(cert_dir))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

    updates = dict(spec["clear"])
    updates[spec["field"]] = str(target)
    if role == "tak_p12" and p12_password:
        updates["tak_tls_p12_pass"] = p12_password
    config_result = write_curated_config(settings, "config.ini", updates)
    return {
        "ok": True,
        "role": role,
        "path": str(target),
        "config": config_result,
    }


def _safe_filename(filename: str) -> str:
    base = Path(filename or "upload").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not safe:
        raise ValueError("invalid filename")
    return safe[:96]


def _validate_pem_like(data: bytes, role: str) -> None:
    text = data[:8192].decode("utf-8", errors="ignore")
    if role in {"tak_cert", "tak_ca"} and "BEGIN CERTIFICATE" not in text:
        raise ValueError("PEM certificate upload must contain a CERTIFICATE block")
    if role == "tak_key" and "PRIVATE KEY" not in text:
        raise ValueError("PEM key upload must contain a PRIVATE KEY block")


def _assert_inside(path: Path, root: Path) -> None:
    path.relative_to(root)
