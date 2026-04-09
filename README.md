# Anki Fractional New-Card Scheduler

An Anki add-on for two related jobs:

- schedule fractional new cards per deck or deck groups, e.g. "1 every 3 days" or a day-of-week schedule, by automatically adjusting Today-Only new-card limits
- show deck-list warning badges when decks or monitored descendants are blocked by `0/day` limits or have no unsuspended new cards available

## Status
- Scheduler logic is implemented.
- The add-on includes a Qt config dialog for editing schedules, previewing the next 14 days in a scrollable table, and configuring automatic apply behavior.
- Limits are applied through Anki's deck-config update path so they affect the Today-Only new-card limit UI.

## Install (manual)
1. Copy the `addon` folder into your Anki add-ons directory as its own folder (e.g. `FractionalScheduler`).
2. Restart Anki.
3. Use `Tools -> Fractional Scheduler: Open Config` to edit config.
4. Configure notify badges in that same scheduler dialog; there is no separate Notify Empty Decks settings window anymore.

## Features
- Each schedule can enable fractional limits, notify badges, both, or neither.
- `Every N Days` schedules such as 1 card every 3 days.
- Three fractional strategies for `every_n_days`: `balance_first`, `fraction_first`, and `hash`.
- Day-of-week schedules with separate values for Mon-Sun.
- Multiple deck targets per schedule using exact names or shell-style wildcards.
- `Pick deck...` adds exact targets immediately, and `Add wildcard...` adds wildcard targets from the deck picker.
- Optional stable staggering for `fraction_first` and day-of-week schedules.
- Fractional-only `leaf_only` matching so container decks do not receive limits.
- Per-schedule notify descendant modes: direct only, any blocked descendant, all blocked descendants, or hide container badges.
- Filtered decks are skipped.
- Automatic apply on profile open, collection open, and optionally sync, with an at-most-once-per-day guard.
- Preview table for the next 14 days, including daily totals, persistent column widths, and grouping by identical schedules.
- `Rebalance Offsets` recomputes stable stagger assignments for the currently matched decks in a schedule.
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
      "fractional_strategy": "balance_first",
      "targets": ["Biology::*"],
      "fractional_enabled": true,
      "notify_enabled": true,
      "notify_descendant_mode": "direct_only",
      "leaf_only": true,
      "stagger": {"mode": "stable"}
    },
    {
      "id": "chemistry-notify",
      "type": "every_n_days",
      "m": 1,
      "n": 1,
      "targets": ["*Chemistry*"],
      "fractional_enabled": false,
      "notify_enabled": true,
      "notify_descendant_mode": "all_included_descendants_blocked",
      "leaf_only": true
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
- For `every_n_days`, the add-on uses a deterministic, front-loaded repeating integer pattern for both per-deck cadence and group-level daily budgets.
- `fraction_first` keeps each deck due as soon as its own cadence says it is owed another introduction, so missed days can bunch later work together.
- `balance_first` uses a stable deck queue plus a shared daily budget so missed introductions keep their place without creating a catch-up spike the next day.
- `hash` uses deterministic hashed offsets for a static spread that does not replay deck history.
- When stable staggering is enabled, existing decks keep their assigned offsets and newly matched decks are placed into the lightest current phase for that schedule.
- For `dow`, it applies the specified weekday limits (optionally rotated per deck if staggering is enabled).
- Matching is by exact deck name or shell-style wildcard.
- Notify schedules are assigned independently from fractional schedules, so a notify-only rule cannot steal fractional ownership from a deck.
- Notify exact targets can include descendants when the schedule's notify mode is not `direct_only`.
- Matching decks are grouped in the preview when they share the same visible schedule pattern.

## Notes
- If you use a non-midnight Anki day rollover, the epoch calculation respects the rollover hour.
- The config dialog autosaves; there is no separate Save button.
- `Rebalance Offsets` updates stored stagger assignments and preview data; deck limits change on the next apply.
- Notify badges are configured per schedule inside the main Fractional Scheduler dialog.
- `addon/meta.json` is local Anki state and is intentionally not tracked in git.
