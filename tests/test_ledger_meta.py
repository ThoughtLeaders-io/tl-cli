"""ledger_meta.py: the meta record is the ledger's first line, derived from
the build's files, and the reuse decision follows the freshness rule (≤5 new
uploads and ≤60 days reuses; more of either refreshes; flags override). No
network: the one index count is mocked."""

import datetime as dt
import gzip
import json
import sys
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import store_io  # noqa: E402
import ledger_meta  # noqa: E402


def _jsonl(path: Path, rows: list[dict], gz: bool = False) -> Path:
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text)
    return path


_LEDGER_FACTS = [
    {"fact_id": "f1", "claim": "has a dog", "sensitivity": "none"},
    {"fact_id": "f2", "claim": "wears glasses", "sensitivity": "lifestyle"},
    {"fact_id": "f3", "claim": "lives in Austin", "sensitivity": "none"},
]


def _build_dir(tmp_path: Path, channel: int = 42, facts=None) -> tuple[Path, Path]:
    profiles = tmp_path / "tl-creator-profiles"
    corpus = profiles / ".corpus" / str(channel)
    corpus.mkdir(parents=True)
    if facts is not False:
        _jsonl(profiles / f"{channel}-facts.jsonl",
               _LEDGER_FACTS if facts is None else facts)
    _jsonl(corpus / "corpus.jsonl.gz", [
        {"id": "42:a", "publication_date": "2019-04-02", "cues": []},
        {"id": "42:b", "publication_date": "2026-08-20", "cues": []},
        {"id": "42:c", "publication_date": None, "cues": []},
    ], gz=True)
    _jsonl(corpus / "windows.jsonl.gz", [{"id": "42:a"}] * 7, gz=True)
    _jsonl(corpus / "windows-r2.jsonl.gz", [{"id": "42:b"}] * 3, gz=True)
    _jsonl(corpus / "classified.jsonl", [{"window": {}}] * 6)
    _jsonl(corpus / "gems.jsonl", [{"window": {}}] * 4)
    (corpus / "fetch.json").write_text(json.dumps(
        {"videos_with_transcript": 120, "latest_video_date": "2026-08-25"}))
    (corpus / "fetch-r2.json").write_text(json.dumps(
        {"videos_with_transcript": 121, "latest_video_date": "2026-08-29"}))
    return profiles, corpus


def _header(profiles: Path, channel: int = 42) -> dict:
    return store_io.read_ledger(profiles / f"{channel}-facts.jsonl")[0]


# --------------------------------------------------------------------------- #
# write — the header, in the ledger
# --------------------------------------------------------------------------- #
def test_write_puts_the_meta_record_on_the_ledgers_first_line(tmp_path, capsys):
    profiles, _ = _build_dir(tmp_path)
    rc = ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                           "--channel-name", "Patterrz", "--format", "solo"])
    assert rc == 0
    ledger = profiles / "42-facts.jsonl"
    assert not (profiles / "42-meta.json").exists()      # no sidecar any more
    first = json.loads(ledger.read_text().splitlines()[0])
    assert first["schema"] == "tl-creator-meta/v2"
    meta, facts = store_io.read_ledger(ledger)
    assert meta == first and facts == _LEDGER_FACTS      # facts untouched
    assert meta["channel_id"] == 42 and meta["channel_name"] == "Patterrz"
    assert meta["corpus_window"] == ["2019-04-02", "2026-08-20"]
    assert meta["coverage"] == {"videos_with_transcript": 121, "videos_matched": 3,
                                "passages": 10, "windows_judged": 6, "gems": 4,
                                "facts": 3}
    assert meta["format"] == "solo"
    assert meta["latest_video_date"] == "2026-08-29"   # newest across rounds
    assert meta["rounds"] == 2                          # one fetch summary per round
    assert meta["facts_file"] == "42-facts.jsonl"
    assert "credits_spent" not in meta and "missing" not in meta
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["ledger"].endswith("42-facts.jsonl")


def test_rewriting_the_header_in_place_recounts_and_never_doubles_it(tmp_path):
    profiles, _ = _build_dir(tmp_path)
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles)])
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles)])
    lines = (profiles / "42-facts.jsonl").read_text().splitlines()
    assert len(lines) == 4                              # one header, three facts
    assert _header(profiles)["coverage"]["facts"] == 3  # counted through store_io


