"""fetch_cues.py — the model layer's only retrieval flow.

Covers everything the script decides on its own: what a highlight fragment
means (`clean`, `format_hint`), how it censuses a channel's transcript
coverage and pulls non-English uploads separately (`census`,
`fetch_non_english`, `sample_windows`), which passages survive the cap
(per-video, per-phrase and recurring-bit ceilings, plus `--exclude` from an
earlier round), the additive `--round` behaviour (suffixed batches/returns,
merged corpus, stale-return cleanup), and the ad-read lookup (real spans vs
the regex fallback). No network anywhere: every ES call goes through
`tl_data.cli_rows` / `tl_data._tl_json`, both stubbed, and the sponsor-span
lookup is stubbed too.
"""

import gzip
import json
import pathlib
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import fetch_cues  # noqa: E402


# --------------------------------------------------------------------------- #
# clean(): a highlight fragment -> (text, hits, first start, raw hits, pieces)
# --------------------------------------------------------------------------- #
def test_clean_strips_timing_tags_and_keeps_the_first_start():
    frag = ('<text start="41.5">so anyway</text>'
            '<text start="45.25">i grew up in a tiny town</text>')
    text, hits, start, raw, pieces = fetch_cues.clean(frag)
    assert text == "so anyway i grew up in a tiny town"
    assert start == 41.5            # the passage opens at its earliest cue
    assert hits == [] and raw == []
    assert pieces == [[41.5, "so anyway"], [45.25, "i grew up in a tiny town"]]


def test_clean_double_unescapes_caption_entities():
    # captions arrive double-escaped often enough that one pass is not enough
    frag = '<text start="10">my &amp;#39;dad&amp;#39; ran a bakery</text>'
    text, _, _, _, pieces = fetch_cues.clean(frag)
    assert text == "my 'dad' ran a bakery"
    assert pieces == [[10.0, "my 'dad' ran a bakery"]]


@pytest.mark.parametrize("stub, fixed", [
    ("amp;#39;s right there behind", "'s right there behind"),
    (";#39;m big into health", "'m big into health"),
    ("#39;ve got a dog", "'ve got a dog"),
    ("amp;#34; quoted", "quoted"),
])
def test_clean_resolves_a_partial_entity_at_a_fragment_start(stub, fixed):
    frag = f'<text start="10">{stub} and <em>my dad</em> ran the bakery there</text>'
    text, hits, start, raw, pieces = fetch_cues.clean(frag)
    assert text.startswith(fixed)
    assert pieces[0][1] == text          # the cue store and the text agree
    assert "my dad" in hits


@pytest.mark.parametrize("stub", [
    'start="138" dur="3.78">',
    '="664.399" dur="2.801">',
    '> ',
    'start="1.2" dur="2">amp;#39;',
])
def test_clean_drops_a_cut_timed_text_tag_at_a_fragment_start(stub):
    frag = f'<text start="10">{stub}hey friends welcome back and <em>my dad</em> ran it</text>'
    text, hits, start, raw, pieces = fetch_cues.clean(frag)
    assert text.startswith("'hey friends" if "#39" in stub else "hey friends"), text
    assert pieces[0][1] == text
    assert start == 10.0


def test_clean_reports_em_hits_lowercased_and_deduped():
    frag = ('<text start="12"><em>I grew up</em> in ohio and '
            '<em>i grew up</em> poor, <em>my dad</em> worked nights</text>')
    text, hits, start, raw, pieces = fetch_cues.clean(frag)
    assert hits == ["i grew up", "my dad"]          # sorted, unique, lowered
    assert raw == ["i grew up", "i grew up", "my dad"]   # raw keeps repeats
    assert "<em>" not in text and start == 12
    assert pieces == [[12.0, text]]                 # one cue -> one piece


def test_clean_returns_no_start_and_no_pieces_when_untimed():
    text, hits, start, raw, pieces = fetch_cues.clean(
        "i grew up in a tiny town in ohio")
    assert start is None
    assert pieces == []
    assert text == ""


def test_clean_attaches_text_before_the_first_tag_to_the_first_cue():
    # a fragment boundary can cut a cue's opening tag off; the leading text
    # belongs to the first real cue, not a phantom one
    frag = 'hello world<text start="10">my dad ran</text>'
    text, _, start, _, pieces = fetch_cues.clean(frag)
    assert start == 10.0
    assert pieces == [[10.0, "hello world my dad ran"]]
    assert text == "hello world my dad ran"


