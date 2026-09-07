"""assemble_extracts.py — the mechanical half of the extraction pass.

The extractor agents are the only judgment in the model layer, so everything
they can get wrong is checked here rather than trusted: the count contract
(every window judged exactly once), the hard `start` check (a verdict about a
different window is not a verdict), the advisory anchor, the quote span cut
from the window text (which is what makes every published quote verbatim by
construction), the enums, and the respawn ledger + exit code that send failures
back to a fresh agent instead of a hand patch. No network anywhere.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import assemble_extracts  # noqa: E402

_TEXT = ("so my dad ran a bakery in a tiny town in ohio for thirty years "
         "and i worked the counter every summer until i left for college")


def _window(i: int, text: str = _TEXT) -> dict:
    return {"id": f"7:vid{i}", "video_id": f"vid{i}", "title": "a video",
            "published": "2024-03-02", "start": 100 + i, "text": text}


def _gem(i: int, w: dict, **over) -> dict:
    gem = {"i": i, "start": w["start"], "anchor": " ".join(w["text"].split()[:5]),
           "life_domain": "family", "speaker_guess": "host",
           "sensitivity": "none", "entity_corrections": {},
           "notable": "father ran a bakery", "claim": "father ran a bakery in Ohio",
           "quote_span": {"first": "my dad ran a", "last": "in ohio"},
           "confidence": "confirmed"}
    gem.update(over)
    return gem


def _run(tmp_path, windows: list[dict], extract: dict | str | None,
         batch: str = "000"):
    batches = tmp_path / "batches"
    returns = tmp_path / "returns"
    batches.mkdir(exist_ok=True)
    returns.mkdir(exist_ok=True)
    (batches / f"batch-{batch}.json").write_text(json.dumps(windows))
    if extract is not None:
        (returns / f"batch-{batch}.extract.json").write_text(
            extract if isinstance(extract, str) else json.dumps(extract))
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(batches), "--returns", str(returns),
         "--out", str(out)], capture_output=True, text=True)
    read = lambda name: [json.loads(x) for x in                      # noqa: E731
                         (out / name).read_text().splitlines() if x.strip()]
    return (proc, json.loads(proc.stdout), read("classified.jsonl"),
            read("gems.jsonl"), read("candidates.jsonl"),
            json.loads((out / "respawn.json").read_text()))


# --------------------------------------------------------------------------- #
# the count contract: every index judged exactly once
# --------------------------------------------------------------------------- #
def test_a_complete_batch_assembles_and_exits_clean(tmp_path):
    wins = [_window(0), _window(1)]
    proc, summary, rows, gems, cands, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 2, "gems": [_gem(0, wins[0])],
        "not_gems": [{"i": 1, "speaker_guess": "guest", "reason": "third-party"}]})
    assert proc.returncode == 0
    assert summary["windows_expected"] == 2 and summary["windows_assembled"] == 2
    assert respawn == {} and summary["unjudged_windows"] == 0 and summary["coverage"] == 1.0
    assert len(gems) == 1 and len(cands) == 1
    assert [r["verdict"]["self_disclosure"] for r in rows].count(False) == 1


def test_a_missing_index_respawns_only_that_window(tmp_path):
    wins = [_window(0), _window(1), _window(2)]
    proc, summary, rows, gems, _, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 3, "gems": [_gem(0, wins[0])],
        "not_gems": [{"i": 2, "speaker_guess": "host", "reason": "ad-read"}]})
    assert proc.returncode == 3                      # "work is left", not failure
    assert respawn == {"000": [1]}
    assert len(rows) == 2 and len(gems) == 1


def test_a_duplicated_index_invalidates_both_of_its_verdicts(tmp_path):
    wins = [_window(0), _window(1)]
    _, _, rows, _, _, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 2,
        "gems": [_gem(0, wins[0]), _gem(0, wins[0])],
        "not_gems": [{"i": 1, "speaker_guess": "host", "reason": "sarcasm"}]})
    assert respawn == {"000": [0]}
    assert [r["verdict"]["i"] for r in rows] == [1]


def test_a_missing_or_unparseable_return_respawns_the_whole_batch(tmp_path):
    wins = [_window(0), _window(1)]
    proc, summary, *_ , respawn = _run(tmp_path, wins, None)
    assert proc.returncode == 3 and respawn == {"000": [0, 1]}
    assert "missing file" in json.dumps(summary["problems"])

    proc, summary, *_, respawn = _run(tmp_path, wins, "{not json", batch="001")
    assert respawn["001"] == [0, 1]
    assert "unparseable" in json.dumps(summary["problems"])


def test_multiple_return_files_merge_and_a_later_file_overrides(tmp_path):
    wins = [_window(0), _window(1)]
    batches = tmp_path / "batches"
    returns = tmp_path / "returns"
    batches.mkdir()
    returns.mkdir()
    (batches / "batch-000.json").write_text(json.dumps(wins))
    # original: 0 is a gem, 1 is a not-gem
    (returns / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 2, "gems": [_gem(0, wins[0])],
        "not_gems": [{"i": 1, "speaker_guess": "guest", "reason": "third-party"}]}))
    # a mini-batch re-spawn for index 1: later file overrides it into a gem
    (returns / "batch-000.extract.r2.json").write_text(json.dumps({
        "batch": "000", "windows": 2, "gems": [_gem(1, wins[1])], "not_gems": []}))
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(batches), "--returns", str(returns),
         "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0
    rows = [json.loads(x) for x in (out / "classified.jsonl").read_text().splitlines()]
    gems = [json.loads(x) for x in (out / "gems.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all(r["verdict"]["self_disclosure"] for r in rows)   # both ended up gems
    assert len(gems) == 2


def test_a_duplicate_within_one_file_stays_invalid_even_if_a_later_file_is_clean(
        tmp_path):
    wins = [_window(0), _window(1)]
    batches = tmp_path / "batches"
    returns = tmp_path / "returns"
    batches.mkdir()
    returns.mkdir()
    (batches / "batch-000.json").write_text(json.dumps(wins))
    # index 0 appears twice WITHIN this one file: invalid, and that taint is
    # per-index for the whole batch, not undone by a later file's clean entry
    (returns / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 2,
        "gems": [_gem(0, wins[0]), _gem(0, wins[0])],
        "not_gems": [{"i": 1, "speaker_guess": "host", "reason": "sarcasm"}]}))
    (returns / "batch-000.extract.r2.json").write_text(json.dumps({
        "batch": "000", "windows": 2, "gems": [_gem(0, wins[0])], "not_gems": []}))
    out = tmp_path / "out"
    subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(batches), "--returns", str(returns),
         "--out", str(out)], capture_output=True, text=True)
    respawn = json.loads((out / "respawn.json").read_text())
    rows = [json.loads(x) for x in (out / "classified.jsonl").read_text().splitlines()]
    assert respawn == {"000": [0]}
    assert [r["verdict"]["i"] for r in rows] == [1]


def test_append_adds_a_later_rounds_rows_to_the_existing_outputs(tmp_path):
    out = tmp_path / "out"
    wins1 = [_window(0)]
    b1, r1 = tmp_path / "batches1", tmp_path / "returns1"
    b1.mkdir()
    r1.mkdir()
    (b1 / "batch-000.json").write_text(json.dumps(wins1))
    (r1 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins1[0])], "not_gems": []}))
    proc1 = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(b1), "--returns", str(r1), "--out", str(out)],
        capture_output=True, text=True)
    assert proc1.returncode == 0

    wins2 = [_window(1)]
    b2, r2 = tmp_path / "batches2", tmp_path / "returns2"
    b2.mkdir()
    r2.mkdir()
    (b2 / "batch-000.json").write_text(json.dumps(wins2))
    (r2 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins2[0])], "not_gems": []}))
    proc2 = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(b2), "--returns", str(r2), "--out", str(out),
         "--append"], capture_output=True, text=True)
    assert proc2.returncode == 0

    rows = [json.loads(x) for x in (out / "classified.jsonl").read_text().splitlines()]
    cands = [json.loads(x) for x in (out / "candidates.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and len(cands) == 2
    assert {c["video"] for c in cands} == {wins1[0]["id"], wins2[0]["id"]}


def test_without_append_a_second_run_replaces_the_output_files(tmp_path):
    out = tmp_path / "out"
    wins1 = [_window(0)]
    b1, r1 = tmp_path / "batches1", tmp_path / "returns1"
    b1.mkdir()
    r1.mkdir()
    (b1 / "batch-000.json").write_text(json.dumps(wins1))
    (r1 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins1[0])], "not_gems": []}))
    subprocess.run([sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
                    "--batches", str(b1), "--returns", str(r1), "--out", str(out)],
                   capture_output=True, text=True)

    wins2 = [_window(1)]
    b2, r2 = tmp_path / "batches2", tmp_path / "returns2"
    b2.mkdir()
    r2.mkdir()
    (b2 / "batch-000.json").write_text(json.dumps(wins2))
    (r2 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins2[0])], "not_gems": []}))
    subprocess.run([sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
                    "--batches", str(b2), "--returns", str(r2), "--out", str(out)],
                   capture_output=True, text=True)

    rows = [json.loads(x) for x in (out / "classified.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["window"]["id"] == wins2[0]["id"]


def test_non_object_envelope_is_a_problem_and_respawns_the_batch(tmp_path):
    wins = [_window(0)]
    _, summary, rows, *_, respawn = _run(tmp_path, wins, json.dumps([1, 2, 3]))
    assert respawn == {"000": [0]}
    assert rows == []
    assert "envelope is not an object" in json.dumps(summary["problems"])


def test_non_list_gems_is_a_problem_and_respawns_the_batch(tmp_path):
    wins = [_window(0)]
    _, summary, rows, *_, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 1, "gems": "not a list", "not_gems": []})
    assert respawn == {"000": [0]}
    assert rows == []
    assert "missing or not lists" in json.dumps(summary["problems"])


def test_a_fenced_return_is_still_read(tmp_path):
    wins = [_window(0)]
    body = json.dumps({"batch": "000", "windows": 1,
                       "gems": [_gem(0, wins[0])], "not_gems": []})
    proc, _, rows, *_ = _run(tmp_path, wins, f"```json\n{body}\n```")
    assert proc.returncode == 0 and len(rows) == 1


# --------------------------------------------------------------------------- #
# the window a verdict claims to be about
# --------------------------------------------------------------------------- #
def test_a_wrong_start_is_a_hard_failure(tmp_path):
    wins = [_window(0)]
    _, summary, rows, _, _, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 1,
        "gems": [_gem(0, wins[0], start=wins[0]["start"] + 5)], "not_gems": []})
    assert respawn == {"000": [0]} and rows == []
    assert "start" in json.dumps(summary["problems"])


def test_a_normalised_anchor_is_advisory_not_a_rejection(tmp_path):
    wins = [_window(0)]
    proc, summary, _, gems, *_ = _run(tmp_path, wins, {
        "batch": "000", "windows": 1,
        "gems": [_gem(0, wins[0], anchor="So, my dad ran")], "not_gems": []})
    assert proc.returncode == 0 and len(gems) == 1
    assert summary["anchor_soft_mismatches"] == 1


# --------------------------------------------------------------------------- #
# the quote span: cut by the script, never typed by the agent
# --------------------------------------------------------------------------- #
def test_the_span_is_cut_verbatim_from_the_window(tmp_path):
    wins = [_window(0)]
    _, _, _, gems, cands, _ = _run(tmp_path, wins, {
        "batch": "000", "windows": 1, "gems": [_gem(0, wins[0])],
        "not_gems": []})
    quote = cands[0]["quote"]
    assert quote == "my dad ran a bakery in a tiny town in ohio"
    assert quote in wins[0]["text"]                   # verbatim by construction
    assert gems[0]["verdict"]["quote"] == quote


def test_a_span_that_is_not_in_the_window_respawns_it(tmp_path):
    wins = [_window(0)]
    _, summary, rows, *_, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 1,
        "gems": [_gem(0, wins[0],
                      quote_span={"first": "my mum ran", "last": "in ohio"})],
        "not_gems": []})
    assert respawn == {"000": [0]} and rows == []
    assert "span" in json.dumps(summary["problems"])


def test_a_span_outside_the_length_band_is_refused(tmp_path):
    wins = [_window(0)]
    _, _, rows, *_, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 1,
        "gems": [_gem(0, wins[0],
                      quote_span={"first": "in ohio", "last": "in ohio"})],
        "not_gems": []})
    assert respawn == {"000": [0]} and rows == []     # two words is not a quote


def test_extract_span_reads_the_first_occurrence_forward():
    text = "i moved to austin and then i moved to berlin"
    assert assemble_extracts.extract_span(
        text, {"first": "i moved", "last": "berlin"}) == text
    assert assemble_extracts.extract_span(text, {"first": "", "last": "x"}) is None


# --------------------------------------------------------------------------- #
# enums, sensitivity and what reaches the fact pass
# --------------------------------------------------------------------------- #
def test_bad_enums_are_refused_one_window_at_a_time(tmp_path):
    wins = [_window(0), _window(1), _window(2)]
    _, summary, rows, *_, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 3,
        "gems": [_gem(0, wins[0], life_domain="vibes"),
                 _gem(1, wins[1], sensitivity="very"),
                 _gem(2, wins[2], speaker_guess="the dog")],
        "not_gems": []})
    assert respawn == {"000": [0, 1, 2]} and rows == []
    flat = json.dumps(summary["problems"])
    assert "domain" in flat and "sensitivity" in flat and "speaker" in flat


def test_a_bad_not_gem_reason_speaker_respawns_that_window(tmp_path):
    wins = [_window(0)]
    _, _, rows, *_, respawn = _run(tmp_path, wins, {
        "batch": "000", "windows": 1, "gems": [],
        "not_gems": [{"i": 0, "speaker_guess": "robot", "reason": "ad-read"}]})
    assert respawn == {"000": [0]} and rows == []


def test_withheld_tiers_derive_the_compatibility_flag(tmp_path):
    wins = [_window(0), _window(1), _window(2)]
    _, _, _, _, cands, _ = _run(tmp_path, wins, {
        "batch": "000", "windows": 3,
        "gems": [_gem(0, wins[0], sensitivity="lifestyle", life_domain="health"),
                 _gem(1, wins[1], sensitivity="clinical", life_domain="health"),
                 _gem(2, wins[2], sensitivity="children", life_domain="family")],
        "not_gems": []})
    got = {c["sensitivity"]: c["sensitive"] for c in cands}
    assert got == {"lifestyle": False, "clinical": True, "children": True}
    assert assemble_extracts.WITHHELD == {"clinical", "children", "location"}


def test_guest_gems_stay_in_the_record_but_never_reach_the_fact_pass(tmp_path):
    wins = [_window(0), _window(1)]
    _, summary, rows, gems, cands, _ = _run(tmp_path, wins, {
        "batch": "000", "windows": 2,
        "gems": [_gem(0, wins[0], speaker_guess="guest"),
                 _gem(1, wins[1], speaker_guess="unclear")],
        "not_gems": []})
    assert len(rows) == 2 and len(gems) == 1 and len(cands) == 1
    assert gems[0]["verdict"]["speaker_guess"] == "unclear"
    assert cands[0]["fact_id"] == "b000-001"
    assert cands[0]["video"] == wins[1]["id"] and cands[0]["start"] == wins[1]["start"]


def test_the_funnel_line_reports_the_stage(tmp_path):
    wins = [_window(0)]
    proc, *_ = _run(tmp_path, wins, {"batch": "000", "windows": 1,
                                     "gems": [_gem(0, wins[0])], "not_gems": []})
    line = next(x for x in proc.stderr.splitlines() if x.startswith("FUNNEL"))
    assert "stage=assemble" in line and "gems=1" in line and "elapsed_s=" in line


def test_append_replaces_the_same_rounds_earlier_rows_instead_of_stacking_them(tmp_path):
    """A round re-assembled after a subset re-spawn must not double its gems."""
    out = tmp_path / "out"
    wins1 = [_window(0)]
    b1, r1 = tmp_path / "batches1", tmp_path / "returns1"
    b1.mkdir()
    r1.mkdir()
    (b1 / "batch-000.json").write_text(json.dumps(wins1))
    (r1 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins1[0])], "not_gems": []}))
    subprocess.run([sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
                    "--batches", str(b1), "--returns", str(r1), "--out", str(out)],
                   capture_output=True, text=True, check=True)
    wins2 = [_window(1)]
    b2, r2 = tmp_path / "batches2", tmp_path / "returns2"
    b2.mkdir()
    r2.mkdir()
    (b2 / "batch-000.json").write_text(json.dumps(wins2))
    (r2 / "batch-000.extract.json").write_text(json.dumps({
        "batch": "000", "windows": 1, "gems": [_gem(0, wins2[0])], "not_gems": []}))
    for _ in range(2):        # the second --append is the re-assembly after a re-spawn
        proc = subprocess.run([sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
                               "--batches", str(b2), "--returns", str(r2), "--out", str(out),
                               "--append"], capture_output=True, text=True)
        assert proc.returncode == 0
    for name in ("classified.jsonl", "gems.jsonl", "candidates.jsonl"):
        rows = [json.loads(x) for x in (out / name).read_text().splitlines()]
        assert len(rows) == 2, name
    cands = [json.loads(x) for x in (out / "candidates.jsonl").read_text().splitlines()]
    assert [c["video"] for c in cands] == [wins1[0]["id"], wins2[0]["id"]]   # round 1 stays first


# --------------------------------------------------------------------------- #
# the coverage threshold: a few unjudged windows are accepted and reported
# --------------------------------------------------------------------------- #
def _run_many(tmp_path, n_windows: int, bad: list[int], *, extra_args=(), drop_file=False):
    wins = [_window(i) for i in range(n_windows)]
    batches = tmp_path / "batches"
    returns = tmp_path / "returns"
    batches.mkdir(exist_ok=True)
    returns.mkdir(exist_ok=True)
    (batches / "batch-000.json").write_text(json.dumps(wins))
    if drop_file:
        (batches / "batch-001.json").write_text(json.dumps([_window(0)]))
    gems = [_gem(i, wins[i]) for i in range(n_windows) if i not in bad]
    (returns / "batch-000.extract.json").write_text(json.dumps(
        {"batch": "000", "windows": n_windows, "gems": gems, "not_gems": []}))
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"),
         "--batches", str(batches), "--returns", str(returns),
         "--out", str(out), *extra_args], capture_output=True, text=True)
    summary = json.loads(proc.stdout)
    rows = [json.loads(x) for x in (out / "classified.jsonl").read_text().splitlines() if x.strip()]
    return proc, summary, rows


def test_unjudged_windows_above_the_threshold_exit_clean_and_are_reported(tmp_path):
    proc, summary, rows = _run_many(tmp_path, 100, bad=[3, 41, 99])
    assert proc.returncode == 0
    assert summary["unjudged_windows"] == 3 and summary["coverage"] == 0.97
    assert summary["respawn"] == {"000": [3, 41, 99]}      # still reachable for a re-judge
    assert {r["verdict"]["i"] for r in rows} == set(range(100)) - {3, 41, 99}
    line = next(x for x in proc.stderr.splitlines() if x.startswith("FUNNEL"))
    assert "unjudged=3" in line and "coverage=0.97" in line and "respawn_windows" not in line


def test_below_the_threshold_is_still_exit_3(tmp_path):
    proc, summary, _ = _run_many(tmp_path, 100, bad=list(range(6)))
    assert proc.returncode == 3 and summary["coverage"] == 0.94 and summary["exit"] == 3


def test_min_coverage_1_restores_every_window_or_nothing(tmp_path):
    proc, summary, _ = _run_many(tmp_path, 100, bad=[7], extra_args=("--min-coverage", "1.0"))
    assert proc.returncode == 3 and summary["unjudged_windows"] == 1


def test_a_batch_with_no_return_file_is_exit_3_whatever_the_coverage(tmp_path):
    proc, summary, _ = _run_many(tmp_path, 100, bad=[], drop_file=True)
    assert proc.returncode == 3
    assert summary["missing_batches"] == ["001"] and summary["coverage"] == 0.99


def test_an_out_of_range_threshold_is_a_usage_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "assemble_extracts.py"), "--batches", str(tmp_path),
         "--returns", str(tmp_path), "--out", str(tmp_path / "out"), "--min-coverage", "1.5"],
        capture_output=True, text=True)
    assert proc.returncode == 2 and "between 0 and 1" in proc.stderr


def test_a_span_with_added_punctuation_still_cuts_the_verbatim_text():
    from assemble_extracts import extract_span
    text = "so my dad ran a bakery in a small town in ohio and we all worked there"
    q = extract_span(text, {"first": "My dad ran a", "last": "town in Ohio."})
    assert q == "my dad ran a bakery in a small town in ohio"
    assert extract_span(text, {"first": "my dad, ran a", "last": "in ohio"}) == "my dad ran a bakery in a small town in ohio"
    assert extract_span(text, {"first": "town in ohio", "last": "my dad ran a"}) is None   # reversed
    assert extract_span(text, {"first": "my dad ran a", "last": "not in the text"}) is None


def test_non_latin_spans_match_by_unicode_words():
    from assemble_extracts import extract_span
    text = "и вот мой папа держал пекарню в маленьком городе под Москвой и мы все там работали"
    q = extract_span(text, {"first": "мой папа держал пекарню", "last": "городе под Москвой."})
    assert q == "мой папа держал пекарню в маленьком городе под Москвой"


def test_coverage_is_compared_unrounded(tmp_path):
    # 474/499 = 0.94990 rounds to 0.950 but is below the 0.95 threshold
    proc, summary, _ = _run_many(tmp_path, 499, bad=list(range(25)))
    assert summary["coverage"] == 0.95 and proc.returncode == 3
