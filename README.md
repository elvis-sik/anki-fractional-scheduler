# Anki Fractional New-Card Scheduler

An Anki add-on to schedule fractional new cards per deck or deck groups, e.g. "1 every 3 days" or a day-of-week schedule, by automatically adjusting Today-Only new-card limits.

## Status
- Scheduler logic is implemented.
- The add-on includes a Qt config dialog for editing schedules, previewing the next 14 days in a scrollable table, and configuring automatic apply behavior.
- Limits are applied through Anki's deck-config update path so they affect the Today-Only new-card limit UI.

## Install (manual)
1. Copy the `addon` folder into your Anki add-ons directory as its own folder (e.g. `FractionalScheduler`).
2. Restart Anki.
3. Use `Tools -> Fractional Scheduler: Open Config` to edit config.
4. Use `Tools -> Apply Fractional Schedule Now` to force an immediate apply.

## Features
- `Every N Days` schedules such as 1 card every 3 days.
- Day-of-week schedules with separate values for Mon-Sun.
- Multiple deck targets per schedule using exact names or wildcard prefixes.
- Optional staggering across matched decks: stable balanced or off.
- Leaf-only matching so container decks do not receive limits.
- Filtered decks are skipped.
- Automatic apply on profile open, collection open, and optionally sync, with an at-most-once-per-day guard.
- Preview table for the next 14 days, including daily totals, persistent column widths, and grouping by identical schedules.
- Immediate manual apply action from the Tools menu.
- Read-only API for other add-ons via `mw.fractional_scheduler_api`.

## Public API

The add-on registers a read-only service on `mw`:

```python
snapshot = mw.fractional_scheduler_api.get_schedule_health_snapshot(col)
```

It returns a dict keyed by deck id. Each value reports:

- `deck_id`
- `deck_name`
- `schedule_id`
- `cycle_length_days`
- `has_future_positive_limit`
- `next_positive_day_offset`

The service only includes matched, non-dynamic decks that survive `leaf_only` filtering. A deck is considered to have a future positive limit if, within one full schedule cycle, at least one day yields `> 0` new cards.

## Config Example
```json
{
  "epoch": "2026-01-01",
  "schedules": [
    {
      "id": "bio-slow",
      "type": "every_n_days",
      "m": 1,
      "n": 3,
      "targets": ["Biology::*"],
      "leaf_only": true,
      "stagger": {"mode": "stable"}
    },
    {
      "id": "langs-weekly",
      "type": "dow",
      "by_day": {"Mon": 2, "Tue": 0, "Wed": 1, "Thu": 0, "Fri": 1, "Sat": 0, "Sun": 0},
      "targets": ["Languages::Japanese", "Languages::Korean", "Languages::EastAsian::*"],
      "leaf_only": true,
      "stagger": {"mode": "stable"}
    }
  ],
  "defaults": {
    "apply_on_profile_open": true,
    "apply_on_collection_open": true,
    "apply_on_sync": false,
    "apply_once_per_day": true,
    "dry_run": false,
    "log_level": "info"
  }
}
```

## How It Works
- For `every_n_days`, the add-on uses a deterministic, evenly spaced pattern (Bresenham-style) and optional staggering per deck.
- For `every_n_days`, a day with `0` newly introduced cards does not consume that deck's slot; the cycle resumes when you actually introduce the next new card.
- When staggering is enabled, existing decks keep their assigned offsets and newly matched decks are placed into the lightest current phase for that schedule.
- For `dow`, it applies the specified weekday limits (optionally rotated per deck if staggering is enabled).
- Matching is by exact deck name or prefix using a trailing `*`.
- Matching decks are grouped in the preview when they share the same visible schedule pattern.

## Notes
- If you use a non-midnight Anki day rollover, the epoch calculation respects the rollover hour.
- The config dialog autosaves; there is no separate Save button.
- `addon/meta.json` is local Anki state and is intentionally not tracked in git.
