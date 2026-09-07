"""The creator-brief scripts: local quote verification and the deterministic
connections page. The retrieval and assembly stages have their own files,
``test_fetch_cues.py`` and ``test_assemble_extracts.py``. No real network.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import store_io  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


# --------------------------------------------------------------------------- #
# verify_quotes.py
# --------------------------------------------------------------------------- #
def _run_verify(tmp_path: Path, candidates: list[dict]) -> tuple:
    corpus = _write_jsonl(tmp_path / "corpus.jsonl", [
        {"id": "1:vid1", "cues": [
            [10, "so before we start"],
            [14, "I grew up in a tiny town in Ohio"],
            [19, "and my dad ran the bakery there"]]},
        {"id": "1:vid2", "cues": []},
    ])
    infile = _write_jsonl(tmp_path / "candidates.jsonl", candidates)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "verify_quotes.py"),
         "--in", str(infile), "--corpus", str(corpus)],
        capture_output=True, text=True)
    out = [json.loads(line) for line in
           (tmp_path / "candidates.jsonl.verified.jsonl")
           .read_text().splitlines()]
    return proc, json.loads(proc.stdout), out


def test_exact_match_publishes_and_owns_the_timestamp(tmp_path):
    proc, summary, rows = _run_verify(tmp_path, [
        {"provenance": "transcript", "video": "1:vid1", "start": 999,
         "quote": "I grew up in a tiny town in Ohio and my dad ran"}])
    assert proc.returncode == 0
    assert summary["exact"] == 1
    v = rows[0]["verify"]
    assert v["match"] == "exact" and v["found"] is True
    # the located timestamp overrides whatever the candidate carried
    assert rows[0]["start"] == 14
    assert rows[0]["url"].endswith("v=vid1&t=14s")


def test_partial_match_never_accepts(tmp_path):
    proc, summary, rows = _run_verify(tmp_path, [
        {"provenance": "transcript", "video": "1:vid1",
         "quote": "I grew up in a tiny town in Texas with my mother"}])
    assert proc.returncode == 1
    assert summary["partial"] == 1
    v = rows[0]["verify"]
    assert v["match"] == "partial" and v["found"] is False
    assert "unmatched_tail" in v and "warning" in v
    assert rows[0].get("start") is None  # nothing promoted


def test_missing_video_and_no_transcript_are_flagged_not_matched(tmp_path):
    proc, summary, rows = _run_verify(tmp_path, [
        {"provenance": "transcript", "video": "1:vid2", "quote": "anything"},
        {"provenance": "transcript", "video": "1:nope", "quote": "anything"}])
    assert proc.returncode == 1
    assert summary["none"] == 2
    assert all(r["verify"]["found"] is False and "error" in r["verify"]
               for r in rows)


def test_social_and_web_facts_pass_through_unverified(tmp_path):
    proc, summary, rows = _run_verify(tmp_path, [
        {"provenance": "social", "claim": "has a dog",
         "source_url": "https://example.com/p"}])
    assert proc.returncode == 0
    assert summary["passed_through_non_transcript"] == 1
    assert rows[0]["verify"]["match"] == "n/a"


def test_a_meta_header_is_not_a_candidate_and_survives_the_pass(tmp_path):
    """Re-verifying an existing ledger must not verify (or lose) its header."""
    corpus = _write_jsonl(tmp_path / "corpus.jsonl", [
        {"id": "1:vid1", "cues": [[14, "I grew up in a tiny town in Ohio"]]}])
    header = {"schema": "tl-creator-meta/v2", "channel_id": 1, "channel_name": "P",
              "coverage": {"facts": 1}}
    ledger = tmp_path / "1-facts.jsonl"
    store_io.write_ledger(ledger, header, [
        {"provenance": "transcript", "video": "1:vid1",
         "quote": "I grew up in a tiny town in Ohio"}])
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "verify_quotes.py"),
         "--in", str(ledger), "--corpus", str(corpus),
         "--out", str(tmp_path / "out.jsonl")],
        capture_output=True, text=True)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["candidates"] == 1      # the header is not one
    meta, facts = store_io.read_ledger(tmp_path / "out.jsonl")
    assert meta == header
    assert len(facts) == 1 and facts[0]["verify"]["match"] == "exact"


# --------------------------------------------------------------------------- #
# build_html.py — the connections page, the one human deliverable
# --------------------------------------------------------------------------- #
_META = {"schema": "tl-creator-meta/v2", "channel_id": 42, "channel_name": "Patterrz",
         "generated_at": "2026-08-31", "corpus_window": ["2019-04-02", "2026-08-20"],
         "coverage": {"videos_with_transcript": 412, "videos_matched": 287,
                      "passages": 2252, "windows_judged": 500, "gems": 310, "facts": 6},
         "format": "solo", "lanes": "transcripts", "latest_video_date": "2026-08-29",
         "rounds": 2}

_FACTS = [
    {"fact_id": "f1", "claim": "has a dog", "domain": "pets", "confidence": "confirmed",
     "sensitivity": "none", "sensitive": False, "recurrence": 4, "selected": True,
     "quote": "we finally adopted luna from the shelter last spring and she",
     "url": "https://www.youtube.com/watch?v=abc&t=12s"},
    {"fact_id": "f2", "claim": "wears glasses", "domain": "health", "confidence": "confirmed",
     "sensitivity": "lifestyle", "sensitive": False, "recurrence": 2},
    {"fact_id": "f3", "claim": "was diagnosed with ADHD", "domain": "health",
     "confidence": "confirmed", "sensitivity": "clinical", "sensitive": True, "recurrence": 3},
    {"fact_id": "f4", "claim": "daughter is named Maple", "domain": "family",
     "confidence": "confirmed", "sensitivity": "children", "sensitive": True, "recurrence": 5},
    {"fact_id": "f5", "claim": "lives on Elm Street", "domain": "home",
     "confidence": "unconfirmed", "sensitivity": "location", "sensitive": True},
    {"fact_id": "f6", "claim": "lived in LA", "domain": "home", "confidence": "confirmed",
     "sensitivity": "none", "sensitive": False, "superseded_by": "f7",
     "source_url": "https://example.com/about"},
]

_CONN_MD = (
    "---\n"
    "schema: tl-creator-connections/v2\n"
    "channel_id: 42\n"
    'channel_name: "Patterrz"\n'
    "brand_id: 7\n"
    "brand_name: Acme\n"
    "facts_file: 42-facts.jsonl\n"
    "brand_read_date: 2026-09-02\n"
    "---\n\n"
    "Built from 6 facts.\n\n"
    "## About Acme\n\n"
    "Acme is a direct-to-consumer dog food brand [web: product pages].\n\n"
    "## 1. Adopted a rescue dog — **direct**\n\n"
    "> we finally adopted luna [watch](https://youtube.com/w?v=abc&t=12s)\n\n"
    "Acme sells dog food [web]\n\n"
    "## Streams on Sundays — **category precedent**\n\n"
    "Bakes sourdough weekly [social: instagram]\n"
)


def _write_ledger(tmp_path: Path, facts=None, meta=None) -> Path:
    path = tmp_path / "42-facts.jsonl"
    store_io.write_ledger(path, _META if meta is None else meta,
                           _FACTS if facts is None else facts)
    return path


def _render_conn(tmp_path: Path, md: str, facts=None, meta=None,
                 with_ledger: bool = True, name: str = "42-7-connections.md",
                 out: Path | None = None) -> str:
    src = tmp_path / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(md)
    cmd = [sys.executable, str(_SCRIPTS / "build_html.py"), "--in", str(src)]
    if with_ledger:
        cmd += ["--facts", str(_write_ledger(tmp_path, facts, meta))]
    if out:
        cmd += ["--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return Path(json.loads(proc.stdout)["html"]).read_text()


def test_connections_page_leads_with_who_they_are_then_ranked_cards(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD)
    assert "<title>Patterrz × Acme</title>" in html
    who, rest = html.split("<h2>Connections</h2>")
    assert "<h2>Who they are</h2>" in who
    assert "format: solo" in who and "videos 2019-04 → 2026-08" in who
    assert "6 facts in the ledger" in who
    # top facts by domain, with a short quote and its link
    assert "has a dog" in who and "we finally adopted luna" in who
    assert 'href="https://www.youtube.com/watch?v=abc&amp;t=12s">watch</a>' in who
    assert "wears glasses" in who and "badge-lifestyle" in who
    assert "was diagnosed with ADHD" in who and "badge-clinical" in who
    # withheld tiers and superseded facts never reach the brand-facing section
    assert "Maple" not in who and "Elm Street" not in who and "lived in LA" not in who
    conn = rest.split("<h2>About this ledger</h2>")[0]
    # the cards: numbered by order, type badge, provenance labels kept
    assert '<ol class="conn">' in conn and conn.count('<li><div class="body">') == 2
    assert "<h3>Adopted a rescue dog — " in conn      # the "1." is the card's numeral
    assert 'class="badge badge-direct">direct</span>' in conn
    assert 'class="badge badge-precedent">category precedent</span>' in conn
    assert "[web]" in conn and "social: instagram" in conn
    assert "brand read 2026-09-02" in html and "ledger built 2026-08-31" in html
    # the only external reference is the font stylesheet; no script anywhere
    head = html.split("</head>")[0]
    assert head.count("http") == 1 and "fonts.googleapis.com" in head
    assert "<script" not in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert ':root[data-theme="dark"]' in html


def test_the_about_brand_strip_is_prose_above_the_cards_not_a_card(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD)
    assert '<div class="about"><h3>About Acme</h3>' in html
    assert "direct-to-consumer dog food brand" in html
    # it sits between who-they-are and the connections, and is not numbered
    assert html.index('class="about"') < html.index("<h2>Connections</h2>")
    assert html.index("<h2>Who they are</h2>") < html.index('class="about"')
    conn = html.split("<h2>Connections</h2>")[1]
    assert "About Acme" not in conn
    assert conn.count('<li><div class="body">') == 2     # the two real connections


def test_the_ledger_footer_carries_the_honesty_surfaces(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD)
    assert "<h2>About this ledger</h2>" in html
    footer = html.split("<h2>About this ledger</h2>")[1]
    assert "6 facts: 5 confirmed, 1 unconfirmed" in footer
    # f3 is clinical but discussed in 3 videos, so it is usable in angles and
    # not counted as withheld; children + location are
    assert "1 lifestyle, 1 clinical, 1 children, 1 location — 2 withheld from angles" in footer
    assert ("287/412 transcript videos matched, 500 passages judged — "
            "absence is not evidence") in footer
    assert "format: solo · corpus 2019-04-02 → 2026-08-20 · lanes: transcripts" in footer
    assert "2 rounds · built 2026-08-31" in footer
    # the withheld facts are counted in the footer, never shown on the page
    assert "Maple" not in html and "Elm Street" not in html


def test_a_passing_clinical_mention_counts_as_withheld(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD, facts=[
        {"claim": "takes medication", "domain": "health", "sensitivity": "clinical",
         "recurrence": 1}])
    assert "1 clinical — 1 withheld from angles" in html


def test_old_boolean_ledgers_count_as_withheld(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD, facts=[
        {"claim": "born 1998", "domain": "family", "sensitive": True},
        {"claim": "has a cat", "domain": "pets", "sensitive": False}])
    assert "1 withheld (untiered)" in html
    assert "born 1998" not in html                 # untiered-but-flagged is withheld


def test_the_footer_lists_linked_platforms_and_sibling_channels(tmp_path):
    meta = dict(_META, lanes="transcripts",
                context={"social_links": ["https://instagram.com/patterrz", "javascript:x"],
                         "second_channel_candidates": [{"name": "Patterrz Clips", "id": 43}]})
    html = _render_conn(tmp_path, _CONN_MD, meta=meta)
    footer = html.split("<h2>About this ledger</h2>")[1]
    assert "<h3>Other channels and platforms</h3>" in footer
    assert 'href="https://instagram.com/patterrz"' in footer
    assert "linked but unread (socials lane not run)" in footer
    assert 'href="javascript' not in html and "javascript:x" in footer
    assert "Patterrz Clips (id 43) — not mined" in footer
    read = _render_conn(tmp_path, _CONN_MD, meta=dict(meta, lanes="transcripts+socials"))
    assert "read (socials lane)" in read and "unread" not in read


def test_the_meta_header_is_read_from_the_ledger_itself(tmp_path):
    """No --meta: the record is the ledger's first line."""
    html = _render_conn(tmp_path, _CONN_MD)
    assert "ledger built 2026-08-31" in html and "format: solo" in html


