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
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from tl_cli import __version__
from tl_cli.commands.setup import _find_claude_binary

CACHE_DIR = Path.home() / ".cache" / "tl-cli"
CACHE_PATH = CACHE_DIR / "version-check.json"
CACHE_TTL_SECONDS = 3600  # 1 hour
LATEST_URL = "https://api.github.com/repos/ThoughtLeaders-io/thoughtleaders-cli/releases/latest"
REQUEST_TIMEOUT = 2  # tight — the user is already waiting to see their shell prompt back
WIN_UPGRADE_RESCHEDULE_WINDOW = 600  # 10 minutes: don't re-schedule a background upgrade we already queued


def _detect_install_method() -> str | None:
    """Return 'pipx', 'uv', or None (dev/pip install — don't auto-upgrade).

    Both the new distribution name (`thoughtleaders-cli`) and the legacy one
    (`tl-cli`) are matched so users who installed before the rename keep
    auto-updating. `Path.as_posix()` normalises Windows backslashes so the
    same substring checks work on every platform.
    """
    exe = Path(sys.executable).as_posix()
    for dist_name in ("thoughtleaders-cli", "tl-cli"):
        if f"/pipx/venvs/{dist_name}/" in exe:
            return "pipx"
        if f"/uv/tools/{dist_name}/" in exe:
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


REPO_URL = "https://github.com/ThoughtLeaders-io/thoughtleaders-cli.git"


def _run_upgrade(method: str, latest: str) -> None:
    """Run the upgrade. Progress goes to stderr so piped stdout stays clean.

    Uses `install --force` with the new tag URL. pipx/uv pin the original
    install spec including the git tag, so a plain `upgrade` re-installs
    the same version — `--force` is the only way to advance the pinned tag.

    On Windows the running tl.exe holds an exclusive lock on its own file,
    so pipx/uv can never replace it in-process — every attempt fails with
    WinError 32 and leaves ``~``-prefixed orphan dirs in site-packages
    that wedge the next launch with ``ModuleNotFoundError: No module
    named 'tl_cli'``. The Windows path spawns a detached helper instead
    that waits for our PID to exit and then runs the upgrade.

    On a successful upgrade (POSIX inline path, or the detached helper),
    Claude Code and OpenCode skills are re-synced if their binaries are
    on PATH, so the new version's skills land in ~/.claude/ and
    ~/.config/opencode/ without the user having to remember to run
    `tl setup ...`.
    """
    tagged_url = f"git+{REPO_URL}@v{latest}"
    cmd = {
        "pipx": ["pipx", "install", "--force", tagged_url],
        "uv": ["uv", "tool", "install", "--force", tagged_url],
    }.get(method)
    if not cmd:
        return

    if sys.platform == "win32":
        if _spawn_detached_windows_upgrade(cmd, latest):
            print(
                f"[tl-cli] upgrade {__version__} → {latest} scheduled "
                f"(runs after this command exits; log: "
                f"{CACHE_DIR / f'upgrade-{latest}.log'})",
                file=sys.stderr,
            )
            _mark_upgrade_scheduled(latest)
        return

    print(
        f"[tl-cli] upgrading {__version__} → {latest} via {method}…",
        file=sys.stderr,
    )
    # Capture output so a noisy traceback from a broken upgrader doesn't
    # get dumped into the user's shell — we surface it deliberately on
    # failure alongside an actionable next-step message.
    try:
        result = subprocess.run(cmd, check=False, timeout=60, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"[tl-cli] could not run {method}: {exc}\n"
            f"[tl-cli] upgrade manually with:\n  {' '.join(cmd)}",
            file=sys.stderr,
        )
        return
    binary_intact = _verify_tl_binary_intact()
    if result.returncode == 0 and binary_intact:
        _resync_integrations()
        return
    _report_upgrade_failure(method, cmd, result, binary_intact=binary_intact, latest=latest)


