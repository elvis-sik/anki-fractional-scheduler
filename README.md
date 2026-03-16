# Anki Fractional New-Card Scheduler

An Anki add-on to schedule fractional new cards per deck or deck groups, e.g. "1 every 3 days" or a day-of-week schedule, by automatically adjusting Today-Only new-card limits.

## Status
- Scheduler logic is implemented.
- The add-on includes a Qt config dialog for editing schedules, previewing the next 14 days, and configuring automatic apply behavior.
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
- Optional staggering across matched decks: balanced, hashed, or off.
- Leaf-only matching so container decks do not receive limits.
- Automatic apply on profile open, collection open, and optionally sync, with an at-most-once-per-day guard.
- Preview for the next 14 days inside the config dialog.

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
      "stagger": {"mode": "balanced"}
    },
    {
      "id": "langs-weekly",
      "type": "dow",
      "by_day": {"Mon": 2, "Tue": 0, "Wed": 1, "Thu": 0, "Fri": 1, "Sat": 0, "Sun": 0},
      "targets": ["Languages::Japanese", "Languages::Korean", "Languages::EastAsian::*"],
      "leaf_only": true,
      "stagger": {"mode": "hash", "seed": "langs-v1"}
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
- For `dow`, it applies the specified weekday limits (optionally rotated per deck if staggering is enabled).
- Matching is by exact deck name or prefix using a trailing `*`.

## Notes
- If you use a non-midnight Anki day rollover, the epoch calculation respects the rollover hour.
- Filtered decks are skipped.
- The preview limits itself to the first matching decks when many decks match a wildcard target.
