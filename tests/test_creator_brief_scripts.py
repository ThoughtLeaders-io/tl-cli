"""The creator-brief scripts: local verification, the deterministic
connections page, and the scripted extractor's mechanical guarantees (return
files, resume, retry, key resolution, loud no-key exit). The retrieval and
assembly stages have their own files — ``test_fetch_cues.py`` and
``test_assemble_extracts.py``. No real network anywhere: the scripted
extractor talks to a fake OpenAI-compatible endpoint on 127.0.0.1.
"""

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import classify_gems  # noqa: E402
import ledger_io  # noqa: E402


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
    ledger_io.write_ledger(ledger, header, [
        {"provenance": "transcript", "video": "1:vid1",
         "quote": "I grew up in a tiny town in Ohio"}])
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "verify_quotes.py"),
         "--in", str(ledger), "--corpus", str(corpus),
         "--out", str(tmp_path / "out.jsonl")],
        capture_output=True, text=True)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["candidates"] == 1      # the header is not one
    meta, facts = ledger_io.read_ledger(tmp_path / "out.jsonl")
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
    ledger_io.write_ledger(path, _META if meta is None else meta,
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
    ledger_io.write_ledger(facts, None, _FACTS)
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


# --------------------------------------------------------------------------- #
# classify_gems.py — the scripted extractor, against a local fake endpoint
# --------------------------------------------------------------------------- #
def _windows_in(prompt: str) -> list[dict]:
    """The window array the rendered extractor message carries."""
    tail = prompt.split("=== WINDOWS", 1)[1].split("===\n", 1)[1]
    return json.loads(tail.splitlines()[0])


def _extract_for(prompt: str, **over) -> dict:
    """A contract-shaped extract for the batch in this prompt: every other
    window a gem, each verdict echoing its own window's `start`."""
    gems, not_gems = [], []
    for w in _windows_in(prompt):
        if w["i"] % 2 == 0:
            text = w["text"]
            ws = text.split()
            gems.append({"i": w["i"], "start": w["start"],
                         "anchor": " ".join(ws[:5]),
                         "life_domain": "family", "speaker_guess": "host",
                         "sensitivity": "none", "entity_corrections": {},
                         "notable": "father ran a bakery",
                         "claim": "father ran a bakery",
                         "quote_span": {"first": " ".join(ws[:4]),
                                        "last": " ".join(ws[-4:])},
                         "confidence": "confirmed"})
        else:
            not_gems.append({"i": w["i"], "speaker_guess": "guest",
                             "reason": "third-party"})
    out = {"gems": gems, "not_gems": not_gems}
    out.update(over)
    return out


class _FakeLLM:
    """An OpenAI-compatible /chat/completions endpoint on 127.0.0.1."""

    def __init__(self, content):
        self.content = content       # callable(prompt) -> response content
        self.requests = []
        handler = self._handler()
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def _handler(server_self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(
                    int(self.headers["Content-Length"])).decode())
                prompt = body["messages"][0]["content"]
                server_self.requests.append(body)
                content = server_self.content(prompt)
                payload = json.dumps({
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        return H

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _corpus(tmp_path: Path, batches: int = 2, per: int = 2):
    root = tmp_path / "batches"
    root.mkdir()
    for n in range(1, batches + 1):
        root.joinpath(f"batch-{n:03d}.json").write_text(json.dumps([
            {"id": f"1:vid{n}", "start": 10 * n + i,
             "text": f"my dad ran a bakery in a tiny town number {n}{i}",
             "title": "t", "format_hint": None, "in_sponsor_read": False}
            for i in range(per)]))
    ctx = tmp_path / "context.json"
    ctx.write_text(json.dumps({"channel_name": "Patterrz"}))
    return root, ctx


def _run_extract(batch_dir, ctx, *extra, env=None):
    e = {"PATH": "/usr/bin:/bin"}
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "classify_gems.py"),
         "--batches", str(batch_dir), "--context", str(ctx), *extra],
        capture_output=True, text=True, env=e)


