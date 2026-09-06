"""merge_pass.py: the compact merge-pass contract.

The merge pass is the run's expensive stage, and the whole point of the
rewrite is that the model returns a few kilobytes of judgment while the script
materialises the ledger. These tests hold the two halves of that bargain: what
`prepare` is allowed to show an agent (no window text, no member list, no
cluster it must not judge), and what `expand` refuses to build from a decision
file that breaks the contract. No network, no model.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import ledger_io  # noqa: E402
import merge_pass  # noqa: E402

_MERGE = _SCRIPTS / "merge_pass.py"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _member(video, start, published="2024-01-01", ad_read=False, anchor=False):
    return {"video_id": video, "start": start, "published": published,
            "in_sponsor_read": ad_read, "host_anchor": anchor}


def _cluster(claim, *, domain="work", speaker="host", tier="none",
             conf="confirmed", quote="i moved to austin last spring honestly",
             video="v1", start=10, published="2024-01-01", members=None,
             format_hint=None, ad_read=False, anchor=False, title="A video",
             lang="en", text="a long window of surrounding chatter"):
    mems = members if members is not None else [
        _member(video, start, published, ad_read, anchor)]
    return {
        "window": {"id": f"1:{video}", "video_id": video, "title": title,
                   "published": published, "start": start, "language": lang,
                   "format_hint": format_hint, "in_sponsor_read": ad_read,
                   "host_anchor": anchor, "text": text, "rank_score": 4.0},
        "verdict": {"life_domain": domain, "speaker_guess": speaker,
                    "sensitivity": tier, "confidence": conf, "notable": claim,
                    "claim": claim, "quote": quote, "self_disclosure": True},
        "error": None,
        "occurrences": len(mems),
        "members": mems,
    }


def _write_clusters(tmp_path, clusters, name="gems-clustered.jsonl"):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        for c in clusters:
            fh.write(json.dumps(c) + "\n")
    return path


def _decisions(tmp_path, decisions, selected=None, name="merge-decisions.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"decisions": decisions,
                                "selected": selected or []}), encoding="utf-8")
    return path


def _keep_all(clustered, out, *, fmt="solo", channel="1", extra=None,
              selected=None, decisions=None, existing=None, state=None,
              flags=()):
    """Run expand with a keep decision for every cluster prepare would judge."""
    tmp = Path(out).parent
    if decisions is None:
        prep = _prepare(clustered, tmp / "prep", fmt=fmt, existing=existing,
                        state=state)
        judged = [json.loads(ln) for ln in
                  open(Path(prep["files"][0]), encoding="utf-8")]
        decisions = {row["c"]: {"action": "keep"} for row in judged}
        if extra:
            for key, value in extra.items():
                decisions[key] = value
    dpath = _decisions(tmp, decisions, selected)
    return _expand(clustered, dpath, out, fmt=fmt, channel=channel,
                   existing=existing, state=state, flags=flags)


def _run(args):
    return subprocess.run([sys.executable, str(_MERGE), *args],
                          capture_output=True, text=True)


def _prepare(clustered, out, *, fmt="solo", existing=None, state=None,
             shards=None):
    args = ["prepare", "--clustered", str(clustered), "--format", fmt,
            "--out", str(out)]
    if existing:
        args += ["--existing", str(existing)]
    if state:
        args += ["--state", str(state)]
    if shards:
        args += ["--shards", str(shards)]
    proc = _run(args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _expand(clustered, decisions, out, *, fmt="solo", channel="1",
            existing=None, state=None, flags=()):
    args = ["expand", "--clustered", str(clustered), "--format", fmt,
            "--channel", channel, "--out", str(out)]
    for d in ([decisions] if isinstance(decisions, (str, Path)) else decisions):
        args += ["--decisions", str(d)]
    if existing:
        args += ["--existing", str(existing)]
    if state:
        args += ["--state", str(state)]
    args += list(flags)
    return _run(args)


def _facts(path):
    _, facts = ledger_io.read_ledger(path)
    return {f["fact_id"]: f for f in facts}


# --------------------------------------------------------------------------- #
# prepare: compaction
# --------------------------------------------------------------------------- #
def test_prepare_line_is_compact_and_leaks_no_text_or_members(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("moved to Austin", members=[_member("v1", 10), _member("v2", 20)])])
    summary = _prepare(clustered, tmp_path / "out")
    rows = [json.loads(ln) for ln in
            open(tmp_path / "out" / "merge-input.jsonl", encoding="utf-8")]
    assert len(rows) == 1 and summary["judged"] == 1
    row = rows[0]
    assert row["c"] == "c001"
    assert set(row) == {"c", "domain", "speaker", "tier", "conf", "claim",
                        "quote", "title", "published", "videos", "occ",
                        "ad_read", "anchor", "format_hint", "lang", "notable"}
    assert row["videos"] == 2 and row["occ"] == 2
    blob = json.dumps(row)
    assert "surrounding chatter" not in blob      # no window text
    assert "video_id" not in blob                 # no member list


def test_prepare_numbers_clusters_in_file_order(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("first", video="v1"), _cluster("second", video="v2"),
        _cluster("third", video="v3")])
    _prepare(clustered, tmp_path / "out")
    rows = [json.loads(ln) for ln in
            open(tmp_path / "out" / "merge-input.jsonl", encoding="utf-8")]
    assert [r["c"] for r in rows] == ["c001", "c002", "c003"]
    assert [r["claim"] for r in rows] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# prepare: the deterministic drops
# --------------------------------------------------------------------------- #
def test_guest_is_auto_dropped_on_every_format(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("guest thing", speaker="guest")])
    for fmt in ("solo", "interview", "multi_host", "faceless_scripted"):
        summary = _prepare(clustered, tmp_path / fmt, fmt=fmt)
        assert summary["auto_dropped"] == 1 and summary["judged"] == 0


def test_unclear_drops_on_shared_voice_and_publishes_on_solo(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("unclear thing", speaker="unclear")])
    assert _prepare(clustered, tmp_path / "i", fmt="interview")["auto_dropped"] == 1
    assert _prepare(clustered, tmp_path / "m", fmt="multi_host")["auto_dropped"] == 1
    assert _prepare(clustered, tmp_path / "s", fmt="solo")["judged"] == 1
    assert _prepare(clustered, tmp_path / "f",
                    fmt="faceless_scripted")["judged"] == 1


def test_narration_publishes_on_a_single_voice_format(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("narrated", speaker="narration")])
    assert _prepare(clustered, tmp_path / "s", fmt="solo")["judged"] == 1


def test_cohost_never_publishes_as_the_host(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("cohost thing", speaker="cohost")])
    for fmt in ("solo", "interview", "multi_host"):
        assert _prepare(clustered, tmp_path / fmt, fmt=fmt)["auto_dropped"] == 1


def test_window_format_hint_overrides_the_channel_format(tmp_path):
    """A solo channel's one interview upload is judged as an interview."""
    clustered = _write_clusters(tmp_path, [
        _cluster("unclear on a collab", speaker="unclear",
                 format_hint="interview_or_collab"),
        _cluster("unclear on a reaction", speaker="unclear", video="v2",
                 format_hint="reaction"),
        _cluster("unclear on a normal upload", speaker="unclear", video="v3")])
    summary = _prepare(clustered, tmp_path / "out", fmt="solo")
    assert summary["auto_dropped"] == 2 and summary["judged"] == 1