def test_a_legacy_headerless_ledger_still_renders_with_meta(tmp_path):
    src = tmp_path / "42-7-connections.md"
    src.write_text(_CONN_MD)
    facts = tmp_path / "42-facts.jsonl"
    store_io.write_ledger(facts, None, _FACTS)
    meta = tmp_path / "42-meta.json"
    meta.write_text(json.dumps(_META))
    subprocess.run([sys.executable, str(_SCRIPTS / "build_html.py"), "--in", str(src),
                    "--facts", str(facts), "--meta", str(meta)],
                   capture_output=True, text=True, check=True)
    assert "ledger built 2026-08-31" in (tmp_path / "42-7-connections.html").read_text()


def test_the_page_defaults_next_to_the_ledger_named_from_the_ids(tmp_path):
    """The markdown is a working file under .corpus/; only the HTML is a
    deliverable, and it lands beside the ledger."""
    corpus = tmp_path / ".corpus" / "42"
    corpus.mkdir(parents=True)
    src = corpus / "connections-7.md"
    src.write_text(_CONN_MD)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "build_html.py"), "--in", str(src),
         "--facts", str(_write_ledger(tmp_path))],
        capture_output=True, text=True, check=True)
    out = Path(json.loads(proc.stdout)["html"])
    assert out == tmp_path / "42-7-connections.html" and out.exists()
    assert not (corpus / "connections-7.html").exists()


