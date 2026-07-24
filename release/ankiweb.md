---
title: Fractional New-Card Scheduler
support_url: https://github.com/ritornello-labs/anki-fractional-scheduler
---

Fractional New-Card Scheduler lets you pace new cards below whole-number daily limits, such as introducing 1 new card every 3 days, while still using Anki's Today-only new-card limits.

Use it when some decks deserve a slow trickle of new material instead of a fixed whole number every day. The add-on can target exact decks or wildcard deck groups, preview the next 14 days, and apply the resulting Today-only limits automatically on profile open, collection open, or before sync.

It also includes deck health badges: schedule rules can mark decks when a deck or its monitored descendants are blocked by 0/day limits or have no unsuspended new cards available.

Screenshot: [Fractional Scheduler config dialog](https://raw.githubusercontent.com/elvis-sik/anki-fractional-scheduler/main/docs/images/config-window.png)

<img alt="Fractional Scheduler config dialog" src="https://raw.githubusercontent.com/elvis-sik/anki-fractional-scheduler/main/docs/images/config-window.png" width="760">

Features:

- Every-N-days schedules, including fractional patterns like 1 every 3 days.
- Day-of-week schedules with separate values for Monday through Sunday.
- Multiple exact or wildcard deck targets per schedule.
- Exact targets follow their deck through direct and parent-deck renames.
- Stable staggering so related decks can be spread across different days.
- Balance-first scheduling for grouped decks, designed to avoid catch-up spikes.
- Optional notify badges per schedule.
- A config dialog with schedule editing, target picking, and preview tables.

Requires Anki 2.1.55 or newer.

GitHub: [https://github.com/ritornello-labs/anki-fractional-scheduler](https://github.com/ritornello-labs/anki-fractional-scheduler)