def test_write_falls_back_to_the_corpus_when_no_fetch_summary(tmp_path):
    profiles, corpus = _build_dir(tmp_path)
    (corpus / "fetch.json").unlink(), (corpus / "fetch-r2.json").unlink()
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--rounds", "1", "--credits-spent", "1840"])
    meta = _header(profiles)
    assert meta["latest_video_date"] == "2026-08-20"    # newest stored video
    assert meta["coverage"]["videos_with_transcript"] == 0
    assert meta["missing"] == ["fetch.json"]
    assert meta["rounds"] == 1 and meta["credits_spent"] == 1840


def test_write_carries_descriptive_fields_over_from_the_existing_header(tmp_path):
    profiles, corpus = _build_dir(tmp_path)
    ctx = tmp_path / "context.json"
    ctx.write_text(json.dumps({"social_links": ["https://x.com/p"], "about_text": "long",
                               "second_channel_candidates": [
                                   {"name": "Clips", "link": "https://youtube.com/@c",
                                    "source": "social_links", "extra": "dropped"}]}))
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--channel-name", "Patterrz", "--format", "solo",
                      "--format-evidence", "fp 41/1k", "--credits-spent", "12",
                      "--lanes", "transcripts+socials", "--context", str(ctx)])
    first = _header(profiles)
    assert first["lanes"] == "transcripts+socials"
    # the read/unread split is carried even when the context file omits it, so
    # the page's honesty strip can say which linked platforms were actually
    # opened rather than reporting every link as read
    assert first["context"] == {"social_links": ["https://x.com/p"],
                                "social_links_read": [],
                                "social_links_unread": [],
                                "second_channel_candidates": [
                                    {"name": "Clips", "link": "https://youtube.com/@c",
                                     "source": "social_links"}]}
    # a refresh write passes only what changed
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--rounds", "3"])
    again = _header(profiles)
    for key in ("channel_name", "format", "format_evidence", "credits_spent", "lanes",
                "context"):
        assert again[key] == first[key], key
    assert again["rounds"] == 3
    # and a value passed again wins
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--format", "interview"])
    assert _header(profiles)["format"] == "interview"


def test_write_defaults_lanes_to_transcripts(tmp_path):
    profiles, _ = _build_dir(tmp_path)
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles)])
    assert _header(profiles)["lanes"] == "transcripts"


def test_write_refuses_without_a_ledger(tmp_path):
    profiles = tmp_path / "p"
    profiles.mkdir()
    try:
        ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles)])
    except SystemExit as exc:
        assert "run the build first" in str(exc)
    else:
        raise AssertionError("no ledger must be a loud failure")


# --------------------------------------------------------------------------- #
# write --from — the verified facts become the ledger
# --------------------------------------------------------------------------- #
_VERIFIED = [
    {"fact_id": "f1", "claim": "has a dog", "provenance": "transcript",
     "video": "42:a", "quote": "we adopted luna", "start": 12,
     "members": [{"video_id": "a", "start": 12}],
     "verify": {"match": "exact", "found": True, "start": 12, "cue": "we adopted luna"}},
    {"fact_id": "f2", "claim": "posts about bread", "provenance": "social",
     "source_url": "https://example.com/p", "verify": {"match": "n/a"}},
]


def test_write_from_verified_facts_strips_verify_and_keeps_everything_else(tmp_path):
    profiles, _ = _build_dir(tmp_path, facts=False)
    src = _jsonl(tmp_path / "facts.verified.jsonl", _VERIFIED)
    rc = ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                           "--from", str(src), "--channel-name", "Patterrz"])
    assert rc == 0
    meta, facts = store_io.read_ledger(profiles / "42-facts.jsonl")
    assert meta["schema"] == "tl-creator-meta/v2" and meta["coverage"]["facts"] == 2
    assert [f.get("verify") for f in facts] == [None, None]
    assert facts[0]["members"] == [{"video_id": "a", "start": 12}]
    assert facts[0]["quote"] == "we adopted luna" and facts[1]["provenance"] == "social"