def test_without_a_ledger_the_page_lands_next_to_its_markdown(tmp_path):
    html = _render_conn(tmp_path, _CONN_MD, with_ledger=False)
    assert (tmp_path / "42-7-connections.html").exists()
    assert "Who they are" not in html and '<ol class="conn">' in html
    assert "About this ledger" not in html          # nothing to be honest about


def test_no_fit_verdict_renders_as_prose_without_cards(tmp_path):
    md = ("---\nchannel_name: Patterrz\nbrand_name: Acme\n---\n\n"
          "**No fit**: nothing in the ledger meets Acme. Searched: 3 category terms.\n")
    html = _render_conn(tmp_path, md)
    assert '<ol class="conn">' not in html
    assert 'class="badge badge-nofit">No fit</span>' in html
    assert "Searched: 3 category terms" in html
    assert "<h2>About this ledger</h2>" in html     # the coverage still bounds it


def test_html_escapes_untrusted_markdown_text(tmp_path):
    html = _render_conn(tmp_path, "# T\n\nquote says <script>alert(1)</script>\n",
                        with_ledger=False)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_link_target_quote_cannot_break_out_of_href(tmp_path):
    html = _render_conn(tmp_path, (
        '# T\n\n[click](https://x.com/a"onmouseover="alert(1))\n'), with_ledger=False)
    assert 'onmouseover="alert' not in html
    assert "&quot;onmouseover=&quot;" in html


