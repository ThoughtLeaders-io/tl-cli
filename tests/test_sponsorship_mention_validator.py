"""Contract coverage for the `sponsorship-mention-validator` agent.

The agent is a prompt, so its behaviour cannot be unit-tested without a model
call; what CAN be pinned is the machine contract around it:

* the gold fixture the prompt is validated against is well-formed and covers
  every input item;
* the checker (`skills/tl/scripts/merge_mention_verdicts.py`) accepts the
  fixture's reference output and rejects each documented contract violation
  (missing/duplicate `m`, input field vocabulary leaking into the output,
  two quotes, over-long quote/note, extra keys, length mismatch, wrong `i`,
  `evidence_field` matching no row);
* the merge path reassembles fan-out batches and scores against gold.

Re-validating the prompt itself is a manual step: run the agent on
`tests/fixtures/sponsorship_mention_validator/input.json` and score with
`merge_mention_verdicts.py --gold gold.json` (see the PR for the recipe).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_AGENT = _ROOT / "agents" / "sponsorship-mention-validator.md"
_SCRIPT = _ROOT / "skills" / "tl" / "scripts" / "merge_mention_verdicts.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sponsorship_mention_validator"


def _load_module():
    spec = importlib.util.spec_from_file_location("merge_mention_verdicts", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mv():
    return _load_module()


@pytest.fixture(scope="module")
def items():
    return json.loads((_FIXTURES / "input.json").read_text())


@pytest.fixture(scope="module")
def gold():
    return json.loads((_FIXTURES / "gold.json").read_text())


@pytest.fixture(scope="module")
def reference():
    return json.loads((_FIXTURES / "reference_output.json").read_text())


# --- the agent file ---------------------------------------------------------

def test_agent_frontmatter_is_well_formed():
    text = _AGENT.read_text()
    assert text.startswith("---\n")
    front = text.split("---\n", 2)[1]
    assert "name: sponsorship-mention-validator" in front
    assert "model: sonnet" in front
    assert "tools: Read, Write" in front


def test_agent_prompt_states_the_contract_the_checker_enforces(mv):
    text = " ".join(_AGENT.read_text().split())
    for label in mv.LABELS:
        assert f"**{label}**" in text, f"label {label} is not defined in the prompt"
    for role in mv.ROLES:
        assert f"`{role}`" in text, f"role {role} is not defined in the prompt"
    assert f"at most {mv.QUOTE_MAX_WORDS} words" in text
    assert f"at most {mv.NOTE_MAX_WORDS} words" in text
    assert "write `description` in your output" in text
    assert "merge_mention_verdicts.py" in text


def test_agent_input_example_is_an_array():
    text = _AGENT.read_text()
    example = text.split("## Input", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    parsed = json.loads(example.replace("<video title>", "t"))
    assert isinstance(parsed, list) and parsed and parsed[0]["i"] == 0


# --- the fixture ------------------------------------------------------------

def test_fixture_input_is_valid(mv, items):
    assert mv.check_items(items) == []
    assert len(items) >= 20


def test_gold_covers_every_item_with_valid_labels(mv, items, gold):
    assert sorted(g["i"] for g in gold) == sorted(it["i"] for it in items)
    for g in gold:
        assert g["label"] in mv.LABELS, g
        for alt in g.get("also_ok", []):
            assert alt in mv.LABELS and alt != g["label"], g
        assert g["evidence_field"] in mv.OUTPUT_FIELDS, g


def test_fixture_covers_every_label_and_the_hard_cases(gold):
    labels = {g["label"] for g in gold}
    assert labels == {"paid_read", "sponsor_credit", "affiliate_or_link_only", "organic", "unclear"}
    whys = " ".join(g["why"].lower() for g in gold)
    for case in ("generic", "asr", "alias", "not sponsored", "own merch", "passing", "affiliate", "partnership"):
        assert case in whys, f"fixture lost its {case!r} case"


def test_reference_output_passes_the_contract_and_gold(mv, items, gold, reference):
    assert mv.check_verdicts(items, reference) == []
    score = mv.score_against_gold(reference, gold)
    assert score["misses"] == []
    assert score["evidence_mismatches"] == []
    assert score["correct"] == len(items)


# --- the checker rejects each contract violation ----------------------------

def _mutate(reference, i, fn):
    out = copy.deepcopy(reference)
    fn(next(v for v in out if v["i"] == i))
    return out


@pytest.mark.parametrize("name, i, mutate, expect", [
    ("drop a match row", 20, lambda v: v["matches"].pop(), "4 mentions"),
    ("duplicate m", 20, lambda v: v["matches"].__setitem__(1, dict(v["matches"][0])), "`m` must be 1"),
    ("input field vocabulary", 1, lambda v: v["matches"][0].__setitem__("field", "summary"), "write `description`"),
    ("field carried over", 16, lambda v: v["matches"][1].__setitem__("field", "transcript"), "found in 'description'"),
    ("two quotes", 0, lambda v: v["matches"][1].__setitem__("quote", "x"), "exactly one match row"),
    ("no quote", 0, lambda v: v["matches"][0].pop("quote"), "exactly one match row"),
    ("quote too long", 0, lambda v: v["matches"][0].__setitem__("quote", "w " * 16), "max 15"),
    ("note too long", 0, lambda v: v.__setitem__("note", "w " * 13), "max 12"),
    ("extra key", 0, lambda v: v.__setitem__("reason", "x"), "extra keys ['reason']"),
    ("extra match key", 0, lambda v: v["matches"][0].__setitem__("score", 1), "extra keys ['score']"),
    ("bad label", 0, lambda v: v.__setitem__("label", "sponsored"), "`label` must be one of"),
    ("bad confidence", 0, lambda v: v.__setitem__("confidence", "sure"), "`confidence` must be one of"),
    ("bad role", 0, lambda v: v["matches"][0].__setitem__("role", "ad"), "`role` must be one of"),
    ("evidence_field matches no row", 0, lambda v: v.__setitem__("evidence_field", "title"), "matches no row"),
    ("missing key", 0, lambda v: v.pop("note"), "missing keys ['note']"),
    ("empty quote", 0, lambda v: v["matches"][0].__setitem__("quote", ""), "`quote` must be non-empty text"),
    ("null quote", 0, lambda v: v["matches"][0].__setitem__("quote", None), "`quote` must be non-empty text"),
    ("object quote", 0, lambda v: v["matches"][0].__setitem__("quote", {"a": 1}), "`quote` must be non-empty text"),
    ("empty note", 0, lambda v: v.__setitem__("note", " "), "`note` must be non-empty text"),
    ("boolean m", 20, lambda v: v["matches"][1].__setitem__("m", True), "`m` must be 1"),
    ("float i", 0, lambda v: v.__setitem__("i", 0.0), "`i`=0.0 is not an input item"),
    ("boolean i", 1, lambda v: v.__setitem__("i", True), "`i`=True is not an input item"),
])
def test_checker_rejects(mv, items, reference, name, i, mutate, expect):
    errs = mv.check_verdicts(items, _mutate(reference, i, mutate))
    assert any(expect in e for e in errs), (name, errs)


def test_checker_rejects_length_and_index_drift(mv, items, reference):
    short = reference[:-1]
    errs = mv.check_verdicts(items, short)
    assert any(f"no verdict for input `i` [{reference[-1]['i']}]" in e for e in errs)

    wrong_i = copy.deepcopy(reference)
    wrong_i[0]["i"] = 999
    errs = mv.check_verdicts(items, wrong_i)
    assert any("`i`=999 is not an input item" in e for e in errs)

    dup = reference + [reference[0]]
    errs = mv.check_verdicts(items, dup)
    assert any("duplicate verdict for `i`=0" in e for e in errs)

    assert mv.check_verdicts(items, {"i": 0}) == ["output: must be a JSON array (no prose, no markdown fence)"]


def test_checker_rejects_bad_input(mv, items):
    assert mv.check_items({}) == ["input: must be a JSON array of items"]
    assert mv.check_items([]) == ["input: the candidate array is empty (nothing to judge)"]
    assert any("`i` must be an integer" in e for e in mv.check_items([{**items[0], "i": "0"}]))
    assert any("`i` must be an integer" in e for e in mv.check_items([{**items[0], "i": True}]))
    bad = copy.deepcopy(items)
    bad[0]["aliases"] = ["rivalco.com"]
    bad[1]["mentions"] = []
    bad[2]["i"] = bad[3]["i"]
    bad[4]["mentions"][0]["field"] = "caption"
    errs = mv.check_items(bad)
    assert any("must include the brand name" in e for e in errs)
    assert any("non-empty array" in e for e in errs)
    assert any("duplicate `i`" in e for e in errs)
    assert any("`field` must be one of" in e for e in errs)


# --- merge + gold scoring ---------------------------------------------------

def test_merge_reassembles_batches_and_flags_second_pass(mv, items, reference):
    half = len(reference) // 2
    b1, b2 = reference[:half], reference[half:]
    b2 = copy.deepcopy(b2)
    b2[0]["label"] = "unclear"
    b2[1]["confidence"] = "low"
    merged, errs = mv.merge_verdicts(items, [b2, b1])
    assert errs == []
    assert [v["i"] for v in merged] == sorted(it["i"] for it in items)
    already = mv.second_pass_items(reference)
    assert mv.second_pass_items(merged) == sorted({b2[0]["i"], b2[1]["i"], *already})


def test_merge_reports_overlap_and_gaps(mv, items, reference):
    merged, errs = mv.merge_verdicts(items, [reference[:5], reference[3:8]])
    assert any("already judged by an earlier batch" in e for e in errs)
    assert any("merged: no verdict for input `i`" in e for e in errs)
    assert len(merged) == 8


def test_gold_and_reference_agree_on_evidence_field(gold, reference):
    ref = {v["i"]: v for v in reference}
    assert [(g["i"], g["evidence_field"]) for g in gold] == [(g["i"], ref[g["i"]]["evidence_field"]) for g in gold]


def test_gold_scoring_honours_also_ok(mv, gold, reference):
    alt = copy.deepcopy(reference)
    flexible = next(g for g in gold if g.get("also_ok"))
    next(v for v in alt if v["i"] == flexible["i"])["label"] = flexible["also_ok"][0]
    assert mv.score_against_gold(alt, gold)["misses"] == []
    next(v for v in alt if v["i"] == 0)["label"] = "organic"
    score = mv.score_against_gold(alt, gold)
    assert [m["i"] for m in score["misses"]] == [0]
    assert score["correct"] == len(gold) - 1


def test_cli_merges_scores_and_exits_nonzero_on_violation(tmp_path, reference):
    half = len(reference) // 2
    (tmp_path / "b1.json").write_text(json.dumps(reference[:half]))
    (tmp_path / "b2.json").write_text(json.dumps(reference[half:]))
    merged = tmp_path / "merged.json"
    cmd = [sys.executable, str(_SCRIPT), "--input", str(_FIXTURES / "input.json"),
           "--verdicts", str(tmp_path / "b1.json"), str(tmp_path / "b2.json"),
           "--merged", str(merged), "--gold", str(_FIXTURES / "gold.json")]
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"gold: {len(reference)}/{len(reference)} correct" in run.stdout
    assert "contract: ok" in run.stdout
    assert len(json.loads(merged.read_text())) == len(reference)

    broken = copy.deepcopy(reference)
    broken[0]["matches"][0]["field"] = "summary"
    (tmp_path / "broken.json").write_text(json.dumps(broken))
    not_written = tmp_path / "not_written.json"
    run = subprocess.run([sys.executable, str(_SCRIPT), "--input", str(_FIXTURES / "input.json"),
                          "--verdicts", str(tmp_path / "broken.json"), "--merged", str(not_written)],
                         capture_output=True, text=True)
    assert run.returncode == 1
    assert "write `description`, not `summary`" in run.stderr
    assert "merged NOT written" in run.stderr and not not_written.exists()

    (tmp_path / "empty.json").write_text("[]")
    run = subprocess.run([sys.executable, str(_SCRIPT), "--input", str(tmp_path / "empty.json"),
                          "--verdicts", str(tmp_path / "empty.json")], capture_output=True, text=True)
    assert run.returncode == 1 and "candidate array is empty" in run.stderr
    run = subprocess.run([sys.executable, str(_SCRIPT), "--input", str(_FIXTURES / "input.json"),
                          "--verdicts", str(tmp_path / "broken.json"), "--lenient"], capture_output=True, text=True)
    assert run.returncode == 0