def test_clean_returns_one_piece_per_timed_text_cue():
    frag = ('<text start="1.0">first cue</text>'
            '<text start="2.0">second cue</text>'
            '<text start="3.0">third cue</text>')
    _, _, start, _, pieces = fetch_cues.clean(frag)
    assert start == 1.0
    assert [p[0] for p in pieces] == [1.0, 2.0, 3.0]
    assert [p[1] for p in pieces] == ["first cue", "second cue", "third cue"]


# --------------------------------------------------------------------------- #
# format_hint(): title -> per-video rubric hint
# --------------------------------------------------------------------------- #
def test_format_hint_detects_reaction_titles():
    assert fetch_cues.format_hint("I REACT to old vlogs") == "reaction"
    assert fetch_cues.format_hint("Reacting to fan comments") == "reaction"
    assert fetch_cues.format_hint("Watching my old content") == "reaction"


def test_format_hint_detects_interview_or_collab_titles():
    assert fetch_cues.format_hint("Interview with a chef") == "interview_or_collab"
    assert fetch_cues.format_hint("Cooking ft. John Doe") == "interview_or_collab"
    assert fetch_cues.format_hint("A collab video") == "interview_or_collab"
    assert fetch_cues.format_hint("Special guest: my mum") == "interview_or_collab"
    assert fetch_cues.format_hint("Q&A with my editor") == "interview_or_collab"


def test_format_hint_detects_with_name_and_w_slash_collab_titles():
    # Live miss (ChippyGaming, 2026-09-03): four-way call titles whose
    # windows were judged as the solo host, because "w/" never matched and
    # "with <Name>" was not a pattern at all.
    for title in (
        "Terraria w/ JaidenAnimations, AntiDarkHeart & AliceSnowpuff",
        "Terraria with JaidenAnimations, AntiDarkHeart and AliceSnowpuff",
        "Teeny Tiny Terraria Bosses! #2 w/ Jaiden Animations & Ant",
        "I Played Terraria 1.4.5 with The Developers",
        "I Played 1.4.5 with Terraria’s Creator",
        "Terraria Livestream with PythonGB and Pedguin!",
        "Cooking with @chefjane",
    ):
        assert fetch_cues.format_hint(title) == "interview_or_collab", title


def test_format_hint_returns_none_for_plain_titles():
    assert fetch_cues.format_hint("My daily vlog") is None
    assert fetch_cues.format_hint(None) is None
    # lower-case "with <thing>" is a mod, a pack or a tool, not a person
    assert fetch_cues.format_hint("You NEED to replay Terraria with this mod!") is None
    assert fetch_cues.format_hint("Ocram is FINALLY playable on PC Terraria! (with mods)") is None


def test_format_hint_is_the_same_rule_channel_context_counts():
    # One home: the per-window hint and the context brief's title census must
    # never disagree on what counts as a second voice.
    import channel_context
    titles = ["Terraria with JaidenAnimations", "I REACT to old vlogs",
              "My daily vlog", "Cooking ft. John Doe"]
    for title in titles:
        expected = next((fmt for fmt, rx in channel_context.TITLE_SECOND_VOICE.items()
                         if rx.search(title)), None)
        assert fetch_cues.format_hint(title) == expected, title


# --------------------------------------------------------------------------- #
# YEARS: derived, not hard-coded
# --------------------------------------------------------------------------- #
def test_years_span_youtube_launch_to_next_year():
    assert fetch_cues.YEARS[0] == 2005
    assert fetch_cues.YEARS[-1] == time.gmtime().tm_year + 1
    assert fetch_cues.YEARS == list(range(2005, time.gmtime().tm_year + 2))


# --------------------------------------------------------------------------- #
# census(): size-0 aggregation over tl_data._tl_json
# --------------------------------------------------------------------------- #
def test_census_queries_a_size_zero_language_aggregation(monkeypatch):
    captured = {}

    def fake(args, input_text=None, timeout=None):
        captured["body"] = json.loads(input_text)
        return {"total": 42, "aggregations": {"lang": {"buckets": [
            {"key": "en", "doc_count": 30}, {"key": "es", "doc_count": 12}]}}}

    monkeypatch.setattr(fetch_cues.tl_data, "_tl_json", fake)
    total, langs = fetch_cues.census(99)
    assert total == 42
    assert langs == {"en": 30, "es": 12}
    body = captured["body"]
    assert body["size"] == 0
    assert body["track_total_hits"] is True
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"channel.id": 99}} in filters
    assert {"term": {"doc_type": "article"}} in filters
    assert {"exists": {"field": "transcript"}} in filters
    assert body["aggs"]["lang"]["terms"]["field"] == "transcript_language"


