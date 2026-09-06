#!/usr/bin/env python3
"""Render the connections page — the one human deliverable of a creator brief.

Deterministic templating in code — the template lives here, is never
redesigned per run, and the model never hand-writes HTML. The ledger
(``<channel_id>-facts.jsonl``, header + one fact per line) and the connection
map's markdown stay canonical; HTML is the view.

Usage:
    build_html.py --in .corpus/<id>/connections-<brand>.md \\
        --facts tl-creator-profiles/<id>-facts.jsonl \\
        [--out tl-creator-profiles/<id>-<brand>-connections.html]

``--out`` defaults to ``<facts dir>/<channel_id>-<brand_id>-connections.html``
when ``--facts`` is given (the markdown is a working file under
``.corpus/``; only the HTML lands in the deliverable directory), and to the
input path with an ``.html`` suffix when it is not. ``--meta`` is a legacy
escape hatch: the meta record is the ledger's first line, and the flag is
read only when the ledger carries no header.

The page, in order:

- the header — creator × brand, with the brand-read and ledger-build dates;
- **Who they are** — rendered from the ledger at render time, never written
  by a model: the top recurring facts by life domain;
- **About <brand>** — the markdown body's ``## About …`` section, rendered as
  prose above the cards (it is context, not a connection, so it is never
  numbered as one);
- **Connections** — one numbered card per remaining ``## `` section, in the
  markdown's order, which IS the ranking. Provenance labels in the markdown
  (``[web]``, ``[social: …]``) are kept: a connection map names its lanes. A
  no-fit verdict has no sections and stays prose;
- **About this ledger** — the honesty footer ``references/evidence-rules.md``
  requires: the confidence and sensitivity tallies with the count withheld
  from angles, the coverage ratio and "absence is not evidence", the build
  facts, and the linked platforms / sibling channels the run did not mine.

Facts at tier ``children`` or ``location`` never enter the who-they-are
section (they are withheld from brand-facing angles by default); ``clinical``
does, carrying its tier badge, per ``references/evidence-rules.md``.

Fonts load from Google Fonts with system fallbacks declared, so the page reads
the same offline; nothing else is fetched and there is no script.
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ledger_io import read_ledger  # sibling script  # noqa: E402

BADGES = {
    "direct": "direct",
    "adjacent": "adjacent",
    "category precedent": "precedent",
    "category-precedent": "precedent",
    "confirmed": "confirmed",
    "unconfirmed": "unconfirmed",
    "lifestyle": "lifestyle",
    "clinical": "clinical",
    "children": "withheld",
    "location": "withheld",
    "withheld": "withheld",
    "no fit": "nofit",
    "no-fit": "nofit",
}
TIER_ORDER = ("none", "lifestyle", "clinical", "children", "location")
WITHHELD = {"children", "location"}          # never on a brand-facing page by default
DOMAIN_LABELS = {
    "origin": "Origin", "family": "Family", "pets": "Pets", "home": "Home",
    "work": "Work", "money": "Money", "health": "Health", "habits": "Habits",
    "tastes": "Tastes", "beliefs": "Beliefs", "relationships": "Relationships",
    "other": "Other",
}
WHO_MAX_FACTS = 12
WHO_MAX_PER_DOMAIN = 3

FONTS = ("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600"
         "&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500"
         "&display=swap")

CSS = """
:root {
  --bg: #f2f4f6; --surface: #ffffff; --ink: #172029; --ink-2: #4b5866;
  --ink-3: #79858f; --line: #d6dde4; --accent: #0b6f8f; --accent-soft: #dbeef4;
  --quote: #33404c;
  --badge-direct: #0b6f8f; --badge-adjacent: #6a4fc4; --badge-precedent: #8a6a12;
  --badge-confirmed: #1f7a5a; --badge-unconfirmed: #8c6a12;
  --badge-lifestyle: #1f7a5a; --badge-clinical: #a35a12; --badge-withheld: #9b2f2f;
  --badge-nofit: #5b6472;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #10161c; --surface: #171f28; --ink: #e4e9ed; --ink-2: #adb8c2;
    --ink-3: #7b8792; --line: #283442; --accent: #58bcd8; --accent-soft: #143241;
    --quote: #c2ccd6;
    --badge-direct: #3d9fbe; --badge-adjacent: #9a84e0; --badge-precedent: #c9a43a;
    --badge-confirmed: #45a37f; --badge-unconfirmed: #c9a43a;
    --badge-lifestyle: #45a37f; --badge-clinical: #d08a45; --badge-withheld: #d76b6b;
    --badge-nofit: #8b95a3;
  }
}
:root[data-theme="dark"] {
  --bg: #10161c; --surface: #171f28; --ink: #e4e9ed; --ink-2: #adb8c2;
  --ink-3: #7b8792; --line: #283442; --accent: #58bcd8; --accent-soft: #143241;
  --quote: #c2ccd6;
  --badge-direct: #3d9fbe; --badge-adjacent: #9a84e0; --badge-precedent: #c9a43a;
  --badge-confirmed: #45a37f; --badge-unconfirmed: #c9a43a;
  --badge-lifestyle: #45a37f; --badge-clinical: #d08a45; --badge-withheld: #d76b6b;
  --badge-nofit: #8b95a3;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 17px/1.6 "Source Sans 3", "Source Sans Pro", -apple-system,
        BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
main { max-width: 860px; margin: 0 auto; padding: 2.75rem 1.25rem 5rem; }
h1, h2, h3 {
  font-family: Fraunces, "Iowan Old Style", Georgia, "Times New Roman", serif;
  font-weight: 600; line-height: 1.15; text-wrap: balance; margin: 0;
}
h1 { font-size: 2.3rem; letter-spacing: -.01em; }
h2 { font-size: 1.45rem; margin: 2.8rem 0 1rem; }
h3 { font-size: 1.15rem; margin: 0; }
p { margin: .5rem 0; max-width: 68ch; }
a { color: var(--accent); text-decoration: none; }
a:hover, a:focus-visible { text-decoration: underline; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
ul, ol { margin: .5rem 0; padding-left: 1.3rem; }
li { margin: .3rem 0; }
hr { border: none; border-top: 1px solid var(--line); margin: 1.8rem 0; }
code {
  font-family: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: .85em; background: var(--accent-soft); border-radius: 3px;
  padding: .05rem .3rem;
}
blockquote {
  margin: .7rem 0; padding: .55rem 1rem; color: var(--quote);
  border-left: 3px solid var(--accent); font-style: italic;
  font-family: Fraunces, "Iowan Old Style", Georgia, serif; font-size: 1.05rem;
}
blockquote p { margin: .2rem 0; max-width: none; }
.eyebrow {
  font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace;
  font-size: .72rem; letter-spacing: .12em; text-transform: uppercase;
  color: var(--ink-3); margin: 0 0 .6rem;
}
header { padding-bottom: 1.4rem; border-bottom: 1px solid var(--line); }
.meta {
  display: flex; flex-wrap: wrap; gap: .4rem .9rem; margin: .9rem 0 0;
  padding: 0; list-style: none; font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .76rem; color: var(--ink-2); font-variant-numeric: tabular-nums;
}
.meta li { margin: 0; }
.meta li::before { content: "·"; color: var(--ink-3); margin-right: .55rem; }
.meta li:first-child::before { content: none; margin: 0; }
.about {
  margin: 0; padding: .9rem 1.1rem; background: var(--surface);
  border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 6px; color: var(--ink-2);
}
.about h3 { font-size: 1.05rem; color: var(--ink); margin: 0 0 .3rem; }
.about p { margin: .35rem 0; }
.ledger {
  margin: .6rem 0 0; padding: .75rem 1rem; background: var(--surface);
  border: 1px solid var(--line); border-radius: 6px; font-size: .9rem;
  color: var(--ink-2);
}
.ledger h3 {
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-weight: 500;
  font-size: .74rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-3); margin: .8rem 0 .35rem;
}
.tally { display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; margin: .2rem 0 0; padding: 0; list-style: none; }
.tally li { margin: 0; font-variant-numeric: tabular-nums; }
.who {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem 1.6rem; margin: 0;
}
.who .domain { border-top: 2px solid var(--accent); padding-top: .5rem; }
.who .domain h3 {
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-weight: 500;
  font-size: .74rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-3); margin: 0 0 .35rem;
}
.who ul { list-style: none; margin: 0; padding: 0; }
.who li { margin: 0 0 .55rem; }
.who .claim { font-weight: 600; }
.who .q {
  display: block; color: var(--ink-2); font-size: .92rem; font-style: italic;
  font-family: Fraunces, "Iowan Old Style", Georgia, serif;
}
.who .q a { color: var(--ink-3); font-style: normal; font-family: "IBM Plex Mono", monospace; font-size: .72rem; }
.who-run { list-style: none; margin: 0; padding: 0; }
.who-run li {
  margin: 0 0 .6rem; padding-left: .9rem; border-left: 2px solid var(--accent);
}
.who-run .claim { font-weight: 600; }
.who-run .q {
  display: block; font-style: italic; color: var(--ink-2); margin-top: .15rem;
}
.who-run .q a {
  color: var(--ink-3); font-style: normal;
  font-family: "IBM Plex Mono", monospace; font-size: .72rem;
}
.thesis {
  border-left: 3px solid var(--accent); padding: .1rem 0 .1rem 1rem;
  margin: 0 0 1.4rem;
}
.thesis p { font-size: 1.08rem; line-height: 1.6; max-width: 68ch; }
.bridges { margin: 0 0 1.4rem; }
.bridges blockquote { margin: 0 0 .7rem; }
.caveat {
  border: 1px solid var(--line); border-left: 3px solid var(--ink-3);
  padding: .8rem 1rem; margin: 1rem 0 1.6rem; background: var(--surface);
}
.caveat p { margin: .35rem 0; max-width: 70ch; }
.conn { list-style: none; margin: 0; padding: 0; counter-reset: rank; }
.conn > li {
  display: grid; grid-template-columns: 3rem 1fr; gap: 0 1rem; margin: 0 0 1.1rem;
  padding: 1rem 1.1rem 1.1rem .9rem; background: var(--surface);
  border: 1px solid var(--line); border-radius: 6px;
}
.conn > li::before {
  counter-increment: rank; content: counter(rank, decimal-leading-zero);
  font-family: Fraunces, Georgia, serif; font-size: 1.9rem; line-height: 1;
  color: var(--ink-3); font-variant-numeric: tabular-nums; padding-top: .1rem;
}
.conn .body { min-width: 0; }
.conn h3 { margin: 0 0 .4rem; }
.conn h3 .badge { margin-left: .5rem; vertical-align: .2em; }
.conn p { max-width: 70ch; }
.prose { margin-top: 1rem; }
.badge {
  display: inline-block; padding: .08rem .5rem; border-radius: 3px;
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .66rem;
  font-weight: 500; letter-spacing: .08em; text-transform: uppercase;
  font-style: normal; color: var(--surface); vertical-align: middle;
}
.badge-direct { background: var(--badge-direct); }
.badge-adjacent { background: var(--badge-adjacent); }
.badge-precedent { background: var(--badge-precedent); }
.badge-confirmed { background: var(--badge-confirmed); }
.badge-unconfirmed { background: var(--badge-unconfirmed); }
.badge-lifestyle { background: var(--badge-lifestyle); }
.badge-clinical { background: var(--badge-clinical); }
.badge-withheld { background: var(--badge-withheld); }
.badge-nofit { background: var(--badge-nofit); }
.empty { color: var(--ink-2); font-style: italic; }
.links { list-style: none; padding: 0; margin: 0; font-size: .92rem; color: var(--ink-2); }
.links li { margin: .3rem 0; word-break: break-word; }
.scroll { overflow-x: auto; }
@media (prefers-reduced-motion: no-preference) { a { transition: color .15s; } }
"""


# --------------------------------------------------------------------------- #
# markdown
# --------------------------------------------------------------------------- #
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Flat ``key: value`` frontmatter (the connections contract's shape)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"')
    return meta, text[end + 4:].lstrip("\n")


def badge(token: str) -> str | None:
    cls = BADGES.get(token.strip().lower())
    return f'<span class="badge badge-{cls}">{html.escape(token.strip())}</span>' if cls else None


def inline(text: str) -> str:
    """Escaped text -> inline HTML: links, bold (with badges), italic, code."""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # the surrounding escape pass already entity-escaped the URL once
    # (quote=False, so quotes survived): decode back to the raw URL, then
    # escape once for attribute context — a quote in a crafted link target
    # must not break out of href, and a & must not double-escape to &amp;amp;
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: ('<a href="'
                   f'{html.escape(html.unescape(m.group(2)), quote=True)}">'
                   f"{m.group(1)}</a>"),
        text)

    def bold(m: re.Match) -> str:
        return badge(m.group(1)) or f"<strong>{m.group(1)}</strong>"

    text = re.sub(r"\*\*([^*]+)\*\*", bold, text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    return text


def render_markdown(md: str) -> str:
    out: list[str] = []
    in_list = None      # "ul" | "ol" | None
    in_quote = False
    para: list[str] = []

    def close_para() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for raw in md.splitlines():
        line = html.escape(raw.rstrip(), quote=False)
        stripped = line.strip()
        if not stripped:
            close_para(), close_list(), close_quote()
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if h:
            close_para(), close_list(), close_quote()
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            continue
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            close_para(), close_list(), close_quote()
            out.append("<hr>")
            continue
        if stripped.startswith("&gt;"):
            close_para(), close_list()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{inline(stripped[4:].strip())}</p>")
            continue
        close_quote()
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            close_para()
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            close_para()
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue
        close_list()
        para.append(stripped)
    close_para(), close_list(), close_quote()
    return "\n".join(out)


def split_sections(body_html: str) -> tuple[str, list[tuple[str, str]]]:
    """``(intro, [(title, rest), …])`` — whatever precedes the first ``## ``
    section stays prose (the header lines, or a no-fit verdict); each section
    is one (title, body) pair in the markdown's order."""
    parts = re.split(r"(?=<h2>)", body_html)
    sections: list[tuple[str, str]] = []
    for chunk in parts[1:]:
        m = re.match(r"<h2>(.*?)</h2>\n?(.*)", chunk, re.S)
        if not m:
            continue
        sections.append((m.group(1), m.group(2).strip()))
    return parts[0].strip(), sections