def _spawn_detached_windows_upgrade(cmd: list[str], latest: str) -> bool:
    """Schedule the upgrade to run after this process exits.

    Writes a small .cmd helper that polls until our PID disappears and
    then runs the upgrader. Spawned with CREATE_NO_WINDOW |
    CREATE_BREAKAWAY_FROM_JOB so it survives this process and any
    job-object-owned shell that launched us. Output is appended to a
    log file under ~/.cache/tl-cli/ so the user can diagnose failures
    after their shell prompt returns.

    Returns True on successful schedule. Idempotent against repeated
    invocations: see `_already_scheduled`.
    """
    import os

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"[tl-cli] could not create cache dir for upgrade helper: {exc}",
            file=sys.stderr,
        )
        return False

    log_path = CACHE_DIR / f"upgrade-{latest}.log"
    script_path = CACHE_DIR / f"upgrade-{latest}.cmd"
    # Per-helper temp file for tasklist output (a pipe would die; see below).
    tmp_path = CACHE_DIR / f"upgrade-{latest}.tasklist.tmp"

    parent_pid = os.getpid()
    quoted_cmd = " ".join(f'"{a}"' for a in cmd)

    # CRLF line endings + cmd.exe-safe quoting.
    #
    # We deliberately avoid the pipe-based idiom `tasklist | findstr`: a
    # detached cmd.exe (CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB) has
    # no console for the pipe sub-shells to attach to, and they exit
    # silently the moment the helper hits a `|`. Routing through a temp
    # file keeps every command as a plain redirected child.
    script = (
        "@echo off\r\n"
        f'echo [tl-cli upgrader] waiting for parent PID {parent_pid} to exit > "{log_path}"\r\n'
        ":wait\r\n"
        f'tasklist /FI "PID eq {parent_pid}" /NH 2>NUL > "{tmp_path}"\r\n'
        f'findstr /C:"{parent_pid}" "{tmp_path}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "    ping -n 2 127.0.0.1 >NUL\r\n"
        "    goto wait\r\n"
        ")\r\n"
        f'del "{tmp_path}" 2>NUL\r\n'
        f'echo [tl-cli upgrader] running: {quoted_cmd} >> "{log_path}"\r\n'
        f'{quoted_cmd} >> "{log_path}" 2>&1\r\n'
        "set RC=%ERRORLEVEL%\r\n"
        f'echo [tl-cli upgrader] exit code %RC% >> "{log_path}"\r\n'
        "if not %RC%==0 goto end\r\n"
        "set CLAUDE_FOUND=0\r\n"
        "where claude >NUL 2>&1\r\n"
        "if not errorlevel 1 set CLAUDE_FOUND=1\r\n"
        'if exist "%USERPROFILE%\\.local\\bin\\claude.exe" set CLAUDE_FOUND=1\r\n'
        'if exist "%APPDATA%\\npm\\claude.cmd" set CLAUDE_FOUND=1\r\n'
        "if %CLAUDE_FOUND%==1 (\r\n"
        f'    echo [tl-cli upgrader] re-syncing claude skills >> "{log_path}"\r\n'
        f'    tl setup claude --json >> "{log_path}" 2>&1\r\n'
        ")\r\n"
        "where opencode >NUL 2>&1\r\n"
        "if not errorlevel 1 (\r\n"
        f'    echo [tl-cli upgrader] re-syncing opencode skills >> "{log_path}"\r\n'
        f'    tl setup opencode --json >> "{log_path}" 2>&1\r\n'
        ")\r\n"
        ":end\r\n"
    )

    try:
        script_path.write_text(script)
    except OSError as exc:
        print(f"[tl-cli] could not write upgrade helper: {exc}", file=sys.stderr)
        return False

    # creationflags constants — repeated here rather than referenced from
    # subprocess.* so this works on Python builds where the symbols are
    # guarded behind sys.platform checks.
    #
    # CREATE_NO_WINDOW (not DETACHED_PROCESS): a fully-detached cmd.exe
    # has no console for spawned child commands to inherit, which breaks
    # piped sub-shells and a few utilities that try to query the console.
    # CREATE_NO_WINDOW gives us "no visible window" while keeping the
    # console subsystem available for children.
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    CREATE_NO_WINDOW = 0x08000000

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(script_path)],
            creationflags=(
                CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(CACHE_DIR),
        )
    except OSError as exc:
        print(
            f"[tl-cli] could not schedule background upgrade: {exc}\n"
            f"[tl-cli] upgrade manually with:\n  {quoted_cmd}",
            file=sys.stderr,
        )
        return False
    return True


def _mark_upgrade_scheduled(latest: str) -> None:
    """Record in the version-check cache that we've queued a background
    upgrade for ``latest`` so subsequent invocations don't re-schedule
    while the first helper is still pending."""
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        cache = {}
    cache["scheduled_at"] = time.time()
    cache["scheduled_for"] = latest
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _already_scheduled(latest: str) -> bool:
    """True if we recently queued the same upgrade — caller should skip."""
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if cache.get("scheduled_for") != latest:
        return False
    scheduled_at = cache.get("scheduled_at")
    if not isinstance(scheduled_at, (int, float)):
        return False
    return time.time() - scheduled_at < WIN_UPGRADE_RESCHEDULE_WINDOW