def test_census_handles_no_hits(monkeypatch):
    monkeypatch.setattr(fetch_cues.tl_data, "_tl_json", lambda *a, **k: None)
    total, langs = fetch_cues.census(1)
    assert total == 0 and langs == {}


# --------------------------------------------------------------------------- #
# is_english(): which caption-language codes count as English
# --------------------------------------------------------------------------- #
def test_is_english_accepts_known_and_regional_codes():
    assert fetch_cues.is_english("en")
    assert fetch_cues.is_english("EN")
    assert fetch_cues.is_english("asr-en")
    assert fetch_cues.is_english("en-US")
    assert fetch_cues.is_english("en-GB")
    assert fetch_cues.is_english(None)          # missing -> treated as English
    assert fetch_cues.is_english("")


def test_is_english_rejects_other_languages():
    assert not fetch_cues.is_english("es")
    assert not fetch_cues.is_english("fr")
    assert not fetch_cues.is_english("pt-BR")


# --------------------------------------------------------------------------- #
# fetch_non_english(): pages non-English transcript docs over tl_data.cli_rows
# --------------------------------------------------------------------------- #
def test_fetch_non_english_pages_and_excludes_english(monkeypatch):
    calls = []
    page1 = [{"id": f"7:v{i}", "publication_date": "2024-01-01",
             "transcript_language": "es"} for i in range(3)]
    page2 = [{"id": "7:v3", "publication_date": "2023-01-01",
             "transcript_language": "es"}]

    def fake(args, input_text=None, timeout=None):
        body = json.loads(input_text)
        calls.append(body)
        return page1 if len(calls) == 1 else page2

    monkeypatch.setattr(fetch_cues.tl_data, "cli_rows", fake)
    docs = fetch_cues.fetch_non_english(7, size=3)
    assert docs == page1 + page2
    first = calls[0]
    assert first["query"]["bool"]["must_not"] == [
        {"terms": {"transcript_language": sorted(fetch_cues.ENGLISH_CODES)}}]
    assert {"term": {"channel.id": 7}} in first["query"]["bool"]["filter"]
    assert {"exists": {"field": "transcript"}} in first["query"]["bool"]["filter"]
    assert "search_after" not in first
    assert calls[1]["search_after"] == ["2024-01-01", "7:v2"]


def test_fetch_non_english_stops_on_a_short_page(monkeypatch):
    monkeypatch.setattr(fetch_cues.tl_data, "cli_rows",
                        lambda *a, **k: [{"id": "7:v0",
                                         "publication_date": "2024-01-01",
                                         "transcript_language": "fr"}])
    docs = fetch_cues.fetch_non_english(7, size=40)
    assert len(docs) == 1


# --------------------------------------------------------------------------- #
# sample_windows(): stride-sampled ~80-word runs of consecutive cues
# --------------------------------------------------------------------------- #
def test_sample_windows_groups_by_word_count_then_stride_samples():
    cue_list = [(float(i), " ".join(["word"] * 10)) for i in range(10)]
    runs = fetch_cues.sample_windows(cue_list, per_video=3, words=30)
    # 10 cues of 10 words each form 3 full 30-word runs plus one partial run
    # of 10 words; stride sampling over per_video=3 keeps the first three
    assert len(runs) == 3
    assert [len(r) for r in runs] == [3, 3, 3]
    assert [r[0][0] for r in runs] == [0.0, 3.0, 6.0]


def test_sample_windows_returns_everything_when_under_the_cap():
    cue_list = [(float(i), " ".join(["word"] * 40)) for i in range(2)]
    runs = fetch_cues.sample_windows(cue_list, per_video=3, words=30)
    assert len(runs) == 2
    assert runs[0] == [[0.0, cue_list[0][1]]]


