"""cluster_gems.py: the deterministic gem pre-clustering step.

The guarantees under test are the ones a false merge would silently break —
distinct claims stay distinct across domains, speakers and sensitivity calls,
singletons keep the same line shape as clusters, and the output does not depend
on the order the gems were read in. No network, no model.
"""

import json
import random
import subprocess
import sys
from pathlib import Path

_SCRIPTS = (Path(__file__).resolve().parents[1]
            / "skills" / "tl-creator-brief" / "scripts")
sys.path.insert(0, str(_SCRIPTS))
import cluster_gems  # noqa: E402


def _gem(video_id, *, notable, text, phrase=None, domain="tastes",
         speaker="host", sensitive=False, start=10, rank=4,
         published="2020-01-01"):
    return {
        "window": {"id": f"1:{video_id}", "video_id": video_id,
                   "start": start, "published": published, "text": text,
                   "recurring_phrase": phrase, "rank_score": rank},
        "verdict": {"self_disclosure": True, "life_domain": domain,
                    "speaker_guess": speaker, "sensitive": sensitive,
                    "notable": notable},
        "error": None,
    }


def _claims(gem_a, gem_b):
    return cluster_gems.cluster([gem_a, gem_b])


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #
def test_identical_recurring_phrase_merges():
    a = _gem("v1", notable="Favorite game is Bioshock",
             text="anyway bioshock is my favorite game of all time honestly",
             phrase="bioshock favorite game all")
    b = _gem("v2", notable="favorite game is Bioshock",
             text="totally different chatter here about a gym leader fight",
             phrase="bioshock favorite game all")
    assert [len(c) for c in _claims(a, b)] == [2]


def test_phrase_variants_of_one_claim_merge():
    """The scan tags the same claim with slightly different n-grams."""
    a = _gem("v1", notable="Favorite game is Bioshock",
             text="one lot of gameplay chatter", phrase="bioshock favorite game all")
    b = _gem("v2", notable="favorite game of all time is Bioshock",
             text="an entirely unrelated lot of chatter",
             phrase="bioshock favorite game ever")
    assert [len(c) for c in _claims(a, b)] == [2]


def test_near_identical_claims_merge_without_a_phrase():
    a = _gem("v1", notable="Has a cat named Leo", text="chatter one", phrase=None)
    b = _gem("v2", notable="has a cat named Leo", text="chatter two", phrase=None)
    assert [len(c) for c in _claims(a, b)] == [2]


# --------------------------------------------------------------------------- #
# conservatism — a false merge silently deletes a fact
# --------------------------------------------------------------------------- #
def test_shared_phrase_does_not_merge_unrelated_claims():
    """The recurring phrase belongs to the window, not to the claim."""
    a = _gem("v1", notable="Channel is nine years old today",
             text="chatter one", phrase="bioshock favorite game all")
    b = _gem("v2", notable="Opened the channel in 2011 with Skyrim videos",
             text="chatter two", phrase="bioshock favorite game all")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_different_domains_never_merge():
    a = _gem("v1", notable="Favorite food is anything his grandma makes",
             text="same words entirely", phrase="favorite food favorite food",
             domain="tastes")
    b = _gem("v2", notable="Favorite food is anything his grandma makes",
             text="same words entirely", phrase="favorite food favorite food",
             domain="family")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_different_speakers_never_merge():
    a = _gem("v1", notable="Has a cat named Leo", text="same words entirely",
             speaker="host")
    b = _gem("v2", notable="Has a cat named Leo", text="same words entirely",
             speaker="unclear")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_sensitive_never_merges_with_non_sensitive():
    a = _gem("v1", notable="Talks about his health condition",
             text="same words entirely", sensitive=True)
    b = _gem("v2", notable="Talks about his health condition",
             text="same words entirely", sensitive=False)
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_similar_frame_different_subject_does_not_merge():
    a = _gem("v1", notable="Favorite ice cream is mint",
             text="chatter one", phrase="what's favorite ice cream")
    b = _gem("v2", notable="Favorite movie is Saving Private Ryan",
             text="chatter two", phrase="what's favorite movie favorite")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_negated_claim_never_merges_with_the_positive_one():
    """The words are identical once stopwords go; the polarity is not."""
    a = _gem("v1", notable="Has children", text="same words entirely",
             domain="family")
    b = _gem("v2", notable="Does not have children", text="same words entirely",
             domain="family")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_contraction_negation_never_merges_with_the_positive_one():
    a = _gem("v1", notable="Owns a car", text="same words entirely")
    b = _gem("v2", notable="Doesn't own a car", text="same words entirely")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_same_frame_different_number_never_merges():
    a = _gem("v1", notable="Has 2 cats", text="same words entirely",
             domain="pets")
    b = _gem("v2", notable="Has 3 cats", text="same words entirely",
             domain="pets")
    assert [len(c) for c in _claims(a, b)] == [1, 1]