def plain(title_html: str) -> str:
    """A section heading as text: tags dropped, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()


def is_about(title_html: str) -> bool:
    """``## About <name>`` — a context strip, never a numbered card. Covers
    both the creator and the brand introductions."""
    return plain(title_html).lower().startswith("about ")


def is_thesis(title_html: str) -> bool:
    """``## Thesis`` — the page's lead argument, rendered above the brand."""
    return plain(title_html).lower().rstrip(":").strip() in (
        "thesis", "core thesis", "the thesis")


def is_caveat(title_html: str) -> bool:
    """``## Where this could go wrong`` — the honest mismatch. It is a ``## ``
    section like any other in the markdown, but it must NEVER render as a
    numbered connection: a caveat sitting in the ranked list reads as an
    angle, which is exactly the "bunch of random connections" complaint."""
    t = plain(title_html).lower()
    return any(k in t for k in ("could go wrong", "does not connect",
                                "doesn't connect", "what does not fit",
                                "mismatch", "caveat"))


def names_creator(title_html: str, creator: str) -> bool:
    """Does this ``## About …`` heading introduce the creator rather than the
    brand? Compared on the name so a brand called "About Time" cannot win."""
    t = plain(title_html).lower()
    c = str(creator or "").strip().lower()
    return bool(c) and c in t