def test_sample_windows_drops_a_too_short_trailing_run():
    cue_list = [(0.0, " ".join(["word"] * 40)), (1.0, "just a few words")]
    runs = fetch_cues.sample_windows(cue_list, per_video=3, words=30)
    assert len(runs) == 1                   # the 4-word trailing run is dropped


# --------------------------------------------------------------------------- #
# main(): the cap, and everything a run leaves behind
# --------------------------------------------------------------------------- #
def _frag(cue: str, start: int, filler: str = "and that is the whole story") -> str:
    return (f'<text start="{start}"><em>{cue}</em> {filler} '
            f'about the year it happened</text>')


def _doc(vid: str, frags: list[str], date: str = "2024-03-02") -> dict:
    return {"id": vid, "title": f"video {vid}", "publication_date": date,
            "transcript_language": "en", "content_type": "video",
            "duration": 600, "highlight": {"transcript": frags}}


def _cli_rows_router(docs_by_year: dict, non_en_docs: list[dict]):
    """A ``tl_data.cli_rows`` stub that answers both callers that use it:
    the year-bucket cue query (``fetch_year``, no ``must_not``) and the
    non-English fetch (``fetch_non_english``, a ``must_not`` clause on
    ``transcript_language: en``) — distinguished by inspecting the body,
    exactly the way the two queries differ for real."""
    def fake(args, input_text=None, timeout=None):
        body = json.loads(input_text)
        bool_q = body["query"]["bool"]
        if bool_q.get("must_not"):
            return [] if "search_after" in body else list(non_en_docs)
        year = None
        for f in bool_q.get("filter", []):
            rng = (f.get("range") or {}).get("publication_date")
            if rng:
                year = int(rng["gte"][:4])
        if "search_after" in body:
            return []
        return list(docs_by_year.get(year, []))
    return fake


def _run(tmp_path, monkeypatch, docs, *, argv=(), phrases=None, spans=None,
        year=2024, langs=None, non_en_docs=(), census_total=None):
    """Run main() over stubbed ES calls, returning (summary, kept windows)."""
    monkeypatch.setattr(fetch_cues.tl_data, "cli_rows",
                        _cli_rows_router({year: docs}, list(non_en_docs)))
    lang_counts = langs if langs is not None else {"en": len(docs) or 1}
    total = census_total if census_total is not None else sum(lang_counts.values())
    monkeypatch.setattr(
        fetch_cues.tl_data, "_tl_json",
        lambda args, input_text=None, timeout=None: {
            "total": total,
            "aggregations": {"lang": {"buckets": [
                {"key": k, "doc_count": v} for k, v in lang_counts.items()]}}})
    monkeypatch.setattr(fetch_cues, "sponsor_segments",
                        spans or (lambda refs: {}))
    phrase_file = tmp_path / "phrases.txt"
    phrase_file.write_text(phrases if phrases is not None
                           else "i grew up\nmy dad\nmy first job\n")
    out = tmp_path / "corpus"
    monkeypatch.setattr(sys, "argv", ["fetch_cues.py", "--channel", "7",
                                      "--out", str(out), "--phrases",
                                      str(phrase_file), *map(str, argv)])
    capture = {}
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: capture.setdefault("lines", []).append((a, k)))
    assert fetch_cues.main() == 0
    monkeypatch.undo()
    summary = json.loads([a[0] for a, k in capture["lines"]
                          if not k.get("file")][0])
    round_n = 1
    argv_list = list(argv)
    if "--round" in argv_list:
        round_n = int(argv_list[argv_list.index("--round") + 1])
    suffix = "" if round_n <= 1 else f"-r{round_n}"
    kept = []
    for p in sorted((out / "7" / f"batches{suffix}").glob("batch-*.json")):
        kept.extend(json.loads(p.read_text()))
    return summary, kept


def test_per_video_cap_stops_one_video_owning_the_batch(tmp_path, monkeypatch):
    frags = [_frag("i grew up", 100 + 60 * i) for i in range(12)]
    summary, kept = _run(tmp_path, monkeypatch, [_doc("7:vid1", frags)],
                         argv=("--per-video-cap", "3"))
    assert summary["passages"] == 12          # every passage is still recorded
    assert len(kept) == 3                     # only three reach the model layer
    assert summary["videos_in_batches"] == 1


