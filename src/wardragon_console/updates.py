"""On-demand GitHub update-availability check.

Strictly read-only and operator-triggered: the UI has a "Check for updates"
button that POSTs to /api/updates/check, which calls this module. No
background polling, no automatic fetch, no execute path. The result is
cached in SnapshotStore so multiple browser tabs see the same answer
without each one re-hitting GitHub.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__
from .settings import Settings
from .state import SnapshotStore

LOG = logging.getLogger(__name__)
GITHUB_API = "https://api.github.com"
USER_AGENT = f"wardragon-console/{__version__}"
RESPONSE_BYTE_CAP = 256 * 1024
REQUEST_TIMEOUT = 5.0


def check_for_updates(settings: Settings, store: SnapshotStore | None = None) -> dict[str, Any]:
    if not settings.update_check_enabled:
        raise PermissionError("update check is disabled")

    payload = {
        "console": _check_repo(
            repo=settings.console_upstream_repo,
            local_version=__version__,
            local_git_dir=None,
        ),
        "dragonsync": _check_repo(
            repo=settings.dragonsync_upstream_repo,
            local_version=None,
            local_git_dir=settings.dragonsync_dir,
        ),
        "checked_at": time.time(),
    }
    if store is not None:
        store.update("updates", payload)
    return payload


def _check_repo(
    *,
    repo: str,
    local_version: str | None,
    local_git_dir: Path | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "repo": repo,
        "local_version": local_version,
        "local_sha": None,
        "upstream_branch": "main",
        "upstream_sha": None,
        "latest_release_tag": None,
        "latest_release_url": None,
        "compare_url": None,
        "update_available": False,
        "error": None,
    }
    if not repo:
        out["error"] = "no repo configured"
        return out

    if local_git_dir is not None:
        out["local_sha"] = _read_local_sha(local_git_dir)

    commits = _api_get(f"/repos/{repo}/commits/{out['upstream_branch']}")
    if commits and commits.get("sha"):
        out["upstream_sha"] = commits["sha"]
    else:
        out["error"] = "could not fetch upstream commit"

    release = _api_get(f"/repos/{repo}/releases/latest")
    if release:
        out["latest_release_tag"] = release.get("tag_name")
        out["latest_release_url"] = release.get("html_url")

    # Decide "update available". Prefer SHA comparison when both sides have
    # one (DragonSync case). Otherwise compare local_version to release tag
    # (console case once we start tagging releases).
    if out["local_sha"] and out["upstream_sha"]:
        if out["local_sha"] != out["upstream_sha"]:
            out["update_available"] = True
            out["compare_url"] = (
                f"https://github.com/{repo}/compare/"
                f"{out['local_sha'][:12]}...{out['upstream_sha'][:12]}"
            )
    elif local_version and out["latest_release_tag"]:
        tag = out["latest_release_tag"].lstrip("v")
        if _version_lt(local_version, tag):
            out["update_available"] = True

    # Display-short SHAs.
    if out["local_sha"]:
        out["local_sha"] = out["local_sha"][:7]
    if out["upstream_sha"]:
        out["upstream_sha"] = out["upstream_sha"][:7]
    return out


def _api_get(path: str) -> dict[str, Any] | None:
    url = f"{GITHUB_API}{path}"
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(RESPONSE_BYTE_CAP)
    except HTTPError as exc:
        if exc.code != 404:
            LOG.debug("github %s -> HTTP %s", path, exc.code)
        return None
    except (URLError, TimeoutError, OSError) as exc:
        LOG.debug("github %s -> %s", path, exc)
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _read_local_sha(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


_VERSION_RE = re.compile(r"\d+")


def _version_lt(a: str, b: str) -> bool:
    """Loose less-than for version strings: extracts numeric parts and compares."""
    try:
        pa = [int(x) for x in _VERSION_RE.findall(a)]
        pb = [int(x) for x in _VERSION_RE.findall(b)]
    except (TypeError, ValueError):
        return False
    if not pa or not pb:
        return False
    return pa < pb