def test_write_from_refuses_anything_but_an_exact_transcript_match(tmp_path, capsys):
    profiles, _ = _build_dir(tmp_path, facts=False)
    src = _jsonl(tmp_path / "facts.verified.jsonl", _VERIFIED + [
        {"fact_id": "f3", "claim": "moved to Austin", "provenance": "transcript",
         "video": "42:b", "quote": "we moved", "verify": {"match": "partial"}},
        {"fact_id": "f4", "claim": "runs at dawn", "video": "42:b", "quote": "x"}])
    rc = ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                           "--from", str(src)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "f3 (partial)" in err and "f4 (unverified)" in err
    assert not (profiles / "42-facts.jsonl").exists()   # nothing written


def test_write_from_carries_the_previous_header_over(tmp_path):
    profiles, _ = _build_dir(tmp_path)
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--channel-name", "Patterrz", "--format", "solo",
                      "--lanes", "transcripts+socials"])
    src = _jsonl(tmp_path / "facts.verified.jsonl", _VERIFIED)
    ledger_meta.main(["write", "--channel", "42", "--profiles-dir", str(profiles),
                      "--from", str(src), "--rounds", "2"])
    meta, facts = store_io.read_ledger(profiles / "42-facts.jsonl")
    assert meta["channel_name"] == "Patterrz" and meta["format"] == "solo"
    assert meta["lanes"] == "transcripts+socials" and meta["rounds"] == 2
    assert len(facts) == 2 and meta["coverage"]["facts"] == 2


# --------------------------------------------------------------------------- #
# check — the reuse decision
# --------------------------------------------------------------------------- #
def _ledger(tmp_path: Path, generated_at: str, latest: str = "2026-08-20",
            header: bool = True) -> Path:
    profiles = tmp_path / "tl-creator-profiles"
    profiles.mkdir(exist_ok=True)
    meta = {"schema": "tl-creator-meta/v2", "channel_id": 42,
            "channel_name": "Sydney Watson", "generated_at": generated_at,
            "corpus_window": ["2016-03-01", "2026-08-20"], "coverage": {"facts": 91},
            "latest_video_date": latest, "rounds": 1}
    store_io.write_ledger(profiles / "42-facts.jsonl", meta if header else None,
                           [{"fact_id": "f1", "claim": "x"}] * 91)
    return profiles


def _mock_count(monkeypatch, total: int, seen: list | None = None):
    def fake(args, input_text=None, **kw):
        body = json.loads(input_text)
        if seen is not None:
            seen.append(body)
        return {"total": total, "results": []}
    monkeypatch.setattr(ledger_meta.tl_data, "_tl_json", fake)


def _check(monkeypatch, capsys, profiles: Path, *flags: str, total: int = 0,
           today: dt.date | None = None, seen: list | None = None) -> tuple[str, dict]:
    _mock_count(monkeypatch, total, seen if seen is not None else [])
    if today:
        class _D(dt.date):
            @classmethod
            def today(cls):
                return today
        monkeypatch.setattr(ledger_meta.dt, "date", _D)
    rc = ledger_meta.main(["check", "--channel", "42", "--profiles-dir", str(profiles), *flags])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    if len(lines) == 1:
        return "", json.loads(lines[0])
    return lines[0], json.loads(lines[1])


def test_missing_ledger_means_build_and_no_count(tmp_path, monkeypatch, capsys):
    calls: list = []
    _mock_count(monkeypatch, 0, calls)
    profiles = tmp_path / "empty"
    profiles.mkdir()
    line, out = _check(monkeypatch, capsys, profiles)
    assert out["decision"] == "build" and out["next_round"] == 1
    assert line == "" and calls == []