def test_markdown_h1_title_is_not_double_escaped(tmp_path):
    html = _render_conn(tmp_path, "# Rhett & Link\n\nhello\n", with_ledger=False)
    assert "<title>Rhett &amp; Link</title>" in html
    assert "&amp;amp;" not in html


def test_href_ampersands_escape_exactly_once(tmp_path):
    html = _render_conn(tmp_path,
                        "[watch](https://www.youtube.com/watch?v=abc&t=90s)\n",
                        with_ledger=False)
    assert 'href="https://www.youtube.com/watch?v=abc&amp;t=90s"' in html
    assert "&amp;amp;" not in html


def test_who_they_are_link_only_http_schemes(tmp_path):
    facts = [{"fact_id": "f1", "claim": "grew up in Ohio", "domain": "origin",
              "quote": "I grew up in Ohio", "url": "javascript:alert(1)",
              "sensitivity": "none"}]
    html = _render_conn(tmp_path, _CONN_MD, facts=facts)
    who = html.split("<h2>Connections</h2>")[0]
    assert 'href="javascript' not in who and "grew up in Ohio" in who


def test_locate_prefers_the_occurrence_nearest_the_hint():
    sys.path.insert(0, str(_SCRIPTS))
    from verify_quotes import locate
    cues = [(10.0, "I grew up in Ohio you know"),
            (200.0, "and then she said I grew up in Ohio too")]
    quote = "I grew up in Ohio"
    assert locate(cues, quote)["start"] == 10
    hit = locate(cues, quote, hint_start=190)
    assert hit["start"] == 200 and hit["occurrences"] == 2


