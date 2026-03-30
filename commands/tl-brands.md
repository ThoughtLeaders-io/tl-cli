---
name: tl-brands
description: Brand intelligence lookup. Research a brand's sponsorship activity and channel mentions.
---

# /tl-brands — Brand Intelligence

The user wants to research a brand's sponsorship activity. Requires Intelligence plan.

1. Run `tl describe brands --json` to discover filters
2. Translate the user's request into a `tl brands` command
3. Execute and present results

Examples:
- "/tl-brands Nike" → `tl brands Nike`
- "/tl-brands Nike on channel 12345" → `tl brands Nike --channel 12345`
