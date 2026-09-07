#!/usr/bin/env python3
"""Group B add-on — deleted / unlisted video integrity (intent-aware).

Deletion or unlisting alone is NOT a fraud signal — channels legitimately
re-upload, fix mistakes, or clean house. What matters is whether deletion is
being used as a *tool to conceal or misrepresent performance*. So every
offline/unlisted video is classified benign vs concealment from intent
indicators, and only concealment is penalized.

Data source: ES article docs (already scraped, no yt-dlp / rate limits):
  * ``offline_since`` exists  -> video went offline (deleted/private/gone)
  * ``content_aspects`` has ``'unlisted'`` -> video is unlisted (hidden from
    channel page & subscribers but still accruing views)

Concealment indicators:
  - a SOLD + PUBLISHED sponsored video now offline/unlisted (brand paid,
    delivery hidden — also a finance/delivery alarm)
  - a HIGH-VIEW video gone (you don't delete a 2M-view video by accident;
    consistent with a YouTube strike for bought traffic)
  - an UNLISTED video still carrying real view volume (hidden from the
    organic audience while fed external traffic)
  - a meaningful SHARE of total tracked views having vanished

Benign (recorded, not penalized):
  - pulled within ~7 days of publish with < BENIGN_VIEW_CEILING views and
    not sponsored (re-upload / mistake)
"""
from __future__ import annotations

from datetime import date

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

PENALTIES = {
    "B_sponsored_video_concealed": (30, "critical"),
    "B_high_view_video_scrub": (25, "critical"),
    "B_unlisted_with_traffic": (15, "warning"),
}

BENIGN_AGE_DAYS = 7
BENIGN_VIEW_CEILING = 5_000
HIGH_VIEW_FLOOR = 50_000          # absolute "not an accident" floor
UNLISTED_TRAFFIC_FLOOR = 20_000
SCRUB_VIEW_SHARE_CRIT = 0.15      # >=15% of tracked peak-views gone => critical
SCRUB_VIEW_SHARE_BENIGN = 0.03    # < this => normal catalogue churn, not penalized
MAX_ARTICLES = 300


