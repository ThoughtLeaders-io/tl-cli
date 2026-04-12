---
name: tl-channels
description: Channel search and lookup. Find YouTube channels by category, subscribers, language, or other criteria.
---

# /tl-channels — Channel Search

The user wants to search or look up YouTube channels.

1. Run `tl describe show channels --json` to discover filters
2. Translate the user's request into a `tl channels` command
3. Execute and present results

Examples:
- "/tl-channels cooking channels over 100k" → `tl channels list category:cooking min-subs:100000`
- "/tl-channels 12345" → `tl channels show 12345`
- "/tl-channels English gaming channels" → `tl channels list category:gaming language:en`
- "/tl-channels mobile-first channels" → `tl channels list primary-device:mobile`
- "/tl-channels channels with majority US audience" → `tl channels list min-us-share:50`
- "/tl-channels mobile tech channels with US focus" → `tl channels list category:tech primary-device:mobile min-us-share:60`