def test_a_headerless_ledger_is_incomplete_and_rebuilds(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01", header=False)
    _, out = _check(monkeypatch, capsys, profiles)
    assert out["decision"] == "build" and "no meta header" in out["reason"]
    # both keys point at the one machine file
    assert out["facts"] == out["meta"] == str(profiles / "42-facts.jsonl")


def test_a_legacy_sidecar_pair_rebuilds_and_says_so(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01", header=False)
    (profiles / "42-meta.json").write_text(json.dumps(
        {"schema": "tl-creator-meta/v1", "channel_id": 42}))
    _, out = _check(monkeypatch, capsys, profiles)
    assert out["decision"] == "build"
    assert "42-meta.json sidecar" in out["reason"] and "no meta header" in out["reason"]


def test_fresh_ledger_is_reused_with_the_announcement_line(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01")
    line, out = _check(monkeypatch, capsys, profiles, total=3, today=dt.date(2026, 9, 2))
    assert line == ("Found a ledger for Sydney Watson built 2026-09-01 over 2016-03 → "
                    "2026-08-20, 91 facts. 3 videos uploaded since.")
    assert out["decision"] == "reuse" and out["new_videos"] == 3 and out["age_days"] == 1
    assert out["next_round"] == 2 and out["fact_count"] == 91
    assert out["facts"] == out["meta"] == str(profiles / "42-facts.jsonl")


def test_the_count_is_one_range_query_against_latest_video_date(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01", latest="2026-08-20")
    seen: list = []
    _check(monkeypatch, capsys, profiles, seen=seen)
    assert len(seen) == 1
    body = seen[0]
    assert body["size"] == 0 and body["track_total_hits"] is True
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"channel.id": 42}} in filters
    assert {"range": {"publication_date": {"gt": "2026-08-20"}}} in filters


def test_too_many_new_uploads_refreshes(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01")
    _, out = _check(monkeypatch, capsys, profiles, total=6, today=dt.date(2026, 9, 2))
    assert out["decision"] == "refresh" and "6 new uploads" in out["reason"]
    assert out["next_round"] == 2


def test_an_old_ledger_refreshes_even_with_few_uploads(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-06-01")
    _, out = _check(monkeypatch, capsys, profiles, total=2, today=dt.date(2026, 9, 2))
    assert out["decision"] == "refresh" and "days old" in out["reason"]


def test_thresholds_are_flags(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-06-01")
    _, out = _check(monkeypatch, capsys, profiles, "--max-new-videos", "10",
                    "--max-age-days", "365", total=8, today=dt.date(2026, 9, 2))
    assert out["decision"] == "reuse"


def test_rebuild_and_no_refresh_override_the_rule(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-01-01")
    _, out = _check(monkeypatch, capsys, profiles, "--no-refresh", total=40,
                    today=dt.date(2026, 9, 2))
    assert out["decision"] == "reuse" and out["reason"] == "--no-refresh"
    line, out = _check(monkeypatch, capsys, profiles, "--rebuild", total=0,
                       today=dt.date(2026, 9, 2))
    assert out["decision"] == "build" and out["reason"] == "--rebuild"
    assert line.startswith("Found a ledger")       # still announced, then rebuilt


def test_a_transcripts_only_ledger_refreshes_when_socials_are_asked_for(
        tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01")
    _, out = _check(monkeypatch, capsys, profiles, "--lanes", "transcripts+socials",
                    total=0, today=dt.date(2026, 9, 2))
    assert out["decision"] == "refresh" and "socials lane requested" in out["reason"]
    assert out["lanes"] == "transcripts"
    # the reverse reuses: a ledger that also read socials covers a
    # transcripts-only request
    path = profiles / "42-facts.jsonl"
    meta, facts = store_io.read_ledger(path)
    meta["lanes"] = "transcripts+socials"
    store_io.write_ledger(path, meta, facts)
    _, out = _check(monkeypatch, capsys, profiles, "--lanes", "transcripts",
                    total=0, today=dt.date(2026, 9, 2))
    assert out["decision"] == "reuse"
    _, out = _check(monkeypatch, capsys, profiles, "--lanes", "transcripts+socials",
                    total=0, today=dt.date(2026, 9, 2))
    assert out["decision"] == "reuse"


def test_a_failed_count_refreshes_rather_than_reusing_blind(tmp_path, monkeypatch, capsys):
    profiles = _ledger(tmp_path, "2026-09-01")

    def boom(args, input_text=None, **kw):
        raise RuntimeError("index unavailable")
    monkeypatch.setattr(ledger_meta.tl_data, "_tl_json", boom)
    rc = ledger_meta.main(["check", "--channel", "42", "--profiles-dir", str(profiles)])
    assert rc == 0
    captured = capsys.readouterr()
    line, payload = captured.out.strip().splitlines()
    assert "uploads since: unknown" in line
    out = json.loads(payload)
    assert out["decision"] == "refresh" and out["new_videos"] is None
    assert "index unavailable" in captured.err