def about_block(sections: list[tuple[str, str]]) -> str:
    """A context strip: plain prose, never a card."""
    blocks = [f'<div class="about"><h3>{title}</h3>{rest}</div>'
              for title, rest in sections]
    return "".join(blocks)


def thesis_block(sections: list[tuple[str, str]]) -> str:
    """The lead argument, above the brand introduction. Deliberately the most
    prominent prose on the page: it is what the reader acts on."""
    if not sections:
        return ""
    bodies = "".join(rest for _, rest in sections)
    return f'<h2>The thesis</h2><div class="thesis">{bodies}</div>'


def caveat_block(sections: list[tuple[str, str]]) -> str:
    """"Where this could go wrong", after the cards and outside the ranking.
    An honest mismatch beats overfitting to a perfect match, so this is kept
    rather than trimmed away — just never numbered among the angles."""
    if not sections:
        return ""
    bodies = "".join(rest for _, rest in sections)
    title = plain(sections[0][0]) or "Where this could go wrong"
    return (f'<h2>{html.escape(title)}</h2>'
            f'<div class="caveat">{bodies}</div>')


_BLOCKQUOTE = re.compile(r"<blockquote>.*?</blockquote>", re.S)


def quote_bridges(sections: list[tuple[str, str]]) -> str:
    """The quote-bridge strip: the first verbatim quote from each connection,
    gathered in one place so the evidence reads as evidence before any
    argument is made about it. Sections carry their quotes as ``>`` blocks, so
    the strongest one is the first."""
    quotes = []
    for _, rest in sections:
        m = _BLOCKQUOTE.search(rest)
        if m:
            quotes.append(m.group(0))
    if not quotes:
        return ""
    return ('<h2>In their own words</h2>'
            f'<div class="bridges">{"".join(quotes)}</div>')