def test_same_claim_with_the_same_negation_and_number_still_merges():
    a = _gem("v1", notable="Does not have 2 cats", text="chatter one")
    b = _gem("v2", notable="does not have 2 cats", text="chatter two")
    assert [len(c) for c in _claims(a, b)] == [2]


def test_complete_linkage_blocks_chaining():
    """B matches A and C, but A and C disagree — no three-way chain."""
    a = _gem("v1", notable="Has a cat named Leo", text="cat chatter")
    b = _gem("v2", notable="Has a cat named Leo and a dog named Max",
             text="cat chatter")
    c = _gem("v3", notable="Has a dog named Max", text="dog chatter")
    sizes = sorted(len(g) for g in cluster_gems.cluster([a, b, c]))
    assert sizes == [1, 2] or sizes == [1, 1, 1]
    assert max(sizes) < 3


# --------------------------------------------------------------------------- #
# shape, ordering, determinism
# --------------------------------------------------------------------------- #
def test_singleton_passes_through_with_cluster_shape():
    gem = _gem("v1", notable="has a brother", text="oh look at my brother")
    line = cluster_gems.build_line([gem])
    assert line["occurrences"] == 1
    assert line["members"] == [{"video_id": "v1", "start": 10,
                                "published": "2020-01-01",
                                "in_sponsor_read": False, "host_anchor": False}]
    # the full representative gem survives untouched alongside the two new keys
    assert line["window"] == gem["window"]
    assert line["verdict"] == gem["verdict"]


def test_representative_is_the_highest_information_member():
    short = _gem("v1", notable="Favorite game is Bioshock", text="bioshock best",
                 phrase="bioshock favorite game all", rank=4)
    long = _gem("v2", notable="Favorite game is Bioshock",
                text="bioshock is far and away my favorite game of all time",
                phrase="bioshock favorite game all", rank=4)
    line = cluster_gems.build_line([short, long])
    assert line["window"]["video_id"] == "v2"
    assert line["occurrences"] == 2
    assert {m["video_id"] for m in line["members"]} == {"v1", "v2"}


def test_ranked_representative_beats_longer_text():
    weak = _gem("v1", notable="Favorite game is Bioshock",
                text="a much much longer window of low ranked chatter here",
                phrase="bioshock favorite game all", rank=2)
    strong = _gem("v2", notable="Favorite game is Bioshock", text="short",
                  phrase="bioshock favorite game all", rank=9)
    assert cluster_gems.build_line([weak, strong])["window"]["video_id"] == "v2"


def _fixture_gems():
    gems = [
        _gem(f"b{i}", notable="Favorite game is Bioshock",
             text=f"gameplay chatter number {i} about a gym battle",
             phrase="bioshock favorite game all", start=100 + i)
        for i in range(5)
    ]
    gems += [
        _gem("c1", notable="Has a cat named Leo", text="my cat leo again",
             domain="pets"),
        _gem("c2", notable="has a cat named Leo", text="leo the cat is here",
             domain="pets"),
        _gem("s1", notable="has a brother", text="oh look at my brother",
             domain="family"),
        _gem("s2", notable="Favorite movie is Saving Private Ryan",
             text="best war film ever made", phrase="movie saving private ryan"),
    ]
    return gems