def test_auto_dropped_clusters_never_reach_the_agent(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("kept", video="v1"),
        _cluster("guest", video="v2", speaker="guest")])
    _prepare(clustered, tmp_path / "out")
    rows = [json.loads(ln) for ln in
            open(tmp_path / "out" / "merge-input.jsonl", encoding="utf-8")]
    assert [r["c"] for r in rows] == ["c001"]


# --------------------------------------------------------------------------- #
# prepare: sharding
# --------------------------------------------------------------------------- #
def test_shards_never_split_a_life_domain(tmp_path):
    clusters = []
    for i, domain in enumerate(["work"] * 5 + ["pets"] * 3 + ["home"] * 2):
        clusters.append(_cluster(f"claim {i}", domain=domain, video=f"v{i}"))
    clustered = _write_clusters(tmp_path, clusters)
    summary = _prepare(clustered, tmp_path / "out", shards=3)
    assert len(summary["files"]) == 3
    seen = {}
    for n, path in enumerate(summary["files"]):
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            assert seen.setdefault(row["domain"], n) == n
    assert set(seen) == {"work", "pets", "home"}
    total = sum(sum(1 for _ in open(p, encoding="utf-8")) for p in summary["files"])
    assert total == 10


# --------------------------------------------------------------------------- #
# expand: contract validation
# --------------------------------------------------------------------------- #
def _violations(proc):
    assert proc.returncode == 3, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["violations"]