def connection_cards(sections: list[tuple[str, str]], intro: str = "") -> str:
    """Each connection section becomes one ranked card; the section order IS
    the ranking, so cards are numbered."""
    cards = []
    for title, rest in sections:
        # a leading "1. " in the heading duplicates the card's own numeral
        title = re.sub(r"^\d+[.)]\s*", "", title)
        cards.append(f'<li><div class="body"><h3>{title}</h3>{rest}</div></li>')
    out = f'<div class="prose">{intro}</div>' if intro else ""
    if cards:
        out += f'<ol class="conn">{"".join(cards)}</ol>'
    return out


# --------------------------------------------------------------------------- #
# ledger data
# --------------------------------------------------------------------------- #
def load_ledger(facts_path: pathlib.Path | None,
                meta_path: pathlib.Path | None) -> tuple[list[dict] | None, dict]:
    """``(facts, meta)`` from the ledger — its first line is the meta record.
    ``--meta`` is only read when the ledger predates the header (legacy)."""
    if facts_path is None:
        return None, (json.loads(pathlib.Path(meta_path).read_text(encoding="utf-8"))
                      if meta_path else {})
    meta, facts = read_ledger(facts_path)
    if meta is None and meta_path:
        meta = json.loads(pathlib.Path(meta_path).read_text(encoding="utf-8"))
    return facts, meta or {}