def test_output_is_independent_of_input_order():
    gems = _fixture_gems()
    baseline = [cluster_gems.build_line(g) for g in cluster_gems.cluster(gems)]
    for seed in (1, 2, 3):
        shuffled = list(gems)
        random.Random(seed).shuffle(shuffled)
        again = [cluster_gems.build_line(g)
                 for g in cluster_gems.cluster(shuffled)]
        assert again == baseline


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
def test_run_over_a_gems_file(tmp_path):
    gems_path = tmp_path / "gems.jsonl"
    gems_path.write_text("".join(json.dumps(g) + "\n" for g in _fixture_gems()))

    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "cluster_gems.py"),
         "--in", str(gems_path)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    summary = json.loads(proc.stdout)
    assert summary["gems"] == 9
    assert summary["clusters"] == 4          # bioshock, cat, brother, ryan
    assert summary["merged"] == 5
    assert summary["largest_cluster"] == 5

    funnel = [ln for ln in proc.stderr.splitlines() if ln.startswith("FUNNEL")]
    assert len(funnel) == 1
    fields = dict(p.split("=", 1) for p in funnel[0].split()[1:])
    assert fields["stage"] == "cluster"
    assert (fields["gems"], fields["clusters"], fields["merged"]) == ("9", "4", "5")
    assert "elapsed_s" in fields

    out = tmp_path / "gems-clustered.jsonl"
    lines = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert len(lines) == 4
    # biggest recurrence first, and every line carries both new fields
    assert [ln["occurrences"] for ln in lines] == [5, 2, 1, 1]
    for ln in lines:
        assert len(ln["members"]) == ln["occurrences"]
        assert all(set(m) == {"video_id", "start", "published",
                              "in_sponsor_read", "host_anchor"}
                   for m in ln["members"])
        assert ln["window"] and ln["verdict"]

    # the slim view beside it: same lines, no window text, verdicts intact
    assert summary["slim_file"] == str(tmp_path / "gems-clustered.slim.jsonl")
    slim = [json.loads(ln) for ln in
            (tmp_path / "gems-clustered.slim.jsonl").read_text().splitlines()]
    assert len(slim) == 4
    for full, thin in zip(lines, slim):
        assert "text" not in thin["window"]
        assert thin["window"]["video_id"] == full["window"]["video_id"]
        assert thin["window"]["start"] == full["window"]["start"]
        assert thin["verdict"]["notable"] == full["verdict"]["notable"]
        assert thin["members"] == full["members"]
        assert thin["occurrences"] == full["occurrences"]
    assert len((tmp_path / "gems-clustered.slim.jsonl").read_bytes()) < len(out.read_bytes())

    # rerunning is byte-identical
    subprocess.run([sys.executable, str(_SCRIPTS / "cluster_gems.py"),
                    "--in", str(gems_path), "--out", str(tmp_path / "again.jsonl")],
                   capture_output=True, text=True, check=True)
    assert (tmp_path / "again.jsonl").read_text() == out.read_text()


# --------------------------------------------------------------------------- #
# member refs carry the per-member attribution flags
# --------------------------------------------------------------------------- #
def test_member_ref_carries_the_sponsor_read_and_anchor_flags():
    """The merge pass caps an ad-read-ONLY cluster and confirms a shared-voice
    cluster only on an anchor: both are questions about every member, so the
    flags travel per member, not just on the representative."""
    ad = _gem("v1", notable="visited his parents", text="chatter one about home")
    ad["window"]["in_sponsor_read"] = True
    ad["window"]["host_anchor"] = False
    organic = _gem("v2", notable="visited his parents",
                   text="chatter two about home")
    organic["window"]["in_sponsor_read"] = False
    organic["window"]["host_anchor"] = True

    line = cluster_gems.build_line([ad, organic])
    by_video = {m["video_id"]: m for m in line["members"]}
    assert by_video["v1"]["in_sponsor_read"] is True
    assert by_video["v1"]["host_anchor"] is False
    assert by_video["v2"]["in_sponsor_read"] is False
    assert by_video["v2"]["host_anchor"] is True


def test_member_ref_flags_default_to_false_when_the_window_lacks_them():
    line = cluster_gems.build_line([_gem("v1", notable="a", text="b")])
    assert line["members"][0]["in_sponsor_read"] is False
    assert line["members"][0]["host_anchor"] is False


def test_member_ref_keeps_the_identity_fields_unchanged():
    ref = cluster_gems.member_ref(_gem("v9", notable="a", text="b", start=42,
                                       published="2021-07-04"))
    assert ref["video_id"] == "v9" and ref["start"] == 42
    assert ref["published"] == "2021-07-04"
    assert set(ref) == {"video_id", "start", "published", "in_sponsor_read",
                        "host_anchor"}
