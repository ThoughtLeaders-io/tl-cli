#!/usr/bin/env python3
"""Minimal view-curve loader + interpolation for sparse view snapshots.

Self-contained gap-filling logic (linear + log interpolation, first deltas).
Pulls per-video view/like/comment snapshots via tl_data.db_fb.

Snapshots are sparse — older videos are sampled less often — so to compare
"views at age N" we interpolate between the surrounding snapshots:
  * linear between two bracketing snapshots (general case)
  * logarithmic (views = a*(b+log(age))) when bracketing by age, which fits
    YouTube view-accumulation far better than linear over wide gaps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data


@dataclass
class Snap:
    age: int
    views: int
    likes: int
    comments: int


def load_curve(channel_id: int, video_id: str) -> list[Snap]:
    rows = tl_data.db_fb(
        "SELECT age, view_count, like_count, comment_count "
        "FROM article_metrics "
        f"WHERE channel_id = {int(channel_id)} AND id = '{video_id}' "
        "ORDER BY age"
    )
    snaps: list[Snap] = []
    seen: set[int] = set()
    for r in rows:
        age = int(r["age"])
        if age in seen:
            continue
        seen.add(age)
        snaps.append(
            Snap(
                age=age,
                views=int(r.get("view_count") or 0),
                likes=int(r.get("like_count") or 0),
                comments=int(r.get("comment_count") or 0),
            )
        )
    snaps.sort(key=lambda s: s.age)
    return snaps


def _interp_linear(a: Snap, b: Snap, target_age: int) -> float | None:
    if b.age == a.age:
        return None
    frac = (target_age - a.age) / (b.age - a.age)
    return a.views + frac * (b.views - a.views)


def _interp_log(a: Snap, b: Snap, target_age: int) -> float | None:
    # views = A*(B + log(age)); solve A,B from the two points (ages >= 1)
    try:
        a1, a2 = a.age, b.age
        v1, v2 = a.views, b.views
        if a1 < 1 or a2 < 1 or a1 == a2 or v1 == v2:
            return None
        A = (v1 - v2) / (math.log(a1) - math.log(a2))
        B = (v2 * math.log(a1) - v1 * math.log(a2)) / (v1 - v2)
        return A * (B + math.log(max(target_age, 1)))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def views_at_age(snaps: list[Snap], target_age: int, *, prefer_log: bool = True) -> float | None:
    """Estimate cumulative views at a given age via bracket interpolation."""
    if not snaps:
        return None
    if target_age <= snaps[0].age:
        return float(snaps[0].views)
    if target_age >= snaps[-1].age:
        return float(snaps[-1].views)
    for i in range(len(snaps) - 1):
        a, b = snaps[i], snaps[i + 1]
        if a.age <= target_age <= b.age:
            if prefer_log:
                est = _interp_log(a, b, target_age)
                if est is not None and est >= 0:
                    return est
            return _interp_linear(a, b, target_age)
    return None


def daily_deltas(snaps: list[Snap]) -> list[dict]:
    """Per-segment normalized deltas between consecutive snapshots.

    Returns list of {from_age, to_age, span_days, dviews, dlikes, dcomments,
    dviews_per_day, like_rate_in_segment} — the building block for burst /
    coherence / frozen-likes detection.
    """
    out = []
    for i in range(1, len(snaps)):
        a, b = snaps[i - 1], snaps[i]
        span = max(b.age - a.age, 1)
        dv = b.views - a.views
        dl = b.likes - a.likes
        dc = b.comments - a.comments
        out.append(
            {
                "from_age": a.age,
                "to_age": b.age,
                "span_days": span,
                "dviews": dv,
                "dlikes": dl,
                "dcomments": dc,
                "dviews_per_day": dv / span,
                "like_rate_in_segment": (dl / dv) if dv > 0 else None,
                "comment_rate_in_segment": (dc / dv) if dv > 0 else None,
            }
        )
    return out


if __name__ == "__main__":
    import json
    import sys

    cid, vid = int(sys.argv[1]), sys.argv[2]
    s = load_curve(cid, vid)
    print(f"{len(s)} snapshots, ages {s[0].age}..{s[-1].age}" if s else "no data")
    print("views@2 =", views_at_age(s, 2), " views@10 =", views_at_age(s, 10))
    print(json.dumps(daily_deltas(s)[-5:], indent=2, default=str))