def test_one_phrase_cannot_supply_more_than_its_share_of_the_cap(
        tmp_path, monkeypatch):
    # 30 videos, one passage each, all firing the same cue: the per-phrase
    # ceiling (max(12, 8% of the cap)) bounds what that one phrase contributes
    docs = [_doc(f"7:v{i:02d}", [_frag("i grew up", 100)]) for i in range(30)]
    _, kept = _run(tmp_path, monkeypatch, docs, argv=("--max-windows", "100"))
    assert len(kept) == fetch_cues.RECURRING_CAP == 12


def test_a_recurring_bit_is_capped_harder_than_an_ordinary_cue(
        tmp_path, monkeypatch):
    """`~phrase` marks a greeting or sign-off that fires in most uploads; it
    may seed a few windows, never fill the batch."""
    docs = [_doc(f"7:v{i:02d}", [_frag("my first job", 100)]) for i in range(30)]
    _, ordinary = _run(tmp_path, monkeypatch, docs,
                       argv=("--max-windows", "500"))
    docs = [_doc(f"7:v{i:02d}", [_frag("welcome back to", 100)])
            for i in range(30)]
    _, recurring = _run(tmp_path, monkeypatch, docs,
                        argv=("--max-windows", "500"),
                        phrases="i grew up\nmy first job\n~welcome back to\n")
    # the ordinary cue rides the 8%-of-cap ceiling (40); the recurring bit is
    # bounded by RECURRING_CAP, which it reaches twice as fast because a
    # recurring-only window charges its phrase on both passes
    assert len(ordinary) == 30
    assert len(recurring) == fetch_cues.RECURRING_CAP // 2 == 6


def test_exclude_skips_passages_an_earlier_round_already_judged(
        tmp_path, monkeypatch):
    frags = [_frag("i grew up", 100), _frag("my dad", 400)]
    done = tmp_path / "classified.jsonl"
    done.write_text(json.dumps({"window": {"id": "7:vid1", "start": 110},
                                "verdict": {}}) + "\n")
    _, kept = _run(tmp_path, monkeypatch, [_doc("7:vid1", frags)],
                   argv=("--exclude", str(done)))
    # 110 is within 30 s of the first passage's start, so only the second one
    # is new work for this round
    assert [w["start"] for w in kept] == [400]


def test_short_passages_and_untimed_fragments_never_batch(tmp_path, monkeypatch):
    frags = ['<text start="10"><em>i grew up</em> here</text>',   # < 8 words
             '<em>my dad</em> ran the bakery for thirty five long years']  # no start
    summary, kept = _run(tmp_path, monkeypatch, [_doc("7:vid1", frags)])
    assert kept == [] and summary["passages"] == 0


def test_windows_carry_a_format_hint_from_the_title(tmp_path, monkeypatch):
    doc = _doc("7:vid1", [_frag("i grew up", 100)])
    doc["title"] = "I React to my old videos"
    _, kept = _run(tmp_path, monkeypatch, [doc])
    assert kept[0]["format_hint"] == "reaction"


# --------------------------------------------------------------------------- #
# the ad-read flag: real spans when the lookup works, the regex when it doesn't
# --------------------------------------------------------------------------- #
_AD = ('<text start="300"><em>i grew up</em> broke, and today\'s video is '
       'sponsored by acme so use code me for 20% off</text>')


def test_real_sponsor_spans_decide_the_flag_and_are_recorded(
        tmp_path, monkeypatch):
    summary, kept = _run(tmp_path, monkeypatch,
                         [_doc("7:vid1", [_AD, _frag("my dad", 900)])],
                         spans=lambda refs: {"7:vid1": [(1000.0, 1100.0)]})
    assert summary["sponsor_source"] == "brand_mentions"
    flags = {w["start"]: w["in_sponsor_read"] for w in kept}
    # the index says the read is at 1000-1100: the 900 s passage overlaps it
    # once padded, the 300 s one does not — whatever the regex thought
    assert flags == {300: False, 900: True}
    assert summary["sponsor_flagged"] == 1


def test_a_failed_span_lookup_falls_back_to_the_regex_and_says_so(
        tmp_path, monkeypatch):
    def boom(refs):
        raise RuntimeError("es unavailable")
    summary, kept = _run(tmp_path, monkeypatch,
                         [_doc("7:vid1", [_AD, _frag("my dad", 900)])],
                         spans=boom)
    assert summary["sponsor_source"] == "regex_fallback"
    flags = {w["start"]: w["in_sponsor_read"] for w in kept}
    assert flags == {300: True, 900: False}