def test_scripted_extractor_writes_one_return_file_per_batch(tmp_path):
    batches, ctx = _corpus(tmp_path)
    fake = _FakeLLM(lambda p: json.dumps(_extract_for(p)))
    try:
        proc = _run_extract(batches, ctx, env={
            "CREATOR_BRIEF_LLM_API_KEY": "k",
            "CREATOR_BRIEF_LLM_BASE_URL": fake.url,
            "CREATOR_BRIEF_LLM_MODEL": "test-model"})
    finally:
        fake.stop()
    assert proc.returncode == 0, proc.stderr
    returns = tmp_path / "returns"
    files = sorted(p.name for p in returns.glob("*.json"))
    assert files == ["batch-001.extract.json", "batch-002.extract.json"]
    obj = json.loads((returns / "batch-002.extract.json").read_text())
    # the response carried neither key; both are filled in from the batch
    assert obj["batch"] == "002" and obj["windows"] == 2
    assert [g["start"] for g in obj["gems"]] == [20]     # echoed, assemblable
    assert [x["i"] for x in obj["not_gems"]] == [1]
    summary = json.loads(proc.stdout)
    assert summary["batches"] == 2 and summary["batches_written"] == 2
    assert summary["errors"] == 0 and summary["windows"] == 4
    assert summary["skipped_existing"] == 0
    assert summary["prompt_tokens"] == 22 and summary["completion_tokens"] == 6
    assert summary["largest_return_chars"] > 100
    line = next(ln for ln in proc.stderr.splitlines()
                if ln.startswith("FUNNEL stage=extract"))
    f = dict(kv.split("=", 1) for kv in line.split()[1:])
    assert f["path"] == "api" and f["batches"] == "2" and f["windows"] == "4"
    assert f["written"] == "2" and f["errors"] == "0"


def test_existing_return_files_are_skipped_unless_forced(tmp_path):
    batches, ctx = _corpus(tmp_path, batches=2)
    returns = tmp_path / "returns"
    returns.mkdir()
    (returns / "batch-001.extract.json").write_text('{"gems": [], '
                                                    '"not_gems": []}')
    fake = _FakeLLM(lambda p: json.dumps(_extract_for(p)))
    env = {"CREATOR_BRIEF_LLM_API_KEY": "k",
           "CREATOR_BRIEF_LLM_BASE_URL": fake.url,
           "CREATOR_BRIEF_LLM_MODEL": "test-model"}
    try:
        first = _run_extract(batches, ctx, env=env)
        assert json.loads(first.stdout)["skipped_existing"] == 1
        assert json.loads(first.stdout)["batches_written"] == 1
        assert len(fake.requests) == 1
        forced = _run_extract(batches, ctx, "--force", env=env)
    finally:
        fake.stop()
    assert json.loads(forced.stdout)["skipped_existing"] == 0
    assert json.loads(forced.stdout)["batches_written"] == 2
    assert len(fake.requests) == 3
    # --force actually replaced the stub
    assert json.loads(
        (returns / "batch-001.extract.json").read_text())["gems"]


def test_malformed_content_is_retried_then_counted_as_an_error(tmp_path):
    batches, ctx = _corpus(tmp_path, batches=1)
    fake = _FakeLLM(lambda p: json.dumps(["not", "an", "object"]))
    try:
        proc = _run_extract(batches, ctx, env={
            "CREATOR_BRIEF_LLM_API_KEY": "k",
            "CREATOR_BRIEF_LLM_BASE_URL": fake.url,
            "CREATOR_BRIEF_LLM_MODEL": "test-model"})
        attempts = len(fake.requests)
    finally:
        fake.stop()
    assert attempts == 1 + classify_gems.RETRIES == 3
    assert proc.returncode == 1
    summary = json.loads(proc.stdout)
    assert summary["errors"] == 1 and summary["batches_written"] == 0
    # nothing written: the assembler sees a missing return file for the batch
    assert not list((tmp_path / "returns").glob("*.json"))


def test_a_fenced_object_is_accepted(tmp_path):
    batches, ctx = _corpus(tmp_path, batches=1)
    fake = _FakeLLM(
        lambda p: "```json\n" + json.dumps(_extract_for(p)) + "\n```")
    try:
        proc = _run_extract(batches, ctx, env={
            "CREATOR_BRIEF_LLM_API_KEY": "k",
            "CREATOR_BRIEF_LLM_BASE_URL": fake.url,
            "CREATOR_BRIEF_LLM_MODEL": "test-model"})
    finally:
        fake.stop()
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(
        (tmp_path / "returns" / "batch-001.extract.json").read_text())
    assert obj["batch"] == "001" and len(obj["gems"]) == 1


def _run_no_key(tmp_path: Path, windows: list[dict], env: dict | None = None):
    batches = tmp_path / "batches"
    batches.mkdir()
    (batches / "batch-000.json").write_text(json.dumps(windows))
    ctx = tmp_path / "context.json"
    ctx.write_text("{}")
    e = {"PATH": "/usr/bin:/bin"}
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "classify_gems.py"),
         "--batches", str(batches), "--context", str(ctx)],
        capture_output=True, text=True,
        env=e), batches


