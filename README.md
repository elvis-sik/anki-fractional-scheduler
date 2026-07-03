# Fractional New-Card Scheduler

[![AnkiWeb add-on 304166926](https://img.shields.io/badge/AnkiWeb-304166926-2f80ed)](https://ankiweb.net/shared/info/304166926)
[![Source on GitHub](https://img.shields.io/badge/source-GitHub-24292f)](https://github.com/elvis-sik/anki-fractional-scheduler)
![Anki 2.1.55+](https://img.shields.io/badge/Anki-%3E%3D2.1.55-4caf50)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)

Fractional New-Card Scheduler is an Anki add-on for decks that should introduce
new cards more slowly than Anki's normal whole-number daily limits allow.

Instead of choosing between `0/day` and `1/day`, you can schedule patterns such
as `1 every 3 days`, set different limits by weekday, and spread related decks
across a steady rotation. The add-on applies those plans through Anki's
Today-only new-card limits, so changes are temporary and reset with Anki's next
day.

It also includes deck health badges for decks that are blocked by `0/day` limits
or have no unsuspended new cards available.

## Install

Requires Anki 2.1.55 or newer.

### From AnkiWeb

1. In Anki, open `Tools -> Add-ons -> Get Add-ons...`.
2. Enter add-on code `304166926`.
3. Restart Anki.
4. Use `Tools -> Fractional Scheduler: Open Config` to edit config.

AnkiWeb page: <https://ankiweb.net/shared/info/304166926>

### From Source

1. Copy the `addon` folder into your Anki add-ons directory as its own folder,
   such as `FractionalScheduler`.
2. Restart Anki.
3. Use `Tools -> Fractional Scheduler: Open Config` to edit config.
4. Configure both scheduling and deck health badges from that dialog.

## Features

- Each schedule can enable fractional limits, notify badges, both, or neither.
- Every-N-days schedules such as 1 card every 3 days.
- Three fractional strategies: `balance_first`, `fraction_first`, and `hash`.
- Day-of-week schedules with separate values for Mon-Sun.
- Multiple deck targets per schedule using exact names or shell-style wildcards.
- `Pick deck...` adds exact targets immediately, and `Add wildcard...` adds wildcard targets from the deck picker.
- Optional stable staggering for `fraction_first` and day-of-week schedules.
- Leaf-only matching so container decks do not receive fractional limits.
- Per-schedule notify descendant modes: direct only, any blocked descendant, all blocked descendants, or hide container badges.
- Filtered decks are skipped.
- Automatic apply on profile open, collection open, and optionally just before sync, with an at-most-once-per-day guard.
- Preview table for the next 14 days, including daily totals, persistent column widths, and grouping by identical schedules.
- `Rebalance Offsets` recomputes stable stagger assignments for the currently matched decks in a schedule.
- Read-only API for other add-ons via `mw.fractional_scheduler_api`.

See [Scheduling Strategies](docs/scheduling-strategies.md) for the tradeoffs
between the fractional strategies.

## Usage Notes

- The config dialog autosaves; there is no separate Save button.
- `Apply Now` writes Today-only limits immediately for the matched decks.
- `Before sync` applies limits before collection sync when the installed Anki
  version exposes the pre-sync hook.
- `Rebalance Offsets` updates stored stagger assignments and preview data; deck
  limits change on the next apply.
- If you use a non-midnight Anki day rollover, the epoch calculation respects
  the rollover hour.
- `addon/meta.json` is local Anki state and is intentionally not tracked in git.

## Public API

The add-on registers a read-only service on `mw`:

```python
snapshot = mw.fractional_scheduler_api.get_schedule_health_snapshot(col)
```

It returns a dictionary keyed by deck id. Each value reports:

- `deck_id`
- `deck_name`
- `schedule_id`
- `cycle_length_days`
- `has_future_positive_limit`
- `next_positive_day_offset`

The service only includes matched, non-dynamic decks that survive `leaf_only`
filtering. A deck is considered to have a future positive limit if, within one
full schedule cycle, at least one day yields `> 0` new cards.

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

## Debug Logging

- Debug logging is off by default and no queue/history diagnostics are shown in the UI.
- To enable temporary scheduler diagnostics before Anki loads the add-on, start Anki with the environment variable `FRACTIONAL_SCHEDULER_DEBUG=1`.
- When enabled, the add-on appends JSON lines to `fractional-scheduler-debug.log` in the platform temporary directory.
- The log may include deck names, schedule ids, queue order, and introduction-history details, so treat it as potentially sensitive and delete it when you are done.
- When debug logging is not enabled, the add-on removes any old temporary debug log on profile/collection open.

## GUI Smoke Test

The repository has an `anki-addon-workbench` config and probe add-on for a disposable Anki GUI smoke test. With `anki-workbench` installed, run:

```bash
anki-workbench smoke --xvfb
```

The probe verifies that the add-on module loads and that
`Fractional Scheduler: Open Config` appears in Anki's Tools menu.

## Release

Release packaging uses the sibling `anki-addon-release` project.

```bash
make release
```

That target validates the manifest/listing metadata, builds
`dist/fractional-new-card-scheduler.ankiaddon`, inspects the archive contents,
and runs an AnkiWeb dry run. Use `make release-handoff` to write handoff files
for a browser/manual upload, or `make release-login` and `make release-publish`
when publishing through the release browser profile with credentials from a
git-ignored `.env` file.

## License

MIT. See [LICENSE](LICENSE).