# --------------------------------------------------------------------------- #
# non-English coverage: sampled in only when the census shows other languages
# --------------------------------------------------------------------------- #
def test_non_english_videos_are_sampled_in_when_census_flags_them(
        tmp_path, monkeypatch):
    cues_xml = "".join(f'<text start="{i * 2}.0">palabra numero {i}</text>'
                       for i in range(50))
    non_en_doc = {"id": "7:vidES", "title": "Un video", "publication_date": "2023-05-01",
                 "transcript_language": "es", "content_type": "video",
                 "duration": 400, "transcript": cues_xml}
    summary, kept = _run(tmp_path, monkeypatch, [], argv=("--max-windows", "500"),
                         langs={"en": 5, "es": 12}, non_en_docs=[non_en_doc])
    assert summary["languages"] == {"en": 5, "es": 12}
    assert summary["non_english_videos_sampled"] == 1
    es_windows = [w for w in kept if w["id"] == "7:vidES"]
    assert es_windows
    assert all(w["language"] == "es" for w in es_windows)
    assert all(w["rank_score"] == 1.0 for w in es_windows)
    assert all(w["cues_fired"] == [] for w in es_windows)
    assert len(es_windows) <= fetch_cues.NON_EN_WINDOWS_PER_VIDEO


def test_non_english_fetch_is_skipped_when_census_shows_only_english(
        tmp_path, monkeypatch):
    summary, _ = _run(tmp_path, monkeypatch,
                      [_doc("7:vid1", [_frag("i grew up", 100)])],
                      langs={"en": 10})
    assert summary["non_english_videos_sampled"] == 0