def test_missing_decision_exits_3_with_the_id(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "c002" in v and "missing decision" in v["c002"][0]


def test_unknown_id_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep"},
                                  "c999": {"action": "drop", "reason": "?"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "c999" in v and "unknown id" in v["c999"][0]


def test_fold_across_domains_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("work thing", domain="work", video="v1"),
        _cluster("pet thing", domain="pets", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep"},
                                  "c002": {"action": "fold", "target": "c001"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "fold across domains" in v["c002"][0]


def test_fold_into_a_cluster_that_is_not_kept_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "drop", "reason": "x"},
                                  "c002": {"action": "fold", "target": "c001"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "not a kept cluster" in v["c002"][0]


def test_fold_cycle_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "fold", "target": "c002"},
                                  "c002": {"action": "fold", "target": "c001"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert any("cycle" in why for whys in v.values() for why in whys)


def test_unknown_fact_target_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _decisions(tmp_path, {"c001": {"action": "fold", "target": "f404"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "not in --existing" in v["c001"][0]


def test_bad_enums_are_violations(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2"),
        _cluster("three", video="v3")])
    dpath = _decisions(tmp_path, {
        "c001": {"action": "keep", "tier": "medical"},
        "c002": {"action": "keep", "confidence": "probably"},
        "c003": {"action": "shred"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "tier must be" in v["c001"][0]
    assert "confidence must be" in v["c002"][0]
    assert "action must be" in v["c003"][0]


def test_a_narrowed_claim_may_not_invent_a_number(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("has a dog", quote="we adopted luna from the shelter", video="v1"),
        _cluster("moved house", quote="i moved to austin in 2019", video="v2")])
    dpath = _decisions(tmp_path, {
        "c001": {"action": "keep", "claim": "has 3 dogs"},
        "c002": {"action": "keep", "claim": "moved to Austin in 2019"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "c001" in v and "3" in v["c001"][0]
    assert "c002" not in v          # the number is in the quote


def test_an_empty_narrowed_claim_is_a_violation(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("has a dog")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep", "claim": "   "}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "empty" in v["c001"][0]


def test_fallback_original_publishes_the_cluster_claim_and_reports_it(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("has a dog", quote="we adopted luna from the shelter")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep", "claim": "has 3 dogs"}})
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out, flags=("--fallback-original",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout)
    assert [f["c"] for f in summary["claim_fallbacks"]] == ["c001"]
    assert _facts(out)["f001"]["claim"] == "has a dog"


def test_a_patch_decisions_file_overrides_the_first(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("has a dog", quote="we adopted luna from the shelter")])
    first = _decisions(tmp_path, {"c001": {"action": "keep", "claim": "has 3 dogs"}},
                       name="r1.json")
    patch = _decisions(tmp_path, {"c001": {"action": "keep",
                                           "claim": "adopted a dog named Luna"}},
                       name="r2.json")
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, [first, patch], out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _facts(out)["f001"]["claim"] == "adopted a dog named Luna"


# --------------------------------------------------------------------------- #
# expand: building the facts
# --------------------------------------------------------------------------- #
def test_fact_fields_are_derived_from_the_cluster(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("moved to Austin", video="vid9", start=512,
                 published="2024-05-01",
                 members=[_member("vid9", 512), _member("vid7", 30)])])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, channel="48247")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fact = _facts(out)["f001"]
    assert fact["video"] == "48247:vid9"
    assert fact["url"] == "https://www.youtube.com/watch?v=vid9&t=512s"
    assert fact["provenance"] == "transcript"
    assert fact["recurrence"] == 2
    assert fact["published"] == "2024-05-01"
    assert fact["members"] == ["vid9:512", "vid7:30"]
    assert fact["superseded_by"] is None


def test_the_facts_file_has_no_meta_header(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    out = tmp_path / "facts.jsonl"
    _keep_all(clustered, out)
    meta, facts = ledger_io.read_ledger(out)
    assert meta is None and len(facts) == 1


def test_recurrence_counts_distinct_videos_over_folds(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("moved to Austin", video="v1",
                 members=[_member("v1", 10), _member("v2", 20)]),
        _cluster("relocated to Austin", video="v3",
                 members=[_member("v3", 30), _member("v1", 99)])])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out,
                     decisions={"c001": {"action": "keep"},
                                "c002": {"action": "fold", "target": "c001"}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert list(facts) == ["f001"]
    assert facts["f001"]["recurrence"] == 3      # v1, v2, v3 — v1 counted once
    assert json.loads(proc.stdout)["folded"] == 1


def test_sensitivity_override_sets_the_derived_boolean(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("mentions a diagnosis", tier="none")])
    out = tmp_path / "facts.jsonl"
    _keep_all(clustered, out,
              decisions={"c001": {"action": "keep", "tier": "clinical"}})
    fact = _facts(out)["f001"]
    assert fact["sensitivity"] == "clinical" and fact["sensitive"] is True


def test_gloss_travels_when_the_agent_gives_one(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("habla espanol", lang="es")])
    out = tmp_path / "facts.jsonl"
    _keep_all(clustered, out,
              decisions={"c001": {"action": "keep", "gloss": "he speaks Spanish"}})
    assert _facts(out)["f001"]["gloss"] == "he speaks Spanish"


# --------------------------------------------------------------------------- #
# expand: confidence
# --------------------------------------------------------------------------- #
def _confidence(tmp_path, cluster, fmt="solo", name="c"):
    d = tmp_path / name
    d.mkdir()
    clustered = _write_clusters(d, [cluster])
    out = d / "facts.jsonl"
    proc = _keep_all(clustered, out, fmt=fmt)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _facts(out)["f001"]["confidence"]


def test_solo_keeps_the_extractors_call(tmp_path):
    assert _confidence(tmp_path, _cluster("a", conf="confirmed"), name="a") == "confirmed"
    assert _confidence(tmp_path, _cluster("b", conf="likely"), name="b") == "unconfirmed"


def test_interview_without_an_anchor_never_confirms(tmp_path):
    assert _confidence(tmp_path, _cluster("a", conf="confirmed"),
                       fmt="interview", name="a") == "unconfirmed"
    assert _confidence(tmp_path, _cluster("b", conf="confirmed", anchor=True),
                       fmt="interview", name="b") == "confirmed"


def test_multi_host_recurrence_alone_never_confirms(tmp_path):
    cluster = _cluster("recurring", conf="confirmed",
                       members=[_member("v1", 1), _member("v2", 2),
                                _member("v3", 3)])
    assert _confidence(tmp_path, cluster, fmt="multi_host") == "unconfirmed"


def test_an_ad_read_only_cluster_caps_at_unconfirmed(tmp_path):
    capped = _cluster("mentions a trip", conf="confirmed", ad_read=True,
                      members=[_member("v1", 1, ad_read=True),
                               _member("v2", 2, ad_read=True)])
    assert _confidence(tmp_path, capped, name="a") == "unconfirmed"
    mixed = _cluster("mentions a trip", conf="confirmed",
                     members=[_member("v1", 1, ad_read=True),
                              _member("v2", 2, ad_read=False)])
    assert _confidence(tmp_path, mixed, name="b") == "confirmed"


def test_an_agent_override_cannot_beat_the_ad_read_cap(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    clustered = _write_clusters(d, [
        _cluster("ad read", conf="confirmed", ad_read=True,
                 members=[_member("v1", 1, ad_read=True)])])
    out = d / "facts.jsonl"
    _keep_all(clustered, out,
              decisions={"c001": {"action": "keep", "confidence": "confirmed"}})
    assert _facts(out)["f001"]["confidence"] == "unconfirmed"


def test_unclear_on_solo_caps_at_unconfirmed(tmp_path):
    assert _confidence(tmp_path, _cluster("a", speaker="unclear",
                                          conf="confirmed")) == "unconfirmed"


def test_an_agent_override_wins_where_no_cap_applies(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    clustered = _write_clusters(d, [_cluster("a", conf="confirmed")])
    out = d / "facts.jsonl"
    _keep_all(clustered, out,
              decisions={"c001": {"action": "keep", "confidence": "unconfirmed"}})
    assert _facts(out)["f001"]["confidence"] == "unconfirmed"


# --------------------------------------------------------------------------- #
# expand: existing ledgers — ids, supersession, selection
# --------------------------------------------------------------------------- #
def _existing(tmp_path, facts, meta=True):
    path = tmp_path / "31792-facts.jsonl"
    header = ({"schema": "tl-creator-meta/v2", "channel_id": 31792}
              if meta else None)
    ledger_io.write_ledger(path, header, facts)
    return path


def _fact(fact_id, *, claim="an older fact", domain="work", recurrence=1,
          confidence="confirmed", members=None, video="old1", start=5):
    return {"fact_id": fact_id, "claim": claim, "domain": domain,
            "provenance": "transcript", "quote": "an older quote",
            "video": f"1:{video}", "start": start,
            "url": f"https://www.youtube.com/watch?v={video}&t={start}s",
            "published": "2020-01-01", "recurrence": recurrence,
            "confidence": confidence, "sensitivity": "none", "sensitive": False,
            "superseded_by": None, "selected": False,
            "members": members if members is not None else [f"{video}:{start}"]}


def test_fact_ids_continue_after_the_existing_maximum(tmp_path):
    existing = _existing(tmp_path, [_fact("f001"), _fact("f007", video="old7")])
    clustered = _write_clusters(tmp_path, [_cluster("new thing", video="v9")])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, existing=existing)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert set(facts) == {"f001", "f007", "f008"}
    assert facts["f008"]["claim"] == "new thing"


def test_superseding_an_existing_fact_marks_it_and_keeps_it(tmp_path):
    existing = _existing(tmp_path, [_fact("f003", claim="lives in LA")])
    clustered = _write_clusters(tmp_path, [_cluster("moved to Austin", video="v9")])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, existing=existing,
                     decisions={"c001": {"action": "keep", "supersedes": "f003"}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert facts["f003"]["superseded_by"] == "f004"
    assert facts["f003"]["selected"] is False
    assert facts["f004"]["claim"] == "moved to Austin"


def test_selected_fills_to_twenty_across_the_whole_active_ledger(tmp_path):
    existing = _existing(tmp_path, [_fact(f"f{i:03d}", video=f"o{i}", recurrence=9)
                                    for i in range(1, 16)])
    clustered = _write_clusters(tmp_path, [
        _cluster(f"new {i}", video=f"v{i}") for i in range(10)])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, existing=existing, selected=["c001"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert len(facts) == 25
    chosen = [f for f in facts.values() if f["selected"]]
    assert len(chosen) == 20
    assert facts["f016"]["selected"] is True      # the agent's own pick, c001


def test_selected_is_trimmed_to_twenty_by_confidence_then_recurrence(tmp_path):
    clusters = []
    for i in range(25):
        clusters.append(_cluster(f"claim {i}", video=f"v{i}",
                                 conf="confirmed" if i < 5 else "likely",
                                 members=[_member(f"v{i}", j) for j in range(i + 1)]))
    clustered = _write_clusters(tmp_path, clusters)
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out,
                     selected=[f"c{i:03d}" for i in range(1, 26)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    chosen = {k for k, f in facts.items() if f["selected"]}
    assert len(chosen) == 20
    # the five confirmed facts survive any trim
    assert {"f001", "f002", "f003", "f004", "f005"} <= chosen


def test_unknown_selected_ids_are_ignored_not_fatal(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, selected=["c001", "c999", "f404"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(json.loads(proc.stdout)["selected_ignored"]) == ["c999", "f404"]


# --------------------------------------------------------------------------- #
# refresh: the state file decides who gets re-judged
# --------------------------------------------------------------------------- #
def test_state_round_trip_additive_rejudge_dropped_and_new(tmp_path):
    round1 = _write_clusters(tmp_path, [
        _cluster("moved to Austin", video="v1", members=[_member("v1", 10)]),
        _cluster("has a rescue dog", domain="pets", video="v2",
                 members=[_member("v2", 20)]),
        _cluster("something wrong", video="v3", members=[_member("v3", 30)])])
    out = tmp_path / "facts.jsonl"
    state = tmp_path / "merge-state.json"
    proc = _keep_all(round1, out, state=state,
                     decisions={"c001": {"action": "keep"},
                                "c002": {"action": "keep"},
                                "c003": {"action": "drop",
                                         "reason": "claim over-reaches"}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert set(facts) == {"f001", "f002"}
    members = json.loads(state.read_text())["members"]
    assert members["v1:10"] == {"fact": "f001"}
    assert members["v3:30"]["dropped"] == "claim over-reaches"

    # round 2: c001 grew a member (additive), c002's members now span two
    # existing facts (re-judge), c003 is unchanged (stays dropped), c004 is new
    round2 = _write_clusters(tmp_path, [
        _cluster("moved to Austin", video="v1",
                 members=[_member("v1", 10), _member("v4", 40)]),
        _cluster("has a rescue dog", domain="pets", video="v2",
                 members=[_member("v2", 20), _member("v1", 10)]),
        _cluster("something wrong", video="v3", members=[_member("v3", 30)]),
        _cluster("bought a house", domain="home", video="v5",
                 members=[_member("v5", 50)])],
        name="round2.jsonl")
    summary = _prepare(round2, tmp_path / "p2", existing=out, state=state)
    assert summary["additive"] == 1
    assert summary["judged"] == 2 and summary["rejudge"] == 1
    assert summary["carry_dropped"] == 1
    rows = {r["c"]: r for r in
            (json.loads(ln) for ln in
             open(tmp_path / "p2" / "merge-input.jsonl", encoding="utf-8"))}
    assert set(rows) == {"c002", "c004"}
    assert rows["c002"]["known"] == ["f002", "f001"]
    existing_rows = {r["f"]: r for r in
                     (json.loads(ln) for ln in
                      open(tmp_path / "p2" / "merge-existing.jsonl", encoding="utf-8"))}
    assert existing_rows["f002"]["rejudge"] is True
    assert existing_rows["f001"]["rejudge"] is True

    # expand reads the round's inherited state from --state and rewrites it
    out2 = tmp_path / "facts2.jsonl"
    proc2 = _keep_all(round2, out2, existing=out, state=state,
                      decisions={"c002": {"action": "keep"},
                                 "c004": {"action": "keep"}})
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    facts2 = _facts(out2)
    assert facts2["f001"]["recurrence"] == 2          # additive: v1 + v4
    assert sorted(facts2["f001"]["members"]) == ["v1:10", "v4:40"]
    assert facts2["f003"]["claim"] == "bought a house"
    assert json.loads(proc2.stdout)["additive"] == 1
    members2 = json.loads(state.read_text())["members"]
    assert members2["v4:40"] == {"fact": "f001"}
    assert members2["v5:50"] == {"fact": "f003"}
    assert members2["v3:30"]["dropped"] == "claim over-reaches"


def test_a_cluster_whose_members_were_all_dropped_stays_dropped(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"members": {"v1:10": {"dropped": "over-reaching"}}}))
    existing = _existing(tmp_path, [_fact("f001")])
    clustered = _write_clusters(tmp_path, [
        _cluster("over-reaching", video="v1", members=[_member("v1", 10)])])
    summary = _prepare(clustered, tmp_path / "p", existing=existing, state=state)
    assert summary["carry_dropped"] == 1 and summary["judged"] == 0


def test_a_fresh_build_ignores_a_stray_state_file(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "merge-state.json").write_text(
        json.dumps({"members": {"v1:10": {"dropped": "stale"}}}))
    clustered = _write_clusters(tmp_path, [
        _cluster("a claim", video="v1", members=[_member("v1", 10)])])
    assert _prepare(clustered, tmp_path / "out")["judged"] == 1


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
def test_end_to_end_prepare_judge_expand(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("graduated from medical school", video="v1",
                 quote="i graduated from medical school in 2018",
                 members=[_member("v1", 10), _member("v2", 20)]),
        _cluster("qualified as a doctor", video="v3",
                 quote="i qualified as a doctor and worked in the nhs",
                 members=[_member("v3", 30)]),
        _cluster("has a rescue dog", domain="pets", video="v4",
                 members=[_member("v4", 40)]),
        _cluster("a guest said this", video="v5", speaker="guest",
                 members=[_member("v5", 50)]),
        _cluster("claim over-reaches its quote", video="v6",
                 members=[_member("v6", 60)])])
    out_dir = tmp_path / "corpus"
    summary = _prepare(clustered, out_dir, fmt="solo")
    assert summary == {**summary, "clusters": 5, "judged": 4, "auto_dropped": 1}

    dpath = _decisions(tmp_path, {
        "c001": {"action": "keep", "claim": "graduated from medical school in 2018"},
        "c002": {"action": "fold", "target": "c001"},
        "c003": {"action": "keep", "tier": "none"},
        "c005": {"action": "drop", "reason": "claim asserts more than the quote"}},
        selected=["c001"])
    out = out_dir / "facts.jsonl"
    proc = _expand(clustered, dpath, out, fmt="solo", channel="31792")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["facts"] == 2 and result["folded"] == 1
    assert result["dropped"] == 1 and result["auto_dropped"] == 1
    assert "FUNNEL stage=merge" in proc.stderr

    facts = _facts(out)
    assert facts["f001"]["recurrence"] == 3
    assert facts["f001"]["claim"] == "graduated from medical school in 2018"
    assert facts["f001"]["selected"] is True
    assert facts["f002"]["domain"] == "pets"
    state = json.loads((out_dir / "merge-state.json").read_text())["members"]
    assert state["v3:30"] == {"folded": "f001"}
    assert state["v5:50"]["dropped"] == "speaker guest"
    assert state["v6:60"]["dropped"] == "claim asserts more than the quote"


def test_module_helpers_are_importable_and_pure():
    line = _cluster("a", members=[_member("v1", 1), _member("v2", 2)])
    assert merge_pass.member_keys(line) == ["v1:1", "v2:2"]
    assert merge_pass.distinct_videos(line) == ["v1", "v2"]
    assert merge_pass.cid(0) == "c001" and merge_pass.cid(99) == "c100"


def test_an_existing_fact_split_across_clusters_is_emitted_once(tmp_path):
    """Round 1 built one fact from a two-member cluster; round 2's clustering
    keeps those members apart. Both clusters are additive to the same fact,
    which must appear once with the pooled evidence, never once per cluster."""
    round1 = _write_clusters(tmp_path, [
        _cluster("owns a tea company", video="v1",
                 members=[_member("v1", 10), _member("v2", 20)])])
    out = tmp_path / "facts.jsonl"
    state = tmp_path / "merge-state.json"
    proc = _keep_all(round1, out, state=state, decisions={"c001": {"action": "keep"}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    round2 = _write_clusters(tmp_path, [
        _cluster("owns a tea company", video="v1", members=[_member("v1", 10)]),
        _cluster("owns a tea company", video="v2",
                 members=[_member("v2", 20), _member("v3", 30)])],
        name="round2.jsonl")
    summary = _prepare(round2, tmp_path / "p2", existing=out, state=state)
    assert summary["additive"] == 2 and summary["judged"] == 0
    out2 = tmp_path / "facts2.jsonl"
    proc2 = _keep_all(round2, out2, existing=out, state=state, decisions={})
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    lines = [json.loads(ln) for ln in open(out2, encoding="utf-8") if ln.strip()]
    assert [f["fact_id"] for f in lines] == ["f001"]
    assert lines[0]["recurrence"] == 3
    assert sorted(lines[0]["members"]) == ["v1:10", "v2:20", "v3:30"]
    assert json.loads(proc2.stdout)["facts"] == 1


# --------------------------------------------------------------------------- #
# the identity lane: social/web facts
# --------------------------------------------------------------------------- #
def _envelope(tmp_path, decisions, *, selected=None, facts=None,
              name="merge-decisions.json"):
    path = tmp_path / name
    body = {"decisions": decisions, "selected": selected or []}
    if facts is not None:
        body["facts"] = facts
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _identity(**over):
    rec = {"ref": "s1", "provenance": "social",
           "claim": "runs a pottery studio in Lisbon", "domain": "work",
           "sensitivity": "none", "source_url": "https://instagram.com/example",
           "seen_date": "2026-09-02"}
    rec.update(over)
    return rec


def test_identity_facts_are_numbered_after_the_clusters(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"},
                                 "c002": {"action": "keep"}},
                      facts=[_identity(),
                             _identity(ref="w1", provenance="web",
                                       claim="was interviewed by a magazine",
                                       source_url="https://example.com/piece")])
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["identity_facts"] == 2
    facts = _facts(out)
    assert sorted(facts) == ["f001", "f002", "f003", "f004"]
    social = facts["f003"]
    assert social["provenance"] == "social"
    assert social["source_url"] == "https://instagram.com/example"
    assert social["seen_date"] == "2026-09-02"
    assert social["confidence"] == "unconfirmed"      # a lane alone never confirms
    assert social["recurrence"] == 1 and social["members"] == []
    assert not {"quote", "video", "start", "url"} & set(social)
    assert facts["f004"]["provenance"] == "web"


def test_an_identity_fact_derives_the_sensitive_boolean_and_is_selectable(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}},
                      selected=["s1"],
                      facts=[_identity(sensitivity="clinical", domain="health")])
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fact = _facts(out)["f002"]
    assert fact["sensitivity"] == "clinical" and fact["sensitive"] is True
    assert fact["selected"] is True
    assert json.loads(proc.stdout)["selected_ignored"] == []


def test_corroboration_lifts_both_lanes_to_confirmed(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("runs a pottery studio", conf="likely", video="v1")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}},
                      facts=[_identity(corroborates="c001")])
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert facts["f001"]["confidence"] == "confirmed"   # was `likely`
    assert facts["f002"]["confidence"] == "confirmed"
    assert json.loads(proc.stdout)["corroborated"] == [["f002", "f001"]]


def test_corroboration_may_name_an_existing_fact(tmp_path):
    existing = _existing(tmp_path, [_fact("f001", confidence="unconfirmed")])
    clustered = _write_clusters(tmp_path, [_cluster("new", video="v9")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}},
                      facts=[_identity(corroborates="f001")])
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out, existing=existing)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert facts["f001"]["confidence"] == "confirmed"
    assert facts["f003"]["confidence"] == "confirmed"


def test_a_bad_identity_record_exits_3(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}}, facts=[
        _identity(ref="s1", provenance="transcript"),
        _identity(ref="s2", source_url=""),
        _identity(ref="s3", quote="i said this on camera"),
        _identity(ref="s4", domain="gardening"),
        _identity(ref="s5", corroborates="c404"),
        {"provenance": "web", "claim": "x", "domain": "work",
         "sensitivity": "none", "source_url": "https://e.com"}])
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "provenance must be" in v["s1"][0]
    assert "source_url is required" in v["s2"][0]
    assert "carries no quote" in v["s3"][0]
    assert "domain must be" in v["s4"][0]
    assert "corroborates target c404" in v["s5"][0]
    assert "seen_date is required" in v["facts[5]"][0]


def test_existing_identity_facts_survive_a_refresh(tmp_path):
    social = {"fact_id": "f002", "claim": "runs a pottery studio",
              "domain": "work", "provenance": "social",
              "source_url": "https://instagram.com/example",
              "seen_date": "2026-08-01", "recurrence": 1,
              "confidence": "unconfirmed", "sensitivity": "none",
              "sensitive": False, "superseded_by": None, "selected": False,
              "members": []}
    existing = _existing(tmp_path, [_fact("f001"), social])
    clustered = _write_clusters(tmp_path, [_cluster("new thing", video="v9")])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, existing=existing)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    assert facts["f002"]["provenance"] == "social"
    assert facts["f002"]["source_url"] == "https://instagram.com/example"
    assert facts["f003"]["claim"] == "new thing"


# --------------------------------------------------------------------------- #
# a dropped member makes a cluster a re-judge, never additive
# --------------------------------------------------------------------------- #
def test_a_dropped_member_beside_a_known_one_forces_a_rejudge(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"members": {
        "v1:10": {"fact": "f001"},
        "v2:20": {"dropped": "claim asserts more than the quote"}}}))
    existing = _existing(tmp_path, [_fact("f001")])
    clustered = _write_clusters(tmp_path, [
        _cluster("moved to Austin", video="v1",
                 members=[_member("v1", 10), _member("v2", 20)])])
    summary = _prepare(clustered, tmp_path / "p", existing=existing, state=state)
    assert summary["additive"] == 0
    assert summary["judged"] == 1 and summary["rejudge"] == 1
    row = json.loads((tmp_path / "p" / "merge-input.jsonl")
                     .read_text(encoding="utf-8").strip())
    assert row["known"] == ["f001"] and row["dropped_members"] == 1


# --------------------------------------------------------------------------- #
# a re-judged cluster reconciles the fact ids it did not keep
# --------------------------------------------------------------------------- #
def test_a_rejudged_cluster_retires_the_other_facts_it_inherited(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"members": {"v1:10": {"fact": "f001"},
                                             "v2:20": {"fact": "f002"}}}))
    existing = _existing(tmp_path, [
        _fact("f001", claim="works as a doctor", video="v1", start=10,
              members=["v1:10"]),
        _fact("f002", claim="worked in the NHS", video="v2", start=20,
              members=["v2:20"])])
    clustered = _write_clusters(tmp_path, [
        _cluster("worked as an NHS doctor", video="v1",
                 members=[_member("v1", 10), _member("v2", 20)])])
    out = tmp_path / "facts.jsonl"
    proc = _keep_all(clustered, out, existing=existing, state=state,
                     decisions={"c001": {"action": "keep"}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["reconciled"] == {"f002": "f001"}
    facts = _facts(out)
    assert facts["f001"]["claim"] == "worked as an NHS doctor"
    assert facts["f001"]["recurrence"] == 2            # evidence pooled
    assert sorted(facts["f001"]["members"]) == ["v1:10", "v2:20"]
    assert facts["f002"]["superseded_by"] == "f001"    # history, not a duplicate
    active = [f for f in facts.values() if not f["superseded_by"]]
    assert [f["fact_id"] for f in active] == ["f001"]
    assert facts["f002"]["selected"] is False


# --------------------------------------------------------------------------- #
# the tripwires the review found
# --------------------------------------------------------------------------- #
def test_a_narrowed_claim_number_is_compared_as_a_token(tmp_path):
    """"has 3 dogs" must not pass on a quote that says "13 dogs"."""
    clustered = _write_clusters(tmp_path, [
        _cluster("has dogs", quote="we have 13 dogs at the moment", video="v1"),
        _cluster("has dogs too", quote="we have 3 dogs at the moment", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep", "claim": "has 3 dogs"},
                                  "c002": {"action": "keep", "claim": "has 3 dogs"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "c001" in v and "3" in v["c001"][0]
    assert "c002" not in v


def test_a_fold_into_an_existing_fact_must_share_its_domain(tmp_path):
    existing = _existing(tmp_path, [_fact("f001", domain="work")])
    clustered = _write_clusters(tmp_path, [
        _cluster("a pet thing", domain="pets", video="v1"),
        _cluster("a work thing", domain="work", video="v2")])
    dpath = _decisions(tmp_path, {"c001": {"action": "fold", "target": "f001"},
                                  "c002": {"action": "fold", "target": "f001"}})
    proc = _expand(clustered, dpath, tmp_path / "facts.jsonl", existing=existing)
    v = _violations(proc)
    assert "fold across domains (pets -> work)" in v["c001"][0]
    assert "c002" not in v


def test_superseding_yourself_after_id_resolution_exits_3(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"members": {"v1:10": {"fact": "f003"},
                                             "v2:20": {"fact": "f004"}}}))
    existing = _existing(tmp_path, [_fact("f003"), _fact("f004", video="v2")])
    clustered = _write_clusters(tmp_path, [
        _cluster("one claim", video="v1",
                 members=[_member("v1", 10), _member("v2", 20)])])
    dpath = _decisions(tmp_path, {"c001": {"action": "keep", "supersedes": "f003"}})
    proc = _expand(clustered, dpath, tmp_path / "facts.jsonl", existing=existing,
                   state=state)
    v = _violations(proc)
    assert "resolves to the fact itself" in v["c001"][0]


def test_a_supersession_cycle_exits_3(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    dpath = _decisions(tmp_path, {
        "c001": {"action": "keep", "supersedes": "c002"},
        "c002": {"action": "keep", "supersedes": "c001"}})
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert any("cycle" in why for whys in v.values() for why in whys)


# --------------------------------------------------------------------------- #
# sharding: `selected` is unioned across shard files, never replaced
# --------------------------------------------------------------------------- #
def test_prepare_shards_pack_whole_domains(tmp_path):
    clusters = ([_cluster(f"work {i}", domain="work", video=f"w{i}")
                 for i in range(6)]
                + [_cluster(f"pets {i}", domain="pets", video=f"p{i}")
                   for i in range(2)])
    clustered = _write_clusters(tmp_path, clusters)
    prep = _prepare(clustered, tmp_path / "prep", shards=2)
    assert len(prep["files"]) == 2
    per_file = []
    for f in prep["files"]:
        rows = [json.loads(ln) for ln in open(f, encoding="utf-8")]
        per_file.append({r["domain"] for r in rows})
    # a domain never straddles two shards, or a fold could cross a file
    assert all(len(doms) >= 1 for doms in per_file)
    assert not (per_file[0] & per_file[1])


def test_selected_is_unioned_across_shard_decision_files(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", domain="work", video="v1"),
        _cluster("two", domain="pets", video="v2")])
    # one file per shard: each nominates only from the domain it saw
    a = _envelope(tmp_path, {"c001": {"action": "keep"}}, selected=["c001"],
                  name="shard-a.json")
    b = _envelope(tmp_path, {"c002": {"action": "keep"}}, selected=["c002"],
                  name="shard-b.json")
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, [a, b], out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    facts = _facts(out)
    # replacing rather than unioning would drop the first shard's pick
    assert {f["claim"] for f in facts.values() if f["selected"]} == {"one", "two"}


# --------------------------------------------------------------------------- #
# a patch file must say what it is patching
# --------------------------------------------------------------------------- #
def test_a_patch_that_changes_nothing_exits_3(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    good = _envelope(tmp_path, {"c001": {"action": "keep"}}, name="r1.json")
    noop = tmp_path / "patch.json"
    noop.write_text(json.dumps({"decisions": {}}), encoding="utf-8")
    v = _violations(_expand(clustered, [good, noop], tmp_path / "facts.jsonl"))
    assert "changes nothing" in v["_files"][0]


def test_an_empty_facts_list_never_silently_wipes_the_identity_lane(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    good = _envelope(tmp_path, {"c001": {"action": "keep"}},
                     facts=[_identity()], name="r1.json")
    wipe = tmp_path / "patch.json"
    wipe.write_text(json.dumps({"decisions": {}, "facts": []}), encoding="utf-8")
    v = _violations(_expand(clustered, [good, wipe], tmp_path / "facts.jsonl"))
    assert any("would drop the 1 identity-lane fact" in p for p in v["_files"])


def test_omitting_facts_leaves_the_earlier_lane_standing(tmp_path):
    clustered = _write_clusters(tmp_path, [
        _cluster("one", video="v1"), _cluster("two", video="v2")])
    r1 = _envelope(tmp_path, {"c001": {"action": "keep"}},
                   facts=[_identity()], name="r1.json")
    patch = _envelope(tmp_path, {"c002": {"action": "keep"}}, name="patch.json")
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, [r1, patch], out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["identity_facts"] == 1


# --------------------------------------------------------------------------- #
# identity-lane domains: near-misses are aliased and reported, never silent
# --------------------------------------------------------------------------- #
def test_near_miss_identity_domains_are_aliased_and_reported(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}}, facts=[
        _identity(ref="s1", domain="hobbies"),
        _identity(ref="s2", domain="identity"),
        _identity(ref="s3", domain="career")])
    out = tmp_path / "facts.jsonl"
    proc = _expand(clustered, dpath, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["identity_facts"] == 3
    assert summary["domain_aliases"] == ["s1: hobbies -> habits",
                                         "s2: identity -> other",
                                         "s3: career -> work"]
    # the correction is announced; a silent rewrite is how a lane stays wrong
    assert "aliased to the enum" in proc.stderr
    assert {f["domain"] for f in _facts(out).values()
            if f.get("source_url")} == {"habits", "other", "work"}


def test_an_unplaceable_identity_domain_still_exits_3(tmp_path):
    clustered = _write_clusters(tmp_path, [_cluster("one")])
    dpath = _envelope(tmp_path, {"c001": {"action": "keep"}},
                      facts=[_identity(ref="s1", domain="gardening")])
    v = _violations(_expand(clustered, dpath, tmp_path / "facts.jsonl"))
    assert "domain must be" in v["s1"][0]
