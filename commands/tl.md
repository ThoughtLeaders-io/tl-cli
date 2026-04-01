---
name: tl
description: Smart router for ThoughtLeaders data queries. Translates your request into the right tl CLI command(s).
---

# /tl — ThoughtLeaders Query Router

The user wants to query ThoughtLeaders data. Translate their request into the right `tl` CLI command.

## Steps

1. Identify which resource(s) the request is about (sponsorships, deals, channels, brands, uploads, snapshots, reports)
2. Run `tl describe show <resource> --json` to discover available filters
3. Translate the user's natural language into a `tl` command with appropriate filters
4. Execute the command
5. Present results clearly

## Examples

- "/tl sold sponsorships for Nike in Q1" → `tl sponsorships list status:sold brand:"Nike" since:2026-01-01 until:2026-03-31`
- "/tl cooking channels over 100k subs" → `tl channels list category:cooking min-subs:100000`
- "/tl Nike's sponsorship activity" → `tl brands show Nike`
- "/tl run my Q1 report" → `tl reports --json` then `tl reports run <id>`
- "/tl check my balance" → `tl balance`
- "/tl show sponsorship 12345" → `tl sponsorships show 12345`

If the request is complex and requires multiple queries, delegate to the tl-analyst agent.