def tier_of(fact: dict) -> str:
    tier = str(fact.get("sensitivity") or "").lower()
    if tier in TIER_ORDER:
        return tier
    # a ledger written against the old boolean: the flag alone cannot say
    # which withheld tier, so it renders as withheld without naming one
    return "withheld" if fact.get("sensitive") else "none"


def tier_badge(fact: dict) -> str:
    tier = tier_of(fact)
    if tier == "none":
        return ""
    label = tier if tier in TIER_ORDER else "withheld"
    return badge(label) or ""


def tallies(facts: list[dict]) -> list[str]:
    """The honesty tallies ``references/evidence-rules.md`` requires: how many
    facts at which confidence, which sensitivity tiers, and how many of them
    are withheld from brand-facing angles."""
    confidence: Counter = Counter()
    domains: Counter = Counter()
    tiers: Counter = Counter()
    for fact in facts:
        confidence[str(fact.get("confidence") or "unknown")] += 1
        domains[str(fact.get("domain") or "other")] += 1
        tiers[tier_of(fact)] += 1
    conf = ", ".join(f"{n} {k}" for k, n in confidence.most_common())
    doms = ", ".join(f"{k} {n}" for k, n in domains.most_common(6))
    tier_parts = [f"{tiers[t]} {t}" for t in TIER_ORDER[1:] if tiers[t]]
    if tiers["withheld"]:
        tier_parts.append(f"{tiers['withheld']} withheld (untiered)")
    # withheld from angles: children, location, untiered-but-flagged, and
    # clinical unless the creator made it public themselves — discussed in 3+
    # videos (evidence-rules.md); a story framing is a judgment the merge pass
    # records as recurrence, the renderer only counts
    withheld = (sum(tiers[t] for t in WITHHELD) + tiers["withheld"]
                + sum(1 for f in facts if tier_of(f) == "clinical"
                      and int(f.get("recurrence") or 0) < 3))
    tier_text = (", ".join(tier_parts) + (f" — {withheld} withheld from angles" if withheld else "")
                 if tier_parts else "all at tier none")
    return [f"{len(facts)} facts: {conf}",
            f"sensitivity: {tier_text}",
            f"domains: {doms}"]


def coverage_line(meta: dict) -> str:
    """What the ledger can speak for, and the line that bounds it."""
    cov = meta.get("coverage") or {}
    parts = []
    if cov.get("videos_matched") and cov.get("videos_with_transcript"):
        parts.append(f"{cov['videos_matched']}/{cov['videos_with_transcript']} "
                     "transcript videos matched")
    if cov.get("windows_judged"):
        parts.append(f"{cov['windows_judged']} passages judged")
    return f"{', '.join(parts) if parts else 'coverage not recorded'} — absence is not evidence"


def build_line(meta: dict) -> str:
    """Format, corpus window, rounds and lanes: what built this ledger."""
    parts = []
    if meta.get("format"):
        parts.append(f"format: {meta['format']}")
    window = meta.get("corpus_window")
    if isinstance(window, str):
        parts.append(f"corpus {window.strip('[]')}")
    elif isinstance(window, (list, tuple)) and any(window):
        lo, hi = (list(window) + [None, None])[:2]
        parts.append(f"corpus {lo or '?'} → {hi or '?'}")
    if meta.get("lanes"):
        parts.append(f"lanes: {meta['lanes']}")
    if meta.get("rounds"):
        n = int(meta["rounds"])
        parts.append(f"{n} round{'s' if n != 1 else ''}")
    if meta.get("generated_at"):
        parts.append(f"built {meta['generated_at']}")
    return " · ".join(parts)


