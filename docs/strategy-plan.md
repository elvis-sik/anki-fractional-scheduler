# Strategy Plan

## Goals

1. Clean up current scheduler/config/UI inconsistencies before adding more behavior.
2. Support three real fractional scheduling strategies instead of one implicit strategy plus legacy config aliases.
3. Keep schedule behavior derivable from Anki history rather than maintaining fragile custom state.
4. Preserve predictable previews, deterministic results, and stable migration from existing configs.

## Immediate Cleanup Targets

### Real Wart: Fake Stagger Modes

Current code accepts three stagger mode values in config validation:

- `stable`
- `balanced`
- `hash`

But the code then immediately normalizes any of them to `stable`, and the UI only exposes:

- `Stable balanced`
- `Off`

So `balanced` and `hash` are not real current behaviors. That should be fixed first.

### Other Cleanup Passes To Do While We Are Here

- audit config naming so the persisted schema matches actual supported behavior
- remove compatibility shims that silently collapse distinct strategy names into one implementation
- make UI labels line up with the actual scheduler semantics
- separate "deck staggering / ordering" from "fractional release strategy" so these concepts are not overloaded
- make tests explicit about which strategy is being exercised

## Proposed Strategy Names

These are slightly more explicit than the current discussion names:

- `spread_hash`
- `cadence_first`
- `balance_first`

Possible UI labels:

- `Hash spread`
- `Cadence first`
- `Balance first`

If we want shorter internal names later, we can rename them, but these are clear enough for implementation.

## Strategy Definitions

### 1. `spread_hash`

Purpose:

- deterministic simple spreading across decks with no history-sensitive balancing

Behavior:

- assign each matched deck a stable order or phase from a hash-like deterministic rule
- compute the schedule's base repeating fractional budget pattern
- expose decks according to that static assignment
- missed introductions do not trigger queue repair logic

This is the simplest "static spread" option.

This can either reuse the current stable offset model directly, or become a deterministic deck-order model if we decide to unify the implementation shape.

### 2. `cadence_first`

Purpose:

- respect each deck's own fractional cadence as closely as possible, even if that creates bunching

Behavior:

- a deck becomes eligible once enough time has passed relative to its actual introduction history
- if a deck is overdue, it should continue to be eligible until it introduces again
- the strategy prefers not to defer an overdue deck merely to preserve group smoothness

Important note:

For arbitrary fractions, `cadence_first` should not just mean "at least `m` days since last introduction."

For `m/n`, we need to compare recent actual introduction history against the intended rolling average. One likely direction:

- fetch the last `n` distinct Anki days with introductions for the schedule or deck
- count how many introductions happened on each of those days
- determine whether introducing today would exceed the intended fractional pace

We should choose a deterministic rule that generalizes beyond easy cases like `1 every m days`.

### 3. `balance_first`

Purpose:

- preserve a smooth group-level workload and avoid catch-up spikes after missed or partial days

Behavior:

- each schedule has a stable ordered deck queue
- each day has a repeating release budget pattern derived from the fraction
- take the first `k` decks from the queue for today's budget
- when a deck actually introduces a new card, remove it from the front and append it to the back
- if a deck did not actually introduce a new card, it keeps its place

Why this works well:

- it naturally handles partial study
- it is robust when the user studies decks out of order
- it avoids "pay back the skipped day tomorrow" behavior

For arbitrary fractions, the repeating daily budget pattern must work for general `m/n`, not just `1/n`.

The likely direction is to derive a deterministic budget sequence from a Bresenham-style pattern over release counts per day.

## Cross-Cutting Design Decisions

### Separate Two Axes

We should separate:

- strategy: how eligibility / queue advancement is decided
- ordering: how decks are ordered or spread when the strategy needs an order

That gives cleaner semantics than using today's `stagger` field to carry too much meaning.

Possible shape:

- `strategy`
- `ordering`

Where `ordering` might start with:

- `stable`
- `hash`
- `none`

But we may also decide that `spread_hash` is itself a complete strategy and not an ordering. We should keep the schema simple and avoid exposing unnecessary knobs.

### Source Of Truth

Use Anki's database as the source of truth for actual new introductions.

We should derive:

- introduction days by deck
- counts per introduction day
- last successful introduction position for queue replay

We should not rely on custom mutable persistent queue state if replay from revlog is feasible.

### What Counts As An Introduction

