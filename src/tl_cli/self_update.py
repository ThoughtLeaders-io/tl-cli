"""Post-command version check and auto-upgrade for pipx/uv installs.

Runs once per CLI invocation via atexit. Skipped for dev / pip installs
(we only know how to upgrade pipx and `uv tool` installations cleanly).

Network fetches are cached for 1 hour in ~/.cache/tl-cli/version-check.json,
so repeated invocations don't hammer the GitHub API.

All failure paths are silent — version-check issues must never break the
user's actual command output.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from tl_cli import __version__

CACHE_PATH = Path.home() / ".cache" / "tl-cli" / "version-check.json"
CACHE_TTL_SECONDS = 3600  # 1 hour
LATEST_URL = "https://api.github.com/repos/ThoughtLeaders-io/tl-cli/releases/latest"
REQUEST_TIMEOUT = 2  # tight — the user is already waiting to see their shell prompt back


def _detect_install_method() -> str | None:
    """Return 'pipx', 'uv', or None (dev/pip install — don't auto-upgrade)."""
    exe = sys.executable
    if "/pipx/venvs/tl-cli/" in exe:
        return "pipx"
    if "/uv/tools/tl-cli/" in exe:
        return "uv"
    return None


def _read_cache() -> dict | None:
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - cache.get("checked_at", 0) >= CACHE_TTL_SECONDS:
        return None
    return cache


def _write_cache(latest: str | None) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
    except OSError as e:
        print(f"Error writing cache: {e}", file=sys.stderr)
        pass


def _fetch_latest_version() -> str | None:
    """Fetch latest release tag from GitHub. Returns the plain version
    string (e.g. '0.4.2') or None on any failure."""
    try:
        req = urllib.request.Request(
            LATEST_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"tl-cli/{__version__}",
            },
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    tag = (data.get("tag_name") or "").lstrip("v")
    return tag or None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def _run_upgrade(method: str, latest: str) -> None:
    """Block briefly to run the upgrade. Progress goes to stderr so piped
    stdout stays clean."""
    cmd = {
        "pipx": ["pipx", "upgrade", "tl-cli"],
        "uv": ["uv", "tool", "upgrade", "tl-cli"],
    }.get(method)
    if not cmd:
        return
    print(
        f"[tl-cli] upgrading {__version__} → {latest} via {method}…",
        file=sys.stderr,
    )
    try:
        subprocess.run(cmd, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def check_and_upgrade() -> None:
    """Entry point. Runs via atexit; silent on every failure path."""
    try:
        method = _detect_install_method()
        if not method:
            return

        cache = _read_cache()
        if cache is None:
            latest = _fetch_latest_version()
            _write_cache(latest)
        else:
            latest = cache.get("latest")

        if not latest:
            return
        try:
            if _version_tuple(latest) <= _version_tuple(__version__):
                return
        except ValueError:
            return

        _run_upgrade(method, latest)
    except Exception:
        # Never let a version-check bug break the user's workflow.
        pass