# --------------------------------------------------------------------------- #
# summary shape and the files a run leaves behind
# --------------------------------------------------------------------------- #
def test_run_writes_batches_windows_and_a_reusable_corpus(tmp_path, monkeypatch):
    docs = [_doc(f"7:v{i}", [_frag("i grew up", 100), _frag("my dad", 400)])
            for i in range(3)]
    summary, kept = _run(tmp_path, monkeypatch, docs, argv=("--batch-size", "2"),
                         census_total=3, langs={"en": 3})
    assert len(kept) == 6 and len(summary["batches"]) == 3
    with gzip.open(summary["windows_file"], "rt", encoding="utf-8") as f:
        assert len([1 for _ in f]) == 6
    with gzip.open(summary["corpus"], "rt", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 3
    # corpus rows are the store shape verify_quotes.py reads
    assert {"id", "title", "publication_date", "cues", "transcript_language"} <= set(rows[0])
    assert rows[0]["transcript_language"] == "en"
    assert [c[0] for c in rows[0]["cues"]] == [100.0, 400.0]   # cues are timed


def test_summary_reports_round_census_and_returns_dir(tmp_path, monkeypatch):
    summary, _ = _run(tmp_path, monkeypatch,
                      [_doc("7:vid1", [_frag("i grew up", 100)])],
                      census_total=77, langs={"en": 77})
    assert summary["round"] == 1
    assert summary["videos_with_transcript"] == 77
    assert summary["languages"] == {"en": 77}
    assert summary["non_english_videos_sampled"] == 0
    assert summary["returns_dir"].endswith("returns")


def test_run_persists_its_summary_for_the_meta_record(tmp_path, monkeypatch):
    summary, _ = _run(tmp_path, monkeypatch,
                      [_doc("7:vid1", [_frag("i grew up", 100)])],
                      census_total=5, langs={"en": 5})
    path = pathlib.Path(summary["summary_file"])
    assert path.name == "fetch.json" and path.parent.name == "7"
    on_disk = json.loads(path.read_text())
    assert on_disk["videos_with_transcript"] == 5
    # the census stub answers every count call, so no newest-upload row came back
    assert on_disk["latest_video_date"] is None
    summary2, _ = _run(tmp_path, monkeypatch,
                       [_doc("7:vid1", [_frag("i grew up", 100)])],
                       argv=("--round", "2"), census_total=5, langs={"en": 5})
    assert pathlib.Path(summary2["summary_file"]).name == "fetch-r2.json"
    assert path.exists()                       # round 1's summary survives round 2


def test_a_round_1_fetch_clears_an_earlier_builds_round_artifacts(tmp_path, monkeypatch):
    out = tmp_path / "corpus" / "7"
    out.mkdir(parents=True)
    (out / "fetch-r2.json").write_text("{}")
    (out / "windows-r2.jsonl.gz").write_bytes(b"")
    (out / "classified.jsonl").write_text("{}\n")
    (out / "batches-r2").mkdir()
    (out / "batches-r2" / "batch-000.json").write_text("[]")
    with gzip.open(out / "corpus.jsonl.gz", "wt") as f:
        f.write(json.dumps({"id": "7:old", "publication_date": "2001-01-01", "cues": []}) + "\n")
    summary, _ = _run(tmp_path, monkeypatch, [_doc("7:vid1", [_frag("i grew up", 100)])],
                      census_total=1, langs={"en": 1})
    assert not (out / "fetch-r2.json").exists()
    assert not (out / "windows-r2.jsonl.gz").exists()
    assert not (out / "classified.jsonl").exists()
    assert not (out / "batches-r2").exists()
    with gzip.open(summary["corpus"], "rt") as f:
        ids = [json.loads(line)["id"] for line in f]
    assert ids == ["7:vid1"]                   # the old build's passages are gone too


def test_latest_upload_is_one_sorted_single_hit_query(monkeypatch):
    seen = []

    def fake(args, input_text=None, timeout=None):
        seen.append(json.loads(input_text))
        return {"results": [{"id": "7:new", "publication_date": "2026-08-29T00:00:00"}],
                "total": 867}
    monkeypatch.setattr(fetch_cues.tl_data, "_tl_json", fake)
    assert fetch_cues.latest_upload(7) == "2026-08-29"
    body = seen[0]
    assert body["size"] == 1 and body["sort"] == [{"publication_date": "desc"}]
    assert {"term": {"channel.id": 7}} in body["query"]["bool"]["filter"]
    monkeypatch.setattr(fetch_cues.tl_data, "_tl_json", lambda *a, **k: {"results": []})
    assert fetch_cues.latest_upload(7) is None


def test_phrase_file_marks_recurring_bits_with_a_tilde(tmp_path):
    path = tmp_path / "p.txt"
    path.write_text("# a comment\ni grew up\n~my name is\n\n")
    phrases, recurring = fetch_cues.load_phrases(path)
    assert "my name is" in phrases and "my name is" in recurring
    assert "i grew up" in phrases and "i grew up" not in recurring
    assert "#" not in "".join(phrases)


@pytest.mark.parametrize("weak,strong", [("i love", "i grew up")])
def test_a_weak_cue_scores_below_a_specific_one(weak, strong, tmp_path,
                                                monkeypatch):
    docs = [_doc("7:v1", [_frag(weak, 100)]), _doc("7:v2", [_frag(strong, 100)])]
    _, kept = _run(tmp_path, monkeypatch, docs,
                   phrases=f"{weak}\n{strong}\n")
    assert [w["id"] for w in kept] == ["7:v2", "7:v1"]   # ranked, not fetched-order


# --------------------------------------------------------------------------- #
# --round N: additive artifacts, corpus merge, stale-return cleanup
# --------------------------------------------------------------------------- #
def test_round_2_merges_the_corpus_and_uses_suffixed_batches_and_returns(
        tmp_path, monkeypatch):
    out = tmp_path / "corpus"
    # round 1: one cue at 100s
    docs1 = [_doc("7:vid1", [_frag("i grew up", 100)])]
    summary1, kept1 = _run(tmp_path, monkeypatch, docs1)
    assert (out / "7" / "batches").exists()
    assert (out / "7" / "windows.jsonl.gz").exists()

    # stage a stale return for round 2's batch layout; it must be cleared
    rdir2 = out / "7" / "returns-r2"
    rdir2.mkdir(parents=True, exist_ok=True)
    stale = rdir2 / "batch-000.extract.json"
    stale.write_text("stale")

    # round 2: a different cue on the same video, at a new start
    docs2 = [_doc("7:vid1", [_frag("my dad", 500)])]
    summary2, kept2 = _run(tmp_path, monkeypatch, docs2, argv=("--round", "2"))

    assert summary2["round"] == 2
    assert (out / "7" / "batches-r2").exists()
    assert (out / "7" / "windows-r2.jsonl.gz").exists()
    assert not stale.exists()          # stale return for the old layout is gone
    # round 1's artifacts are untouched
    assert (out / "7" / "windows.jsonl.gz").exists()

    with gzip.open(summary2["corpus"], "rt", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 1
    starts = sorted(c[0] for c in rows[0]["cues"])
    assert starts == [100.0, 500.0]    # round 1's cue survives, round 2 merges in


def test_date_range_is_the_year_bucket_unless_since_cuts_into_it():
    assert fetch_cues.date_range(2024, None) == {
        "range": {"publication_date": {"gte": "2024-01-01", "lt": "2025-01-01"}}}
    # a since date before the bucket leaves the bucket whole
    assert fetch_cues.date_range(2024, "2023-06-01") == {
        "range": {"publication_date": {"gte": "2024-01-01", "lt": "2025-01-01"}}}
    # inside the bucket: strictly after since, so the ledger's newest video is not refetched
    assert fetch_cues.date_range(2024, "2024-05-01") == {
        "range": {"publication_date": {"gt": "2024-05-01", "lt": "2025-01-01"}}}


def test_query_body_carries_since_into_the_filter():
    body = fetch_cues.query_body(42, ["i grew up"], 2026, 10, 900, 10, None, since="2026-05-01")
    assert {"range": {"publication_date": {"gt": "2026-05-01", "lt": "2027-01-01"}}} in \
        body["query"]["bool"]["filter"]
    body = fetch_cues.query_body(42, ["i grew up"], 2026, 10, 900, 10, None)
    assert {"range": {"publication_date": {"gte": "2026-01-01", "lt": "2027-01-01"}}} in \
        body["query"]["bool"]["filter"]


# --------------------------------------------------------------------------- #
# batch size follows the host's concurrent-agent cap
# --------------------------------------------------------------------------- #
def test_derived_batch_size_spreads_the_windows_over_the_agent_cap():
    assert fetch_cues.derived_batch_size(500, 20) == 25
    assert fetch_cues.derived_batch_size(500, 40) == 13        # 39 batches, one wave of 40
    assert fetch_cues.derived_batch_size(300, 40) == 8         # from the windows kept, not the cap
    assert fetch_cues.derived_batch_size(30, 40) == fetch_cues.MIN_BATCH_SIZE
    assert fetch_cues.derived_batch_size(0, 40) == fetch_cues.MIN_BATCH_SIZE


def test_agent_cap_comes_from_the_environment_or_defaults_to_20(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", raising=False)
    assert fetch_cues.env_agent_cap() == fetch_cues.DEFAULT_AGENT_CAP == 20
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "40")
    assert fetch_cues.env_agent_cap() == 40
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "lots")
    assert fetch_cues.env_agent_cap() == 20
    assert "not an integer" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "0")
    assert fetch_cues.env_agent_cap() == 20