def _d(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _sponsored_article_ids(channel_id: int) -> dict[str, dict]:
    rows = tl_data.db_pg(
        "SELECT a.id, a.publish_status, a.publish_date, a.price, a.article_id "
        "FROM thoughtleaders_adlink a "
        "JOIN thoughtleaders_adspot s ON a.ad_spot_id = s.id "
        f"WHERE s.channel_id = {int(channel_id)} "
        "AND a.publish_status = 3 AND a.publish_date IS NOT NULL "
        "AND a.article_id IS NOT NULL"
    )
    out: dict[str, dict] = {}
    for r in rows:
        art = (r.get("article_id") or "")
        vid = art.split(":", 1)[1] if ":" in art else art
        if vid:
            out[vid] = {
                "adlink_id": r["id"],
                "price": r.get("price"),
                "publish_date": r.get("publish_date"),
            }
    return out


def _all_articles(channel_id: int) -> list[dict]:
    body = {
        "size": MAX_ARTICLES,
        "_source": [
            "id", "title", "publication_date", "views", "offline_since",
            "content_aspects", "content_type",
        ],
        "query": {
            "bool": {
                "must": [
                    {"term": {"doc_type": "article"}},
                    {"term": {"channel.id": channel_id}},
                ]
            }
        },
        "sort": [{"publication_date": {"order": "desc"}}],
    }
    out = []
    for s in tl_data.db_es(body):
        vid = str(s.get("id", ""))
        out.append(
            {
                "video_id": vid.split(":", 1)[1] if ":" in vid else vid,
                "title": s.get("title", ""),
                "publication_date": s.get("publication_date"),
                "views": s.get("views") or 0,
                "offline_since": s.get("offline_since"),
                "unlisted": "unlisted" in (s.get("content_aspects") or []),
            }
        )
    return out


def analyze(channel: dict, *, sample_floor_views: int = 0) -> dict:
    """Returns {flags, metrics, penalty, hard_fail}. Caller merges into
    Group B."""
    cid = int(channel["id"])
    arts = _all_articles(cid)
    sponsored = _sponsored_article_ids(cid)

    total = len(arts)
    total_views = sum(a["views"] for a in arts) or 1
    offline = [a for a in arts if a["offline_since"]]
    unlisted = [a for a in arts if a["unlisted"]]

    # channel-relative "high view" bar: max of absolute floor and 25% of the
    # median tracked video's views, so it scales to small channels too.
    nonzero = sorted(a["views"] for a in arts if a["views"] > 0)
    med = nonzero[len(nonzero) // 2] if nonzero else 0
    high_bar = max(HIGH_VIEW_FLOOR, int(med * 0.25))

    concealed_sponsored: list[dict] = []
    high_view_gone: list[dict] = []
    unlisted_traffic: list[dict] = []
    benign: list[dict] = []

    for a in arts:
        if not a["offline_since"] and not a["unlisted"]:
            continue
        spon = sponsored.get(a["video_id"])
        pub, off = _d(a["publication_date"]), _d(a["offline_since"])
        age_at_off = (off - pub).days if (pub and off) else None
        why = []

        if spon:
            why.append(
                f"SOLD+PUBLISHED sponsorship (adlink {spon['adlink_id']}, "
                f"${spon['price']}) is now "
                f"{'unlisted' if a['unlisted'] else 'offline'} — paid "
                f"delivery hidden from brand/audience"
            )
            concealed_sponsored.append({**a, "why": "; ".join(why), **spon})
            continue

        if (
            a["offline_since"]
            and age_at_off is not None
            and age_at_off <= BENIGN_AGE_DAYS
            and a["views"] < BENIGN_VIEW_CEILING
        ):
            benign.append(
                {**a, "why": f"removed {age_at_off}d after publish at only "
                 f"{a['views']:,} views — likely re-upload/mistake"}
            )
            continue

        if a["offline_since"] and a["views"] >= high_bar:
            high_view_gone.append(
                {**a, "why": f"{a['views']:,} views then taken offline "
                 f"(>{high_bar:,} bar) — large video removed"}
            )
            continue

        if a["unlisted"] and a["views"] >= UNLISTED_TRAFFIC_FLOOR:
            unlisted_traffic.append(
                {**a, "why": f"unlisted but still has {a['views']:,} views — "
                 f"hidden from organic audience while accruing views"}
            )
            continue

        benign.append({**a, "why": "offline/unlisted, low-view, non-sponsored"})

    gone_views = sum(a["views"] for a in offline)
    scrub_share = gone_views / total_views

    flags: list[dict] = []
    penalty = 0
    hard_fail = False

    if concealed_sponsored:
        penalty += PENALTIES["B_sponsored_video_concealed"][0]
        sample = concealed_sponsored[0]
        flags.append(
            {
                "code": "B_sponsored_video_concealed",
                "severity": "critical",
                "penalty": PENALTIES["B_sponsored_video_concealed"][0],
                "detail": (
                    f"{len(concealed_sponsored)} sold+published sponsored "
                    f"video(s) now offline/unlisted. e.g. "
                    f"\"{sample['title'][:50]}\" — {sample['why']}"
                ),
            }
        )
        # concealment of paid delivery is bad-faith; >=1 with real money +
        # any view history, or >=2, forces the verdict.
        if len(concealed_sponsored) >= 2 or any(
            c["views"] >= BENIGN_VIEW_CEILING for c in concealed_sponsored
        ):
            hard_fail = True

    if high_view_gone:
        # Escalation is driven by the SHARE of tracked views that vanished, not
        # the raw count: a large channel always accumulates a few old high-view
        # videos going offline (claims, re-edits, privating). A scattering that
        # is a trivial fraction of views is normal catalogue churn.
        if scrub_share >= SCRUB_VIEW_SHARE_CRIT:
            pen, sev = PENALTIES["B_high_view_video_scrub"][0], "critical"
            interp = ("not an accidental deletion; consistent with a strike "
                      "on bought traffic")
        elif scrub_share >= SCRUB_VIEW_SHARE_BENIGN:
            pen, sev = 12, "warning"
            interp = "verify none were recently-pulled sponsorships"
        else:
            pen, sev, interp = 0, "info", ""
        if pen:
            penalty += pen
            s = high_view_gone[0]
            flags.append(
                {
                    "code": "B_high_view_video_scrub",
                    "severity": sev,
                    "penalty": pen,
                    "detail": (
                        f"{len(high_view_gone)} high-view video(s) gone "
                        f"({gone_views:,} views = {scrub_share*100:.0f}% of all "
                        f"tracked views). e.g. \"{s['title'][:50]}\" — {interp}"
                    ),
                }
            )
        if scrub_share >= SCRUB_VIEW_SHARE_CRIT and len(high_view_gone) >= 3:
            hard_fail = True

    if unlisted_traffic:
        penalty += PENALTIES["B_unlisted_with_traffic"][0]
        s = unlisted_traffic[0]
        flags.append(
            {
                "code": "B_unlisted_with_traffic",
                "severity": "warning",
                "penalty": PENALTIES["B_unlisted_with_traffic"][0],
                "detail": (
                    f"{len(unlisted_traffic)} unlisted video(s) still "
                    f"carrying view volume. e.g. \"{s['title'][:50]}\" — "
                    f"{s['why']}"
                ),
            }
        )

    metrics = {
        "tracked_articles": total,
        "offline_count": len(offline),
        "offline_rate": round(len(offline) / total, 3) if total else 0,
        "unlisted_count": len(unlisted),
        "views_gone": gone_views,
        "scrub_view_share": round(scrub_share, 3),
        "concealed_sponsored": [
            {"video_id": c["video_id"], "title": c["title"],
             "views": c["views"], "adlink_id": c["adlink_id"], "why": c["why"]}
            for c in concealed_sponsored
        ],
        "high_view_gone": [
            {"video_id": h["video_id"], "title": h["title"],
             "views": h["views"], "why": h["why"]} for h in high_view_gone
        ],
        "unlisted_with_traffic": [
            {"video_id": u["video_id"], "title": u["title"],
             "views": u["views"]} for u in unlisted_traffic
        ],
        "benign_removals": len(benign),
        "benign_examples": [b["why"] for b in benign[:3]],
    }
    return {
        "flags": flags,
        "metrics": metrics,
        "penalty": penalty,
        "hard_fail": hard_fail,
    }


if __name__ == "__main__":
    import json
    import sys

    import resolve_channel

    ch = resolve_channel.resolve(sys.argv[1])["channel"]
    print(json.dumps(analyze(ch), indent=2, default=str))
