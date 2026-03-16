# Plan: Fractional New-Card Scheduler Add-on

## Goals
- Allow per-deck or per-group new-card schedules like "m every n days" where m < n.
- Allow day-of-week schedules (e.g. Mon:2 Tue:0 Wed:1 Thu:0 Fri:1 Sat:0 Sun:0).
- Apply schedules automatically with zero manual clicking.
- Let one schedule apply to multiple decks via deck selectors.
- Keep configuration as text (JSON) but make room for a simple GUI editor later.

## Non-Goals (initial)
- Cross-device synchronization of config beyond standard Anki add-on sync expectations.
- Complex per-card or tag-specific scheduling (deck-level only for v1).
- Replacement of Anki's scheduler logic; we only adjust Today-Only new limits.

## Core Behavior
- Each day at collection open and optionally at profile open, compute the "today-only" new-card limit for each target deck based on its schedule.
- For an "m every n days" schedule, distribute m new cards across n days deterministically so that every n-day window yields exactly m cards per deck.
- For a day-of-week schedule, apply the explicit number for the current weekday.
- When a schedule targets multiple decks, optionally stagger decks so they do not all introduce new cards on the same days.
- Respect parent deck max new cards if set; treat child schedules as caps (not guarantees).

## Scheduling Model
### 1) m every n days
- Deterministic cadence using a fixed epoch and phase per schedule.
- For a given schedule with (m,n):
  - Compute dayIndex = days_since_epoch % n.
  - Precompute a pattern array of length n with exactly m ones (or integers > 1 for m > 1) and n-m zeros.
  - Use a stable, deterministic pattern (evenly spaced via Bresenham-style spacing) so behavior is predictable and testable.
- Example: m=1, n=3 -> [1,0,0] repeating.
- Example: m=2, n=5 -> [1,0,1,0,0] (evenly spaced).

### 2) Day-of-week
- Simple mapping: {Mon:2, Tue:0, Wed:1, Thu:0, Fri:1, Sat:0, Sun:0}.
- Use local time zone, day starts at collection day rollover.

### 3) Priority and conflicts
- If multiple schedules match the same deck, choose the most specific rule:
  1. Exact deck match
  2. Deck prefix match (e.g. "Languages::*")
  3. Tag or regex match if supported in future
- If still multiple, higher priority wins (explicit integer priority in config).

### 4) Staggering within a schedule
- Default strategy for schedules targeting multiple decks: balanced phase assignment.
  - Collect matching decks, sort by name.
  - Assign phase = index % n for the schedule, so decks distribute evenly across n days.
  - Deterministic and load-balanced; adding/removing decks will shift phases for some decks.
- Optional strategy: hash-based phase using a stable hash of deck name + schedule id.
  - Deterministic, minimal shifting when deck list changes.
  - Not guaranteed to be evenly balanced.

## Deck Targeting
- Targets are deck name patterns. Each target is either:
  - Exact name (e.g. "Biology::Immunology"), or
  - Prefix match using a trailing `*` (e.g. "Biology::*" matches all decks under Biology).
- A schedule can include a list mixing exact and prefix targets.
- Allow "groups" of decks defined by name patterns.

## Config Format (JSON)
- Stored in add-on config with schema:
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
      "priority": 10,
      "stagger": {"mode": "balanced"}
    },
    {
      "id": "langs-weekly",
      "type": "dow",
      "by_day": {"Mon": 2, "Tue": 0, "Wed": 1, "Thu": 0, "Fri": 1, "Sat": 0, "Sun": 0},
      "targets": ["Languages::Japanese", "Languages::Korean", "Languages::EastAsian::*"],
      "priority": 20,
      "stagger": {"mode": "hash", "seed": "langs-v1"}
    }
  ],
  "defaults": {
    "apply_on_profile_open": true,
    "apply_on_collection_open": true,
    "dry_run": false
  }
}
```

## Algorithm Details
- Compute local "today" using Anki collection day start.
- Derive dayIndex from epoch date (configurable) to keep schedules stable across machines.
- Build the pattern for (m,n) using a spacing algorithm:
  - Use a simple Bresenham-like distribution: for i in [0..n-1], increment accumulator by m; if accumulator >= n, place 1 and accumulator -= n else 0.
  - This evenly distributes m ones over n slots deterministically.
- If m > n, reject config; if m == 0, set today-only to 0.
- If stagger is enabled, shift dayIndex by the per-deck phase before indexing the pattern.

## Apply Logic
- For each deck:
  - Determine matching schedule.
  - Compute today-only new limit for that deck.
  - Apply only if value differs from current today-only.
- If a parent deck has its own schedule, apply it; child decks still receive their own caps. Parent cap should not be increased beyond schedule.
- Optionally, allow "min" or "max" with existing deck options; default is "override today-only".

## GUI (Optional Phase)
- Simple Qt dialog listing schedules with add/edit/delete.
- Schedule editor layout:
  - Header: name/id, type selector (every-n-days or day-of-week), and priority.
  - Every-n-days: m and n inputs, stagger mode dropdown (balanced/hash), optional seed.
  - Day-of-week: 7 numeric fields for Mon-Sun, stagger mode dropdown for balanced/hash.
  - Targets: list with add/remove; each entry is a deck selector with a prefix toggle that writes a trailing `*`.
  - Preview: read-only table for the next 14 days per target deck, to visualize staggering.
- Save to add-on config (JSON) so power users can still edit directly.

## Testing
- Unit tests for pattern generation and dayIndex calculation.
- Integration test on a mock collection to ensure today-only limits are written correctly.
- Edge cases: DST changes, custom day start, epoch changes.

## File Layout (Proposed)
- `addon/` (Anki add-on root)
  - `__init__.py` entrypoint
  - `config.py` load/validate config
  - `schedule.py` compute today values
  - `apply.py` write today-only limits
  - `ui.py` (optional)
  - `tests/`

## Milestones
1. Implement config + schedule calculation + apply on collection open.
2. Add deterministic pattern algorithm and tests.
3. Add GUI editor (optional).
4. Add documentation and examples.