def context_section(meta: dict) -> str:
    """Linked platforms and sibling channels from the ledger header's channel
    context — what the run could have read and did not. Each platform says
    whether the socials lane read it; each sibling channel says "not mined"."""
    ctx = meta.get("context") or {}
    links = ctx.get("social_links") or []
    sibs = ctx.get("second_channel_candidates") or []
    if not links and not sibs:
        return ""
    lane_ran = str(meta.get("lanes") or "") == "transcripts+socials"
    # A lane that ran is not a lane that read everything: it is time-boxed, and
    # pages it never opened must not be reported as read. When the context
    # carries the per-link truth, honour it; otherwise fall back to the lane flag.
    def _norm(u: str) -> str:
        return re.sub(r"^(?:https?://)?(?:www\.)?", "", str(u).strip().rstrip("/")).lower()
    were_read = {_norm(u) for u in (ctx.get("social_links_read") or [])}
    per_link = bool(ctx.get("social_links_read") or ctx.get("social_links_unread"))
    items = []
    for link in links:
        raw = str(link)
        shown = html.escape(raw)
        if raw.lower().startswith(("http://", "https://")):
            shown = f'<a href="{html.escape(raw, quote=True)}">{shown}</a>'
        if per_link:
            note = "read (socials lane)" if _norm(raw) in were_read else "linked but unread"
        else:
            note = "read (socials lane)" if lane_ran else "linked but unread (socials lane not run)"
        items.append(f"<li>{shown} — {note}</li>")
    for c in sibs:
        name = html.escape(str(c.get("name") or c.get("link") or ""))
        ident = c.get("id") or c.get("channel_id")
        tail = f" (id {html.escape(str(ident))})" if ident else ""
        items.append(f"<li>{name}{tail} — not mined</li>")
    return ('<h3>Other channels and platforms</h3>'
            f'<ul class="links">{"".join(items)}</ul>')


def ledger_footer(facts: list[dict] | None, meta: dict) -> str:
    """"About this ledger" — the honesty surface the connections page carries
    for the ledger behind it. Superseded and withheld facts are counted here
    even though they never appear above."""
    if facts is None and not meta:
        return ""
    lines = tallies(facts or []) + [coverage_line(meta)]
    build = build_line(meta)
    if build:
        lines.append(build)
    items = "".join(f"<li>{html.escape(x)}</li>" for x in lines)
    return ('<h2>About this ledger</h2>'
            f'<div class="ledger">Every angle above comes from the machine ledger: '
            f'verified quotes only, each with its citation.'
            f'<ul class="tally">{items}</ul>{context_section(meta)}</div>')


# --------------------------------------------------------------------------- #
# who they are (connections page)
# --------------------------------------------------------------------------- #
def pick_who(facts: list[dict], *, max_facts: int = WHO_MAX_FACTS,
             per_domain: int = WHO_MAX_PER_DOMAIN) -> list[tuple[str, list[dict]]]:
    """Top recurring facts by domain: selected first, then recurrence, then
    confirmed. Superseded facts and the withheld tiers never appear."""
    usable = [f for f in facts
              if not f.get("superseded_by") and tier_of(f) not in WITHHELD
              and tier_of(f) != "withheld"]

    def key(f: dict):
        return (bool(f.get("selected")), int(f.get("recurrence") or 0),
                f.get("confidence") == "confirmed")

    usable.sort(key=key, reverse=True)
    by_domain: dict[str, list[dict]] = defaultdict(list)
    total = 0
    for f in usable:
        d = str(f.get("domain") or "other")
        if len(by_domain[d]) >= per_domain:
            continue
        by_domain[d].append(f)
        total += 1
        if total >= max_facts:
            break
    # domains ordered by how much of the ledger they hold — what the creator
    # talks about most comes first
    weight = Counter(str(f.get("domain") or "other") for f in usable)
    return sorted(by_domain.items(), key=lambda kv: -weight[kv[0]])


def short_quote(quote: str, words: int = 14) -> str:
    toks = quote.split()
    return " ".join(toks[:words]) + ("…" if len(toks) > words else "")


def pick_who_flat(facts: list[dict], *, max_facts: int = WHO_MAX_FACTS,
                  per_domain: int = WHO_MAX_PER_DOMAIN) -> list[dict]:
    """The same picks as ``pick_who``, flattened back into one ranked run.

    The domain grid this replaces read as a database view of a person rather
    than an introduction. The per-domain cap is kept, because it is what stops
    one talkative domain owning the whole strip, but the domains stop being
    headings."""
    out: list[dict] = []
    for _, items in pick_who(facts, max_facts=max_facts, per_domain=per_domain):
        out.extend(items)
    out.sort(key=lambda f: (bool(f.get("selected")),
                            int(f.get("recurrence") or 0),
                            f.get("confidence") == "confirmed"), reverse=True)
    return out[:max_facts]


