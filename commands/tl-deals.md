---
name: tl-deals
description: Quick deal/sponsorship lookup. Query, filter, or show details for sponsorship deals.
---

# /tl-deals — Deal Lookup

The user wants to query sponsorship deals.

1. Run `tl describe deals --json` to discover filters
2. Translate the user's request into a `tl deals` command
3. Execute and present results

If no specific request is given, run `tl deals --limit 10` to show recent deals.

Examples:
- "/tl-deals pending with send dates in April" → `tl deals status:pending send-date:2026-04`
- "/tl-deals Nike" → `tl deals brand:"Nike"`
- "/tl-deals 12345" → `tl deals 12345`