def _verify_tl_binary_intact() -> bool:
    """Sanity-check the upgraded install: is there still a working `tl`?

    `uv tool install --force` removes the previous install BEFORE it
    builds the new one. A failing build leaves the user with no `tl`
    binary at all — even though the exit-code branch will already have
    flagged the failure, we use this check to emphasize the now-missing
    state in the recovery message. Also catches the rarer case where
    the upgrader returns 0 but the resulting binary is unusable.

    Cheapest signal that won't trip on harmless slowness: does
    `tl --version` exit 0 within a couple of seconds?
    """
    tl_bin = shutil.which("tl")
    if not tl_bin:
        return False
    try:
        result = subprocess.run(
            [tl_bin, "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _report_upgrade_failure(
    method: str,
    cmd: list[str],
    result: subprocess.CompletedProcess,
    *,
    binary_intact: bool,
    latest: str,
) -> None:
    """Print a user-friendly failure message after a non-zero upgrader exit.

    On Windows in particular, `pipx.exe` can be a broken shim that errors
    with `ModuleNotFoundError: No module named 'pipx'`. We detect that and
    give a targeted hint instead of just echoing the traceback.

    If the install is now corrupted (no working `tl` on PATH), we
    emphasize that in the message and surface the version-pinned
    recovery command for the *previous* good version too.
    """
    combined_err = (result.stderr or '') + (result.stdout or '')
    if result.returncode == 0 and not binary_intact:
        # Upgrader claimed success but the binary is gone or broken — rare
        # but a worse failure mode than a non-zero exit because there's no
        # error text to anchor on.
        print(
            "[tl-cli] Upgrade reported success but the `tl` command is missing or broken.",
            file=sys.stderr,
        )
    else:
        print(
            f"[tl-cli] automatic upgrade failed (exit {result.returncode}).",
            file=sys.stderr,
        )

    if not binary_intact:
        print(
            f"[tl-cli] Your install was removed by `{method}` before the new build "
            f"could finish, so the `tl` command is gone right now.",
            file=sys.stderr,
        )
        print(
            f"[tl-cli] Recover with the previous known-good release:\n"
            f"  {cmd[0]} {'tool ' if method == 'uv' else ''}install --force "
            f"git+{REPO_URL}@v{__version__}",
            file=sys.stderr,
        )
        print(
            f"[tl-cli] Or retry the new version once the issue is fixed:\n"
            f"  {' '.join(cmd)}",
            file=sys.stderr,
        )
    elif "No module named 'pipx'" in combined_err:
        print(
            "[tl-cli] Your pipx install appears broken (its launcher can't find the pipx module).\n"
            "[tl-cli] Reinstall pipx from your system Python, then rerun the upgrade.\n"
            "[tl-cli] Or switch to uv:  uv tool install --force " + cmd[-1],
            file=sys.stderr,
        )
    else:
        print(
            f"[tl-cli] To upgrade manually, run:\n  {' '.join(cmd)}",
            file=sys.stderr,
        )
    if combined_err.strip():
        print("[tl-cli] Upgrader output:", file=sys.stderr)
        sys.stderr.write(combined_err if combined_err.endswith('\n') else combined_err + '\n')


def _resync_integrations() -> None:
    """Re-sync Claude Code and OpenCode skills after a self-upgrade.

    Spawned as a subprocess against the freshly-installed `tl` binary —
    the running process holds the OLD code in memory, so calling the
    setup functions in-process would (depending on import caching) copy
    the wrong assets. Subprocess re-execs the new code from disk.

    Conditional on each tool's binary being on PATH; everything is
    silent on failure (a skill resync issue must never fail the
    upgrade itself).
    """
    tl_bin = shutil.which("tl")
    if not tl_bin:
        return
    for tool, binary in (
        ("claude", "claude"),
        ("opencode", "opencode"),
        ("gemini", "gemini"),
        ("codex", "codex"),
    ):
        # claude is often installed off-PATH (native installer, npm prefix);
        # use the same probing the setup command uses.
        found = _find_claude_binary() if binary == "claude" else shutil.which(binary)
        if not found:
            continue
        print(f"[tl-cli] re-syncing {tool} skills…", file=sys.stderr)
        try:
            subprocess.run(
                [tl_bin, "setup", tool, "--json"],
                capture_output=True,
                # The Claude Code path shells out to `claude` several times
                # (marketplace add + update, plugin install + update), each
                # with its own timeout — a 120s budget for the whole resync
                # can expire mid-sequence on a slow network.
                timeout=300,
                check=False,
            )
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

        # On Windows the upgrade is detached: don't re-queue it on every
        # subsequent tl invocation while the first helper is still pending.
        if sys.platform == "win32" and _already_scheduled(latest):
            return

        _run_upgrade(method, latest)
    except Exception:
        # Never let a version-check bug break the user's workflow.
        pass


def force_upgrade(force: bool = False) -> None:
    """Explicitly requested upgrade — bypasses cache, prints verbose output.

    When `force` is True, runs the upgrade even when the local version
    already matches the latest — useful for reinstalls or recovering from
    a corrupted install.
    """
    method = _detect_install_method()
    if not method:
        print(
            "tl-cli was not installed via pipx or uv — cannot auto-upgrade.\n"
            "Update it with the same tool you used to install it.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Checking for updates (current: {__version__})…", file=sys.stderr)
    latest = _fetch_latest_version()
    if latest is None:
        print("Could not reach GitHub to check for updates.", file=sys.stderr)
        sys.exit(1)

    _write_cache(latest)

    try:
        newer = _version_tuple(latest) > _version_tuple(__version__)
    except ValueError:
        newer = False

    if not newer and not force:
        print(f"tl-cli {__version__} is already the latest version.", file=sys.stderr)
        return

    if not newer and force:
        print(
            f"tl-cli {__version__} already matches latest {latest}; reinstalling because --force was passed.",
            file=sys.stderr,
        )

    _run_upgrade(method, latest)
