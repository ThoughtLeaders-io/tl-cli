---
name: tl-sponsorships
description: Quick sponsorship lookup. Query, filter, or show details for sponsorships.
---

# /tl-sponsorships — Sponsorship Lookup

The user wants to query sponsorships.

1. Run `tl describe sponsorships --json` to discover filters
2. Translate the user's request into a `tl sponsorships` command
3. Execute and present results

If no specific request is given, run `tl sponsorships list --limit 10` to show recent sponsorships.

Examples:
- "/tl-sponsorships pending with send dates in April" → `tl sponsorships list status:pending send-date:2026-04`
- "/tl-sponsorships Nike" → `tl sponsorships list brand:"Nike"`
- "/tl-sponsorships sold deals on mobile-first channels" → `tl sponsorships list status:sold primary-device:mobile`
- "/tl-sponsorships deals on channels with majority US audience" → `tl sponsorships list min-us-share:50`
- "/tl-sponsorships 12345" → `tl sponsorships show 12345`