def who_they_are(facts: list[dict], meta: dict, intro_html: str = "") -> str:
    picks = pick_who_flat(facts)
    fmt = meta.get("format")
    lead = []
    if fmt:
        lead.append(f"format: {fmt}")
    window = meta.get("corpus_window")
    if isinstance(window, (list, tuple)) and any(window):
        lo, hi = (list(window) + [None, None])[:2]
        lead.append(f"videos {str(lo or '?')[:7]} → {str(hi or '?')[:7]}")
    cov = meta.get("coverage") or {}
    if cov.get("facts"):
        lead.append(f"{cov['facts']} facts in the ledger")
    head = ('<h2>Who they are</h2>'
            + (f'<div class="about">{intro_html}</div>' if intro_html else "")
            + (f'<ul class="meta">{"".join(f"<li>{html.escape(x)}</li>" for x in lead)}</ul>'
               if lead else ""))
    if not picks:
        return head + '<p class="empty">The ledger holds no facts that can appear on a brand-facing page.</p>'
    lis = []
    for f in picks:
        claim = html.escape(str(f.get("claim") or ""))
        q = ""
        if f.get("quote"):
            q = html.escape(short_quote(str(f["quote"])))
            url = str(f.get("url") or "")
            if url.lower().startswith(("http://", "https://")):
                q += f' <a href="{html.escape(url, quote=True)}">watch</a>'
            q = f'<span class="q">“{q}”</span>' if q else ""
        lis.append(f'<li><span class="claim">{claim}</span>{tier_badge(f)}{q}</li>')
    return head + f'<ul class="who-run">{"".join(lis)}</ul>'


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
def page_html(title: str, eyebrow: str, header_extra: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<p class="eyebrow">{html.escape(eyebrow)}</p>
<h1>{html.escape(title)}</h1>
{header_extra}
</header>
{body}
</main>
</body>
</html>
"""


def default_out(in_path: pathlib.Path, facts_path: pathlib.Path | None,
                fm: dict) -> pathlib.Path:
    """The deliverable lands next to the ledger as
    ``<channel_id>-<brand_id>-connections.html``; without a ledger it lands
    next to its own markdown."""
    if facts_path is None:
        return in_path.with_suffix(".html")
    channel = re.sub(r"-facts$", "", facts_path.stem)
    brand = str(fm.get("brand_id") or "").strip()
    if not brand:
        m = (re.search(r"^connections-(.+)$", in_path.stem)
             or re.search(r"^\d+-([^-]+)-connections$", in_path.stem))
        brand = m.group(1) if m else ""
    name = (f"{channel}-{brand}-connections.html" if brand
            else f"{in_path.stem}.html")
    return facts_path.parent / name


def render_connections(md_text: str, facts: list[dict] | None, meta: dict) -> tuple[str, str]:
    fm, body = parse_frontmatter(md_text)
    creator = fm.get("channel_name") or meta.get("channel_name") or "Creator"
    brand = fm.get("brand_name") or fm.get("brand_id") or "Brand"
    title = f"{creator} × {brand}"
    body_html = render_markdown(body)
    m = re.search(r"<h1>(.*?)</h1>", body_html)
    if m:
        # the markdown's own H1 replaces the derived title (decode: it was
        # entity-escaped once already and the template escapes again)
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
        body_html = body_html.replace(m.group(0), "", 1)
    chips = []
    if fm.get("brand_read_date"):
        chips.append(f"brand read {fm['brand_read_date']}")
    if meta.get("generated_at"):
        chips.append(f"ledger built {meta['generated_at']}")
    header_extra = (f'<ul class="meta">{"".join(f"<li>{html.escape(c)}</li>" for c in chips)}</ul>'
                    if chips else "")
    intro, sections = split_sections(body_html)

    # One deliverable, in the order the reader needs it: who this is, the
    # argument, the evidence, then the brand, then the ranked angles, then the
    # honest mismatch. The thesis and the quotes sit ABOVE the brand strip
    # deliberately — the reader wants the case before the background.
    creator_about, brand_about, thesis, caveats, conns = [], [], [], [], []
    for sec in sections:
        if is_thesis(sec[0]):
            thesis.append(sec)
        elif is_caveat(sec[0]):
            caveats.append(sec)
        elif is_about(sec[0]):
            (creator_about if names_creator(sec[0], creator)
             else brand_about).append(sec)
        else:
            conns.append(sec)

    # the creator intro is prose inside "Who they are", not a card of its own
    creator_intro = "".join(rest for _, rest in creator_about)
    who = who_they_are(facts, meta, creator_intro) if facts is not None else (
        about_block(creator_about))
    body_out = (who
                + thesis_block(thesis)
                + quote_bridges(conns)
                + about_block(brand_about)
                + ("<h2>Connections</h2>" if conns or intro else "")
                + connection_cards(conns, intro)
                + caveat_block(caveats)
                + ledger_footer(facts, meta))
    return title, page_html(title, "creator × brand connection map", header_extra, body_out)


# --------------------------------------------------------------------------- #
# --check: the mechanical half of a QA pass, in the renderer
# --------------------------------------------------------------------------- #
# A second agent re-reading the first agent's page cost a measured 234 s and
# caught only things a script can check. These are those things. What a script
# cannot check — whether the thesis is any good — stays the connection pass's
# job and is not re-litigated by another model.
_MONEY = re.compile(r"(?<![\w-])(?:[$€£]\s?\d|\d+\s?(?:usd|eur|gbp)\b"
                    r"|\bcpm\b|\brate card\b|\bflat fee\b"
                    r"|\bper (?:video|integration|read)\b\s*[:=]?\s*[$€£\d])",
                    re.I)
# A quote's link must point at the moment it was said. The href is already
# entity-escaped by the renderer, so the separator can be "?", "&" or "&amp;".
_TIMED_LINK = re.compile(r'href="[^"]*(?:\?|&amp;|&)t=\d', re.I)


def check_page(md_text: str, facts: list[dict] | None, meta: dict) -> list[str]:
    """Contract problems with the deliverable, as one line each. Empty means
    the page is publishable."""
    problems: list[str] = []
    fm, body = parse_frontmatter(md_text)
    creator = fm.get("channel_name") or meta.get("channel_name") or ""
    _, sections = split_sections(render_markdown(body))

    kinds = {"thesis": [], "caveat": [], "creator": [], "brand": [], "conn": []}
    for sec in sections:
        if is_thesis(sec[0]):
            kinds["thesis"].append(sec)
        elif is_caveat(sec[0]):
            kinds["caveat"].append(sec)
        elif is_about(sec[0]):
            kinds["creator" if names_creator(sec[0], creator) else "brand"].append(sec)
        else:
            kinds["conn"].append(sec)

    no_fit = not kinds["conn"]
    for key, label in (("creator", f"## About {creator or '<creator>'}"),
                       ("thesis", "## Thesis"),
                       ("brand", "## About <brand>")):
        if not kinds[key]:
            problems.append(f"missing section: {label}")
    if not kinds["caveat"]:
        problems.append("missing section: ## Where this could go wrong — an "
                        "honest mismatch is required even on a strong fit")

    # every connection card must carry its evidence
    for title, rest in kinds["conn"]:
        name = plain(title)[:60]
        quote = _BLOCKQUOTE.search(rest)
        if not quote:
            problems.append(f"connection carries no quote: {name}")
        elif not _TIMED_LINK.search(quote.group(0)):
            problems.append(f"connection quote has no timestamped link: {name}")

    # the bans that survive the sample-read exception
    for m in _MONEY.finditer(html.unescape(re.sub(r"<[^>]+>", " ", body))):
        problems.append(f"price, cost or rate language on the page: "
                        f"{m.group(0).strip()!r}")
        break

    # withheld tiers never reach a brand-facing page
    for f in (facts or []):
        if tier_of(f) in WITHHELD and f.get("selected"):
            problems.append(f"selected fact at withheld tier "
                            f"{tier_of(f)}: {str(f.get('claim'))[:50]}")
    if no_fit and not kinds["thesis"]:
        problems = [p for p in problems if not p.startswith("missing section: ## Thesis")]
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--in", dest="infile", required=True,
                    help="the connection map's markdown, e.g. "
                         ".corpus/<channel_id>/connections-<brand_id>.md")
    ap.add_argument("--facts", default=None,
                    help="<channel_id>-facts.jsonl — the ledger, header first")
    ap.add_argument("--meta", default=None,
                    help="legacy <channel_id>-meta.json; read only when the "
                         "ledger carries no meta header")
    ap.add_argument("--out", default=None,
                    help="default: <facts dir>/<channel_id>-<brand_id>-connections.html, "
                         "or the input path with .html when --facts is omitted")
    ap.add_argument("--check", action="store_true",
                    help="validate the map against the page contract and exit "
                         "3 if it fails; writes nothing")
    a = ap.parse_args()

    facts_path = pathlib.Path(a.facts) if a.facts else None
    facts, meta = load_ledger(facts_path, pathlib.Path(a.meta) if a.meta else None)
    in_path = pathlib.Path(a.infile)
    text = in_path.read_text(encoding="utf-8")

    problems = check_page(text, facts, meta)
    if a.check:
        print(json.dumps({"in": str(in_path), "problems": problems,
                          "ok": not problems}, indent=1))
        raise SystemExit(3 if problems else 0)

    out_path = (pathlib.Path(a.out) if a.out
                else default_out(in_path, facts_path, parse_frontmatter(text)[0]))
    title, page = render_connections(text, facts, meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    print(json.dumps({"html": str(out_path), "title": title,
                      "problems": problems}))
    if problems:
        print("PAGE CONTRACT: " + "; ".join(problems), file=sys.stderr)


if __name__ == "__main__":
    main()