def test_no_api_key_emits_the_fallback_marker_and_its_own_exit_code(tmp_path):
    proc, batches = _run_no_key(tmp_path, [{"id": "1:aaa", "start": 5,
                                            "text": "my dad ran a bakery"}])
    # a code of its own: not argparse's usage 2, not the "errors" 1
    assert proc.returncode == classify_gems.EXIT_FALLBACK_REQUIRED == 20
    marker = next(ln for ln in proc.stderr.splitlines()
                  if ln.startswith("FALLBACK_REQUIRED"))
    fields = dict(kv.split("=", 1) for kv in marker.split()[1:])
    assert fields["reason"] == "missing_api_key"
    # the fallback consumes exactly these files, so the path must resolve
    assert Path(fields["batches_dir"]) == batches.resolve()
    assert fields["batch_files"] == "1" and fields["windows"] == "1"
    assert "CREATOR_BRIEF_LLM_API_KEY" in proc.stderr
    assert "CREATOR_BRIEF_LLM_BASE_URL" in proc.stderr
    assert "CREATOR_BRIEF_LLM_MODEL" in proc.stderr
    assert "fall back" in proc.stderr


def test_missing_model_alone_also_exits_fallback_required(tmp_path):
    proc, _ = _run_no_key(
        tmp_path, [{"id": "1:aaa", "start": 5, "text": "my dad ran a bakery"}],
        env={"CREATOR_BRIEF_LLM_API_KEY": "k",
             "CREATOR_BRIEF_LLM_BASE_URL": "http://127.0.0.1:1"})
    assert proc.returncode == classify_gems.EXIT_FALLBACK_REQUIRED == 20
    marker = next(ln for ln in proc.stderr.splitlines()
                  if ln.startswith("FALLBACK_REQUIRED"))
    fields = dict(kv.split("=", 1) for kv in marker.split()[1:])
    assert fields["missing"] == "CREATOR_BRIEF_LLM_MODEL"
    assert "CREATOR_BRIEF_LLM_MODEL" in proc.stderr


def test_no_api_key_still_prints_its_funnel_line(tmp_path):
    proc, _ = _run_no_key(tmp_path, [{"id": "1:aaa", "start": 5, "text": "x"},
                                     {"id": "1:bbb", "start": 9, "text": "y"}])
    line = next(ln for ln in proc.stderr.splitlines()
                if ln.startswith("FUNNEL stage=extract"))
    fields = dict(kv.split("=", 1) for kv in line.split()[1:])
    assert fields["path"] == "fallback_required"
    assert fields["windows"] == "2" and fields["written"] == "0"
    assert float(fields["elapsed_s"]) >= 0


def test_concurrency_comes_from_the_env_within_bounds(monkeypatch):
    monkeypatch.delenv("CREATOR_BRIEF_LLM_CONCURRENCY", raising=False)
    assert classify_gems.env_concurrency() == classify_gems.CONCURRENCY == 16
    monkeypatch.setenv("CREATOR_BRIEF_LLM_CONCURRENCY", "8")
    assert classify_gems.env_concurrency() == 8
    monkeypatch.setenv("CREATOR_BRIEF_LLM_CONCURRENCY", "9999")
    assert classify_gems.env_concurrency() == classify_gems.MAX_CONCURRENCY
    monkeypatch.setenv("CREATOR_BRIEF_LLM_CONCURRENCY", "0")
    assert classify_gems.env_concurrency() == classify_gems.MIN_CONCURRENCY
    monkeypatch.setenv("CREATOR_BRIEF_LLM_CONCURRENCY", "lots")
    assert classify_gems.env_concurrency() == classify_gems.CONCURRENCY


def test_the_extract_parser_fills_in_batch_and_windows_only_when_missing():
    got = classify_gems.parse_extract('{"gems": [], "not_gems": []}', "007", 25)
    assert got == {"batch": "007", "windows": 25, "gems": [], "not_gems": []}
    kept = classify_gems.parse_extract(
        '{"batch": "003", "windows": 4, "gems": [], "not_gems": []}', "007", 25)
    assert kept["batch"] == "003" and kept["windows"] == 4
    for bad in ('[]', 'not json', '{"gems": []}', '{"gems": {}, "not_gems": []}'):
        assert classify_gems.parse_extract(bad, "007", 25) is None


def test_locate_prefers_the_occurrence_nearest_the_hint():
    sys.path.insert(0, str(_SCRIPTS))
    from quote_timestamp import locate
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