def test_rows_raises_on_withheld_premium_fields():
    shared = _SCRIPTS.parents[1] / "_shared"
    sys.path.insert(0, str(shared))
    import pytest
    import tl_data
    with pytest.raises(tl_data.DataError, match="premium"):
        tl_data._rows({"results": [{"id": 1}],
                       "_upgrade_required": {"message": "upgrade",
                                             "fields": ["transcript"]}})
    assert tl_data._rows({"results": [{"id": 1}]}) == [{"id": 1}]


def test_write_context_builds_the_extractor_block_from_the_saved_full_context(tmp_path):
    full = tmp_path / "context-full.json"
    full.write_text(json.dumps({"name": "Ali Abdaal",
                                "context_stats": {"fp_per_1k_words_median": 41}}))
    out = tmp_path / "context.json"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "channel_context.py"), "--from", str(full),
         "--format-label", "solo", "--format-evidence", "fp density 41/1k",
         "--known-facts", "ex-doctor; lives in London", "--write-context", str(out)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text()) == {
        "channel_name": "Ali Abdaal", "host_names": ["Ali Abdaal"],
        "known_facts": ["ex-doctor", "lives in London"],
        "format_label": "solo", "format_evidence": "fp density 41/1k"}
    # the label is an enum: a near-miss fails here, not in front of 20 agents
    bad = subprocess.run(
        [sys.executable, str(_SCRIPTS / "channel_context.py"), "--from", str(full),
         "--format-label", "Solo", "--write-context", str(out)],
        capture_output=True, text=True)
    assert bad.returncode != 0


def test_youtu_be_shortlinks_are_not_second_channel_candidates():
    sys.path.insert(0, str(_SCRIPTS))
    import channel_context
    row = {"external_channel_id": "UCmain", "url": "https://youtube.com/@main"}
    doc = {"social_links": ["https://youtu.be/dQw4w9WgXcQ",
                            "https://youtube.com/@mainVlogs"],
           "description": "watch https://youtu.be/abc123 now"}
    cands = channel_context.second_channel_candidates(row, doc)
    assert [c["link"] for c in cands] == ["https://youtube.com/@mainVlogs"]


# --------------------------------------------------------------------------- #
# the one-pager: section order, the caveat block, and the --check pass
# --------------------------------------------------------------------------- #
_FULL_MD = (
    "---\n"
    "schema: tl-creator-connections/v2\n"
    "channel_id: 42\n"
    'channel_name: "Patterrz"\n'
    "brand_id: 7\n"
    "brand_name: Acme\n"
    "facts_file: 42-facts.jsonl\n"
    "brand_read_date: 2026-09-02\n"
    "---\n\n"
    "## About Patterrz\n\n"
    "A solo creator who has been posting since 2019.\n\n"
    "## Thesis\n\n"
    "He already lives the thing Acme sells, and says so unprompted.\n\n"
    "## About Acme\n\n"
    "Acme is a direct-to-consumer dog food brand [web: product pages].\n\n"
    "## Adopted a rescue dog — **direct**\n\n"
    "> we finally adopted luna [watch](https://youtube.com/w?v=abc&t=12s)\n\n"
    "Acme sells dog food [web]\n\n"
    "**Do.** Let him tell the adoption story first.\n\n"
    "**Do not.** Open on the ingredient list.\n\n"
    "## Where this could go wrong\n\n"
    "He has said he distrusts subscription boxes.\n"
)