Use the same revlog-backed logic the add-on already uses for new-card introductions and refactor that into reusable history helpers instead of duplicating query logic.

## Likely Data/Algorithm Work

### Shared History Helpers

Add helpers for:

- actual introduction day indices by deck
- distinct introduction days for a schedule
- introduction counts by Anki day
- maybe ordered introduction events by deck/day

These helpers should support both preview and actual limit computation.

### Daily Budget Pattern

For arbitrary `m/n`, compute a repeating integer daily budget sequence whose average is `m/n`.

Examples:

- `1/7` -> `0,0,0,0,0,0,1` or a rotated equivalent
- `3/4` -> `1,1,0,1`
- `5/7` -> `1,1,0,1,1,0,1`

We need one canonical deterministic construction so both preview and live application agree.

### `cadence_first` Generalization

This is the least settled piece.

We need a rule that says whether a deck is eligible today for arbitrary `m/n`.

Promising directions:

- replay the deck against the intended pattern and keep overdue releases pending
- compare actual introduction count inside a rolling window against the target count
- reconstruct the deck's effective schedule position from its real introduction days

My current preference is replay-based logic because it matches the add-on's current mental model and should stay deterministic.

### `balance_first` Queue Replay

For `balance_first`, we should be able to reconstruct the queue from history:

1. start from a stable ordered deck list
2. derive the daily budget pattern
3. replay past days
4. for each actual successful introduction, rotate that deck to the back
5. unresolved decks remain in place

This should let us compute:

- today's eligible decks
- preview rows for future days

without storing queue state in the config.

## Config And Migration Plan

### Config Shape

Add an explicit strategy field for fractional schedules, likely:

- `fractional_strategy`

Possible values:

- `spread_hash`
- `cadence_first`
- `balance_first`

### Migration

Existing schedules should migrate deterministically.

Tentative mapping:

- current staggered schedules -> `spread_hash` or `balance_first`, depending on which better matches existing user expectations
- existing implicit carry-forward logic probably maps closest to `cadence_first`

We should decide this carefully before coding, because migration semantics matter.

Current best guess:

- default new schedules to `balance_first`
- migrate existing schedules to `cadence_first` to preserve current behavior as much as possible

This needs one more pass while implementing.

### UI

Add a strategy control for fractional schedules.

The UI should:

- explain the difference briefly
- avoid exposing dead or duplicate modes
- update preview based on the selected strategy

If ordering remains relevant separately, keep that control subordinate and only show it when the selected strategy needs it.

## Testing Plan

### Cleanup Tests

- config normalization preserves truly supported modes only
- legacy values migrate explicitly rather than silently collapsing

### Shared History Tests

- day-index derivation from revlog rows
- counts per distinct introduction day
- grouped schedule history aggregation

### Strategy Tests

For `spread_hash`:

- deterministic deck assignment/order
- same config yields same plan across reloads

For `cadence_first`:

- overdue decks remain eligible
- arbitrary fractions do not regress to only the `1/n` case
- partial study does not break deck-specific cadence tracking

For `balance_first`:

- queue head remains stable when the user skips a scheduled deck
- introduced decks rotate to the back
- out-of-order study still results in correct queue repair
- arbitrary fractions produce the intended repeating daily budget

### Preview Tests

- preview reflects the selected strategy
- balance-first preview shows repaired future days rather than naive static offsets

## Implementation Sequence

1. Write and land the plan doc.
2. Clean up fake strategy/mode handling in config and UI.
3. Refactor shared revlog history helpers out of the current schedule code.
4. Introduce explicit fractional strategy config plumbing.
5. Implement `spread_hash`.
6. Implement `cadence_first`.
7. Implement `balance_first`.
8. Update preview generation.
9. Update README and config example.
10. Run tests and any formatter/lint commands available in the repo.
11. Fix failures and make a final cleanup pass.

## Commit Plan

Planned checkpoints:

1. plan doc + cleanup of fake/legacy mode handling
2. shared history helper refactor + tests
3. strategy plumbing + first implemented strategy
4. remaining strategies + preview updates
5. docs/tests/final cleanup

## Open Questions

- Should `spread_hash` be a true top-level strategy, or just an ordering mode used by another strategy?
- What is the cleanest replay rule for arbitrary-fraction `cadence_first`?
- How should existing schedules migrate by default?
- Should day-of-week schedules gain these strategies too, or should this first pass only apply to `every_n_days`?
