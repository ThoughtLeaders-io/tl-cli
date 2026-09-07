#!/usr/bin/env python3
"""Group B — view-curve time-series anomaly detection.

Per recent longform: burst-without-engagement, engagement-velocity
coherence, guarantee-cliff, slow-start/late-spike, late-life view drip with
frozen likes. Plus a channel-level subscriber-vs-views check.

Returns {"subscore", "flags", "metrics"}. Flags carry per-video evidence.
"""
from __future__ import annotations

import statistics

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)
import view_curves

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

PENALTIES = {
    "B_burst_without_engagement": (25, "critical"),
    "B_engagement_incoherence": (25, "critical"),
    "B_guarantee_cliff": (15, "warning"),
    "B_slow_start_late_spike": (15, "warning"),
    "B_latelife_drip_frozen_likes": (20, "critical"),
    "B_subs_flat_while_views_surge": (15, "warning"),
}

ROUND_NUMBERS = [50_000, 100_000, 200_000, 250_000, 500_000, 1_000_000, 2_000_000]
N_VIDEOS = 10


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _analyze_video(channel_id: int, video: dict) -> dict:
    vid = video["video_id"]
    snaps = view_curves.load_curve(channel_id, vid)
    res = {"video_id": vid, "title": video.get("title", ""), "issues": {}, "snaps": len(snaps)}
    if len(snaps) < 4:
        return res

    deltas = view_curves.daily_deltas(snaps)
    final_views = snaps[-1].views

    # channel-ish lifetime like rate baseline for this video
    life_like_rate = (snaps[-1].likes / final_views) if final_views else 0.0

    # 1. burst without engagement
    dpd = [d["dviews_per_day"] for d in deltas]
    if dpd:
        roll = statistics.mean(dpd)
        for d in deltas:
            if (
                d["dviews_per_day"] > 3 * max(roll, 1)
                and d["dviews"] > 5000
                and d["like_rate_in_segment"] is not None
                and life_like_rate > 0
                and d["like_rate_in_segment"] < 0.5 * life_like_rate
            ):
                res["issues"]["B_burst_without_engagement"] = (
                    f"burst age {d['from_age']}->{d['to_age']}: "
                    f"+{d['dviews']:,} views but seg like-rate "
                    f"{d['like_rate_in_segment']*100:.4f}% (< half of "
                    f"{life_like_rate*100:.3f}% lifetime)"
                )
                break

    # 2. engagement-velocity coherence
    dv = [d["dviews"] for d in deltas if d["dviews"] >= 0]
    de = [
        (deltas[i]["dlikes"] + deltas[i]["dcomments"])
        for i in range(len(deltas))
        if deltas[i]["dviews"] >= 0
    ]
    r = _pearson([float(x) for x in dv], [float(x) for x in de])
    if r is not None:
        res["coherence_r"] = round(r, 3)
        if r < 0.2:
            res["issues"]["B_engagement_incoherence"] = (
                f"Δviews vs Δengagement correlation r={r:.2f} (<0.2)"
            )

    # 3. guarantee cliff: plateau near a round number
    plateau_start = None
    for i in range(len(deltas)):
        if deltas[i]["dviews_per_day"] < 0.02 * max(statistics.mean(dpd), 1):
            plateau_start = snaps[i + 1]
            break
    if plateau_start and plateau_start.age <= 60:
        for rn in ROUND_NUMBERS:
            if abs(plateau_start.views - rn) / rn <= 0.05:
                res["issues"]["B_guarantee_cliff"] = (
                    f"plateaus at {plateau_start.views:,} (~{rn:,}) by age "
                    f"{plateau_start.age} then flatlines"
                )
                break

    # 4. slow start, late spike
    v2 = view_curves.views_at_age(snaps, 2)
    v10 = view_curves.views_at_age(snaps, 10)
    if v2 is not None and v10 is not None and v2 > 0:
        res["v2"], res["v10"] = int(v2), int(v10)
        if v10 / max(v2, 1) > 8 and v2 < 0.15 * final_views:
            res["issues"]["B_slow_start_late_spike"] = (
                f"slow start (age2={int(v2):,}) then {v10/max(v2,1):.1f}x to "
                f"age10={int(v10):,}"
            )

    # 5. late-life view drip with frozen likes
    for d in deltas:
        if d["from_age"] >= 20 and d["dviews"] > 3000 and d["dlikes"] <= 1:
            res["issues"]["B_latelife_drip_frozen_likes"] = (
                f"age {d['from_age']}->{d['to_age']}: +{d['dviews']:,} views, "
                f"+{d['dlikes']} likes (frozen)"
            )
            break

    return res


def _subs_vs_views(channel_id: int) -> dict | None:
    rows = tl_data.db_fb(
        "SELECT scrape_date, total_views, subscribers FROM channel_metrics "
        f"WHERE id = {int(channel_id)} ORDER BY scrape_date"
    )
    if len(rows) < 8:
        return None
    first, last = rows[0], rows[-1]
    dv = (last.get("total_views") or 0) - (first.get("total_views") or 0)
    ds = (last.get("subscribers") or 0) - (first.get("subscribers") or 0)
    if dv <= 0:
        return None
    subs_per_100k = ds / (dv / 100_000) if dv else 0
    return {"delta_views": dv, "delta_subs": ds, "subs_per_100k_views": subs_per_100k}


def analyze(channel: dict, longform: list[dict], focus_video: dict | None = None) -> dict:
    cid = int(channel["id"])
    flags: list[dict] = []
    metrics: dict = {"videos": []}

    vids = list(longform[:N_VIDEOS])
    if focus_video and focus_video.get("video_id") not in {v["video_id"] for v in vids}:
        vids.append(focus_video)

    triggered: dict[str, int] = {}
    for v in vids:
        r = _analyze_video(cid, v)
        metrics["videos"].append(r)
        for code in r.get("issues", {}):
            triggered[code] = triggered.get(code, 0) + 1

    for code, count in triggered.items():
        sample = next(
            (
                f"{x['title'][:50]} — {x['issues'][code]}"
                for x in metrics["videos"]
                if code in x.get("issues", {})
            ),
            "",
        )
        flags.append(
            {
                "code": code,
                "severity": PENALTIES[code][1],
                "penalty": PENALTIES[code][0],
                "detail": f"{count}/{len(vids)} recent longforms: {sample}",
            }
        )

    sv = _subs_vs_views(cid)
    if sv:
        metrics["subs_vs_views"] = sv
        if sv["subs_per_100k_views"] < 30:
            flags.append(
                {
                    "code": "B_subs_flat_while_views_surge",
                    "severity": PENALTIES["B_subs_flat_while_views_surge"][1],
                    "penalty": PENALTIES["B_subs_flat_while_views_surge"][0],
                    "detail": (
                        f"Only {sv['subs_per_100k_views']:.0f} new subs per 100k "
                        f"channel views over the snapshot window — viewers aren't "
                        f"converting, consistent with non-organic traffic."
                    ),
                }
            )

    subscore = 100
    for f in flags:
        subscore -= f["penalty"]
    return {"subscore": max(subscore, 0), "flags": flags, "metrics": metrics}


if __name__ == "__main__":
    import sys

    import resolve_channel

    d = resolve_channel.resolve(sys.argv[1])
    out = analyze(d["channel"], d["longform"], d.get("focus_video"))
    print("subscore", out["subscore"])
    for f in out["flags"]:
        print(f["code"], f["severity"], "-", f["detail"])
