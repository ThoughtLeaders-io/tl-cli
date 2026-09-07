"""Tests for skills/_shared/tl_data.py — the shared skill-script wrapper.

The wrapper is not part of the installed package (skill scripts load it via a
sys.path hook), so the tests import it the same way and drive it against a
stub `tl` binary selected with TL_CLI_BIN. Failure classification must come
from the exit code alone — the stub's stderr deliberately avoids the old
magic words ("auth", "credit", "403") to prove no string sniffing remains.
"""

import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parents[1] / "skills" / "_shared"
sys.path.insert(0, str(_SHARED))
import tl_data  # noqa: E402


def _stub(tmp_path: Path, *, exit_code: int = 0, stdout: str = "",
          stderr: str = "") -> Path:
    """A fake `tl` that records its argv/stdin and plays a canned response."""
    record = tmp_path / "call.json"
    script = tmp_path / "tl-stub.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"rec = {str(record)!r}\n"
        "payload = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
        "open(rec, 'w').write(json.dumps(payload))\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )
    runner = tmp_path / "tl"
    runner.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    runner.chmod(runner.stat().st_mode | stat.S_IEXEC)
    return runner


def _use(monkeypatch, binary: Path) -> None:
    monkeypatch.setattr(tl_data, "TL_BIN", str(binary))


def _recorded(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "call.json").read_text())


class TestHappyPath:
    def test_db_pg_sends_sql_on_stdin_and_unwraps_rows(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout='{"results": [{"id": 1}]}'))
        rows = tl_data.db_pg("SELECT 1")
        assert rows == [{"id": 1}]
        call = _recorded(tmp_path)
        assert call["argv"] == ["db", "pg", "-", "--json"]
        assert call["stdin"] == "SELECT 1"

    def test_db_es_sends_json_body_on_stdin(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout='{"results": []}'))
        assert tl_data.db_es({"size": 1}) == []
        call = _recorded(tmp_path)
        assert call["argv"] == ["db", "es", "-", "--json"]
        assert json.loads(call["stdin"]) == {"size": 1}

    def test_rows_unwraps_alternate_envelopes_and_bare_lists(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout='{"rows": [{"a": 1}]}'))
        assert tl_data.db_fb("SELECT 1") == [{"a": 1}]
        _use(monkeypatch, _stub(tmp_path, stdout='[{"b": 2}]'))
        assert tl_data.db_fb("SELECT 1") == [{"b": 2}]

    def test_whoami_returns_dict(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout='{"user": "x"}'))
        assert tl_data.whoami() == {"user": "x"}


class TestExitCodeClassification:
    # stderr text avoids every magic word the old sniffing keyed on.
    def test_exit_2_is_unauthenticated(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, exit_code=2, stderr="denied"))
        with pytest.raises(tl_data.CliUnavailable, match="not authenticated"):
            tl_data.db_pg("SELECT 1")

    def test_exit_4_is_out_of_credits(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, exit_code=4, stderr="balance gone"))
        with pytest.raises(tl_data.CliUnavailable, match="out of credits"):
            tl_data.db_pg("SELECT 1")

    def test_exit_5_is_access_denied(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, exit_code=5, stderr="Superuser only"))
        with pytest.raises(tl_data.CliUnavailable, match="access denied"):
            tl_data.db_pg("SELECT 1")

    def test_exit_3_is_retryable_data_error(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, exit_code=3, stderr="slow down"))
        with pytest.raises(tl_data.DataError, match="retryable"):
            tl_data.db_pg("SELECT 1")

    def test_exit_1_is_plain_data_error(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, exit_code=1, stderr="no such table"))
        with pytest.raises(tl_data.DataError, match="failed: no such table"):
            tl_data.db_pg("SELECT 1")

    def test_missing_binary_is_unavailable(self, tmp_path, monkeypatch):
        _use(monkeypatch, tmp_path / "nope")
        with pytest.raises(tl_data.CliUnavailable, match="not found on PATH"):
            tl_data.preflight()


class TestBadOutput:
    def test_non_json_output_raises_data_error(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout="Fancy table, not JSON"))
        with pytest.raises(tl_data.DataError, match="non-JSON"):
            tl_data.db_pg("SELECT 1")

    def test_empty_output_is_empty_rows_not_an_error(self, tmp_path, monkeypatch):
        _use(monkeypatch, _stub(tmp_path, stdout=""))
        assert tl_data.db_pg("SELECT 1") == []