def test_run_derives_the_batch_size_from_the_cap_unless_the_flag_is_given(tmp_path, monkeypatch):
    docs = [_doc(f"7:v{i}", [_frag("i grew up", 100), _frag("my dad", 400)])
            for i in range(6)]                                   # 12 windows
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "2")
    summary, kept = _run(tmp_path, monkeypatch, docs, census_total=6, langs={"en": 6})
    assert len(kept) == 12 and summary["batch_size"] == 6 and summary["agent_cap"] == 2
    assert len(summary["batches"]) == 2
    summary, _ = _run(tmp_path, monkeypatch, docs, argv=("--batch-size", "5"),
                      census_total=6, langs={"en": 6})
    assert summary["batch_size"] == 5 and len(summary["batches"]) == 3


def test_reserve_leaves_room_for_lanes_running_beside_the_fan_out():
    """A lane in flight during the fan-out costs one extractor slot, so the
    batches are sized against what is actually free. 20 batches for a cap of
    20 with the socials lane running meant the 20th extractor was rejected and
    relaunched a wave later."""
    cap = 20
    assert fetch_cues.derived_batch_size(500, cap) == 25            # 20 batches
    assert fetch_cues.derived_batch_size(500, cap - 1) == 27        # 19 batches
    assert -(-500 // fetch_cues.derived_batch_size(500, cap - 1)) == 19
    # reserving more than the cap can never produce zero or negative batches
    assert fetch_cues.derived_batch_size(500, max(1, cap - 99)) >= 1
