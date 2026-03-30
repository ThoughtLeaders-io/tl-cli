---
name: tl-reports
description: List and run saved reports. View your organization's saved reports and execute them.
---

# /tl-reports — Saved Reports

The user wants to see or run their saved reports.

1. Run `tl reports --json` to list saved reports
2. Present the list with IDs and titles
3. If the user specifies a report, run it with `tl reports run <id> --json`

Examples:
- "/tl-reports" → `tl reports`
- "/tl-reports run my Q1 pipeline" → list reports, find matching one, `tl reports run <id>`
