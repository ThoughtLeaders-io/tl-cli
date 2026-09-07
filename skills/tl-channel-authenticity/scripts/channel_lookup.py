#!/usr/bin/env python3
"""Channel resolution for the authenticity skill, on top of the shared seam.

Skill-specific by design: the ambiguity contract (raise, never guess) and the
URL/handle parsing belong to this skill's orchestration, while all actual data
access goes through ``skills/_shared/tl_data.py``.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data


class AmbiguousChannel(tl_data.DataError):
    """A name/handle matched multiple channels; the caller must pick by id.

    Carries the candidate rows (ordered by subscribers desc) so the
    orchestrator can present them and re-run with a specific id.
    """

    def __init__(self, ref, candidates: list[dict]):
        self.ref = ref
        self.candidates = candidates
        lines = "\n".join(
            f"  {c.get('id'):>9}  {(c.get('subscribers') or 0):>13,}  "
            f"{c.get('channel_name', '')}"
            for c in candidates
        )
        super().__init__(
            f"Multiple channels match '{ref}'. Re-run with a specific id.\n"
            f"  {'id':>9}  {'subscribers':>13}  name\n{lines}"
        )


def channels_show(ref: str | int) -> dict:
    # Build the exact query rather than calling `tl channels show`: the
    # structured command returns a curated public schema (channel_id/name/
    # subscribers/category) that doesn't match the raw-table columns the rest
    # of the skill reads (id/channel_name/subscribers/content_category).
    sql = (
        "SELECT id, channel_name, slug, url, external_channel_id, subscribers, "
        "total_views, country, language, content_category, is_active, "
        "media_selling_network_join_date, is_tpp, engagement, "
        "sponsorship_score, num_uploads, last_published, "
        "demographic_male_share, demographic_usa_share "
        f"FROM thoughtleaders_channel WHERE {_channel_where(ref)} "
        "ORDER BY subscribers DESC NULLS LAST LIMIT 10"
    )
    rows = tl_data.db_pg(sql)
    if not rows:
        raise tl_data.DataError(f"channel not found: {ref}")
    if len(rows) > 1:
        # A name/handle matched several channels (e.g. localized dupes). Don't
        # silently pick one — surface candidates (biggest first) so the caller
        # can re-run with the intended id.
        raise AmbiguousChannel(ref, rows)
    return rows[0]


def channels_similar(channel_id: int, limit: int = 20) -> list[dict]:
    return tl_data.cli_rows(
        ["channels", "similar", str(channel_id), "--limit", str(limit),
         "--json"]
    )


def _channel_where(ref: str | int) -> str:
    s = str(ref).strip()
    if s.isdigit():
        return f"id = {int(s)}"
    handle = s
    ext_id = None
    if "youtube.com" in s or "youtu.be" in s:
        path = s.split("youtube.com", 1)[-1].split("youtu.be", 1)[-1]
        path = path.split("?")[0].split("#")[0]
        if "@" in path:                       # /@handle
            handle = path.split("@", 1)[1].split("/")[0]
        elif "/channel/" in path:             # /channel/UCxxxx (external id)
            ext_id = path.split("/channel/", 1)[1].split("/")[0]
        elif "/c/" in path:                   # /c/CustomName
            handle = path.split("/c/", 1)[1].split("/")[0]
        elif "/user/" in path:                # /user/LegacyName
            handle = path.split("/user/", 1)[1].split("/")[0]
        else:
            handle = path.strip("/").split("/")[0]
    if ext_id:
        return f"external_channel_id = '{ext_id.replace(chr(39), '')}'"
    handle = handle.lstrip("@").replace("'", "''")
    # /c/ and /user/ custom names are often spaced in channel_name
    spaced = handle.replace("-", " ").replace("_", " ")
    return (
        f"url ILIKE '%@{handle}%' OR slug ILIKE '%{handle}%' "
        f"OR channel_name ILIKE '%{handle}%' "
        f"OR channel_name ILIKE '%{spaced}%'"
    )