def _check_conn(tmp_path: Path, md: str, name: str = "42-7-connections.md"):
    src = tmp_path / name
    src.write_text(md)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "build_html.py"), "--in", str(src),
         "--facts", str(_write_ledger(tmp_path)), "--check"],
        capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def test_the_page_puts_the_thesis_above_the_brand(tmp_path):
    html = _render_conn(tmp_path, _FULL_MD)
    # the reader wants the argument before the background
    order = [html.index(x) for x in ('<h2>Who they are</h2>',
                                     '<h2>The thesis</h2>',
                                     '<div class="about"><h3>About Acme</h3>',
                                     '<h2>Connections</h2>')]
    assert order == sorted(order)
    assert "already lives the thing Acme sells" in html
    # the creator introduction is prose inside "Who they are", not a card
    assert "posting since 2019" in html.split('<h2>The thesis</h2>')[0]
    conn = html.split("<h2>Connections</h2>")[1]
    assert "About Patterrz" not in conn


def test_each_quote_appears_once_inside_its_own_card(tmp_path):
    """The page used to repeat every card's strongest quote in an "In their
    own words" strip above the brand. Removed: a quote read twice is not
    read twice as hard, and the card is where it carries its connection."""
    html = _render_conn(tmp_path, _FULL_MD)
    assert "In their own words" not in html
    assert 'class="bridges"' not in html
    # nothing repeats a card's quote above the brand any more
    above_brand = html.split('<div class="about"><h3>About Acme</h3>')[0]
    assert "we finally adopted luna" not in above_brand.split(
        '<h2>The thesis</h2>')[1]
    # the quote still renders, once, inside its own card, with its link
    cards = html.split('<h2>Connections</h2>')[1]
    assert cards.count("we finally adopted luna") == 1
    assert cards.count('href="https://youtube.com/w?v=abc&amp;t=12s"') == 1


def test_the_caveat_is_kept_but_never_numbered_among_the_connections(tmp_path):
    html = _render_conn(tmp_path, _FULL_MD)
    # honest mismatch beats overfitting, so it stays on the page …
    assert "distrusts subscription boxes" in html
    assert '<div class="caveat">' in html
    # … but a caveat inside the ranked list reads as an angle
    conn = html.split('<ol class="conn">')[1].split("</ol>")[0]
    assert conn.count('<li><div class="body">') == 1
    assert "could go wrong" not in conn.lower()
    assert html.index('<ol class="conn">') < html.index('<div class="caveat">')


def test_check_passes_a_complete_map(tmp_path):
    code, report = _check_conn(tmp_path, _FULL_MD)
    assert code == 0 and report["ok"] and report["problems"] == []


def test_check_names_every_missing_section(tmp_path):
    stripped = _FULL_MD.replace(
        "## About Patterrz\n\nA solo creator who has been posting since 2019.\n\n", ""
    ).replace(
        "## Thesis\n\nHe already lives the thing Acme sells, and says so unprompted.\n\n", ""
    ).replace(
        "## Where this could go wrong\n\nHe has said he distrusts subscription boxes.\n", ""
    )
    code, report = _check_conn(tmp_path, stripped)
    assert code == 3 and not report["ok"]
    joined = " | ".join(report["problems"])
    assert "## About Patterrz" in joined
    assert "## Thesis" in joined
    assert "Where this could go wrong" in joined


def test_check_catches_a_connection_with_no_quote_and_price_language(tmp_path):
    md = _FULL_MD.replace(
        "> we finally adopted luna [watch](https://youtube.com/w?v=abc&t=12s)\n\n",
        "He mentions a dog sometimes.\n\n")
    md += "\nThe integration ran at a $4,000 flat fee.\n"
    code, report = _check_conn(tmp_path, md)
    assert code == 3
    joined = " | ".join(report["problems"])
    assert "carries no quote" in joined
    assert "price, cost or rate language" in joined


def test_a_render_still_writes_the_page_but_reports_contract_problems(tmp_path):
    """--check gates; a plain render never silently swallows the same finding."""
    src = tmp_path / "42-7-connections.md"
    src.write_text(_CONN_MD)          # no creator intro, no thesis, no caveat
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "build_html.py"), "--in", str(src),
         "--facts", str(_write_ledger(tmp_path))],
        capture_output=True, text=True, check=True)
    assert Path(json.loads(proc.stdout)["html"]).exists()
    assert json.loads(proc.stdout)["problems"]
    assert "PAGE CONTRACT" in proc.stderr
