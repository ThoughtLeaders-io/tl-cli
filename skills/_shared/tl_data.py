#!/usr/bin/env python3
"""Shared data-access seam for skills that shell out to the ``tl`` CLI.

One wrapper instead of a copy per script. Import it with a two-line path hook
(no ``cd`` into any skill directory, so concurrent runs on different channels
never fight over a working directory):

    import pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
    import tl_data

(The module is deliberately NOT named ``tl_cli``: the installed package is,
and a path hook that shadows it is a debugging trap.)

Behaviour every consumer relies on:

* **Query bodies travel on stdin** (``tl db es -``), never argv, so a large
  ids list or SQL never hits an argument-length limit.
* **Every call has a timeout** (default 180s). One hung ``tl`` process must
  not stall a bulk run.
* **Failures are loud.** Auth, credit, plan and network errors raise; they are
  never converted into empty results, because an empty result is data and an
  error is not.
* **Failures are classified by the CLI's exit code**, never by parsing
  stderr: 2 auth required, 4 out of credits, 5 access denied, 3 rate-limit or
  server error, 1 other. The mapping is documented in AGENTS.md.

Public API:

    db_pg(sql)        -> list[dict]   rows
    db_fb(sql)        -> list[dict]   rows
    db_es(body: dict) -> list[dict]   rows (the CLI's ``results`` list)
    whoami()          -> dict
    preflight()       -> None         raises CliUnavailable if unusable
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

TL_BIN = os.environ.get("TL_CLI_BIN", "tl")
DEFAULT_TIMEOUT = 180


class CliUnavailable(RuntimeError):
    """The tl CLI cannot be used: missing, not authenticated, out of credits,
    or the account lacks the required plan/permission."""


class DataError(RuntimeError):
    """A query executed but failed or returned something unreadable."""


def _child_env() -> dict[str, str]:
    # Force UTF-8 in the child so `tl`'s output survives Windows consoles
    # whose default codec would mangle it before we ever see the JSON.
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _tl(args: list[str], *, input_text: str | None = None,
        timeout: int = DEFAULT_TIMEOUT) -> str:
    exe = shutil.which(TL_BIN) or TL_BIN
    try:
        proc = subprocess.run(
            [exe, *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CliUnavailable(
            f"`{TL_BIN}` CLI not found on PATH. Install it (see the tl-setup "
            f"skill) and run `tl auth login`. ({exc})"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DataError(
            f"tl {' '.join(args[:3])} timed out after {timeout}s"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode == 2:
            raise CliUnavailable(
                "tl CLI is not authenticated. Run `tl auth login` (or set "
                "TL_API_KEY), then retry. " + err
            )
        if proc.returncode == 4:
            raise CliUnavailable("tl CLI is out of credits: " + err)
        if proc.returncode == 5:
            raise CliUnavailable(
                "tl CLI access denied (plan or permission): " + err
            )
        if proc.returncode == 3:
            raise DataError(
                f"tl {' '.join(args[:3])} rate-limited or server error "
                f"(retryable): {err[:500]}"
            )
        raise DataError(f"tl {' '.join(args[:3])} failed: {err[:500]}")
    return proc.stdout


def _tl_json(args: list[str], *, input_text: str | None = None,
             timeout: int = DEFAULT_TIMEOUT):
    out = _tl(args, input_text=input_text, timeout=timeout).strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise DataError(
            f"tl {' '.join(args[:3])} returned non-JSON output: {out[:300]}"
        ) from exc


def _rows(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, dict):
        # A 200 with premium fields withheld carries "_upgrade_required"
        # next to the rows. Unwrapping past it would let a plan-gated run
        # read the gaps as "no data exists" (e.g. a complete-looking corpus
        # with no transcripts), so it fails loudly like any other denial.
        notice = data.get("_upgrade_required")
        if isinstance(notice, dict):
            fields = ", ".join(str(f) for f in notice.get("fields") or [])
            raise DataError(
                "tl withheld premium field(s) from this response"
                + (f" ({fields})" if fields else "") + ": "
                + (notice.get("message")
                   or "some fields are available on paid plans")
            )
        for key in ("results", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return list(data)


def db_pg(sql: str, *, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    return _rows(_tl_json(["db", "pg", "-", "--json"], input_text=sql,
                          timeout=timeout))


def db_fb(sql: str, *, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    return _rows(_tl_json(["db", "fb", "-", "--json"], input_text=sql,
                          timeout=timeout))


def db_es(body: dict, *, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Run an ES search body and return the rows.

    ``tl db es`` returns ``{"results": [...]}`` (flat rows), not the native
    ``hits.hits`` shape.
    """
    return _rows(_tl_json(["db", "es", "-", "--json"],
                          input_text=json.dumps(body), timeout=timeout))


def cli_rows(args: list[str], *, input_text: str | None = None,
             timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Run any ``tl`` command with ``--json`` and return unwrapped rows."""
    return _rows(_tl_json(args, input_text=input_text, timeout=timeout))


def whoami(*, timeout: int = DEFAULT_TIMEOUT) -> dict:
    data = _tl_json(["whoami", "--json"], timeout=timeout)
    return data if isinstance(data, dict) else {}


def preflight() -> None:
    """Confirm the tl CLI is usable; raise CliUnavailable otherwise."""
    _tl(["whoami"], timeout=60)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "preflight":
        preflight()
        print("OK")
    else:
        sys.exit("usage: tl_data.py preflight")
