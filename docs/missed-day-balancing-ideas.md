# Missed-Day Balancing Ideas

This is a historical design note for the `balance_first` strategy documented in
[Scheduling Strategies](scheduling-strategies.md). The ordered-rotation approach
described below is now implemented; the alternate designs are kept as rationale
for future scheduling changes.

## Problem

The add-on already has an important behavior for `every_n_days` schedules: if a deck was supposed to introduce a new card on a given day and that introduction did not happen, that release stays pending instead of being lost.

That is the right baseline behavior. If I skip today because I am tired, I should still be able to see that deck's new card tomorrow.

The remaining problem is balancing across a grouped schedule:

- A schedule may contain many decks that are intentionally staggered to keep the daily load smooth.
- If missed releases are carried forward independently per deck, several decks can pile up onto the next study day.
- That preserves "don't lose the card," but it weakens the load-balancing benefit that made grouped schedules useful in the first place.

So the design target is not just "carry forward or not." We want both:

- missed releases should not be lost
- grouped schedules should stay reasonably balanced after missed days

## What A Good Solution Should Preserve

- Predictability: users should be able to understand why a deck is showing a new card today.
- Fairness: a deck should not get starved forever because other decks are also waiting.
- Smoothness: a missed day should not cause an unnecessary spike on the next day.
- Simplicity: this should fit the current mental model of schedules, staggering, and previews.
- Determinism: the same history should produce the same future plan.
- Low state cost: preferably derive state from Anki history instead of maintaining complex custom bookkeeping.

## Leading Idea: Ordered Rotation With Balance First

### Core Idea

Treat a grouped schedule as an ordered rotation of decks plus a daily release budget.

For example, if the group should average `20 / 7` new cards per day, the budget pattern might be:

- day 1: 3 decks
- day 2: 3 decks
- day 3: 3 decks
- day 4: 4 decks
- then repeat

Each day, we take the next `k` decks from the ordered schedule queue and allow one new card from each of those decks.

If the user studies only some of those decks, only the decks that actually introduced a new card advance past their turn. Any scheduled deck that did not actually introduce a new card stays at the front of the queue for the next day.

### Example

Suppose today's budget is `3`, and the next decks in order are:

- `A`
- `B`
- `C`

If the user introduces new cards from `A` and `B` but not `C`, then tomorrow `C` should be first in line. If tomorrow's budget is also `3`, the queue for tomorrow would effectively begin:

- `C`
- `D`
- `E`

This gives us a small amount of slippage at the margin, but the overall balance remains intact instead of turning into catch-up spikes.

### Why This Is Appealing

- It is balance-first rather than catch-up-first.
- It matches the stated user preference: if a day is missed, do not force extra work the next day.
- It preserves smooth daily load much better than independent per-deck carry-forward.
- It is still easy to explain: "the schedule has an order, and unfinished decks keep their place."
- It does not need a large custom state machine if we derive progress from Anki history.

### Important Consequence

This intentionally allows some decks to wait a little longer than their nominal cadence after missed days.

That is not a bug under this strategy. It is the tradeoff we choose in order to preserve overall balance and avoid catch-up pressure.

## How To Derive Progress From The Anki Database

This idea fits well with the current implementation direction because the add-on already looks at Anki revlog history to infer when new cards were actually introduced.

The key capability we need is:

- for each deck in the schedule group, determine the last Anki day on which a new card was actually introduced from that deck

That should come from Anki's database, not from custom persistent add-on state.

Conceptually, we can derive:

- actual introduction days per deck
- how many successful turns each deck has consumed
- which decks are still waiting at the front of the ordered rotation

This is attractive because:

- it survives restarts naturally
- it stays in sync with what the user really studied
- it avoids having to maintain separate "queue position" state that can drift

## Design 2: Schedule-Wide Freeze On Fully Missed Days

### Idea

Treat the schedule as a shared stream for balancing purposes. If none of the decks in the schedule introduced a new card on a calendar day, the schedule does not advance at all for that day.

In effect, the whole schedule shifts forward by one day together.

### Why It Helps

- It preserves the original spread very well when the user missed the day entirely.
- It matches the intuition that the schedule should not move on without the user.
- It is simpler than a full ordered-rotation model.

### Downsides

- It handles full missed days well, but not partial misses.
- If the user studies some of the scheduled decks but not all, we still need another rule.

### Complexity

Low to moderate.

This is still a strong fallback if we want a narrower first implementation.

## Design 3: Hybrid Freeze

### Idea

Combine two rules:

- if a schedule had zero new introductions on a day, freeze the whole schedule for that day
- if the user introduced some scheduled new cards but not all of them, keep the missed decks pending individually

### Why It Helps

- It handles the common "I skipped the whole day" case well.
- It changes less behavior relative to the current implementation.

### Downsides

- Partial misses can still create bunching.
- It does not address the user's stated preference against catch-up as directly as the ordered-rotation idea.

### Complexity

Moderate.

## Design 4: Queue Missed Releases And Spend A Daily Budget

### Idea

Convert missed releases into a schedule-level backlog queue.

Each day, the schedule gets a budget close to its intended average daily load. The scheduler then spends that budget on the oldest or most overdue pending releases first, instead of exposing every overdue deck at once.

### Why It Helps

- It smooths spikes after missed days.
- It generalizes well to long absences.

### Downsides

- It is still a catch-up model.
- A deck missed yesterday is not necessarily shown tomorrow.
- It is less aligned with the "balance first" preference.

### Complexity

Moderate to high.

## Design 5: Backlog Queue With A Small Catch-Up Allowance

### Idea

Use the same queue idea as Design 4, but allow a little extra catch-up beyond the normal daily budget.

For example:

- normal schedule budget, plus one extra overdue release per day
- or normal schedule budget, plus up to 25% catch-up

### Why It Helps

- It reduces spikes without making catch-up too slow.

### Downsides

- Still a catch-up strategy.
- Still requires extra ordering and tuning rules.

### Complexity

Moderate.

## Design 6: Re-Slot Missed Releases Into The Lightest Upcoming Days

### Idea

When a release is missed, reassign it to the lightest day inside a short repair window, such as the next 3 to 7 days.

### Why It Helps

- It spreads recovery load across several days.

### Downsides

- Harder to explain.
- Harder to preview.
- More complex than the problem seems to warrant right now.

### Complexity

High.

## Recommendation

The strongest current candidate is the ordered-rotation, balance-first model.

If we expose multiple strategies, a clean split would be:

1. `Balance first`
2. `Catch up gradually`

Where those mean:

- `Balance first`: ordered rotation with a daily budget, and unfinished decks remain at the front
- `Catch up gradually`: backlog queue with a capped catch-up allowance

If we expose only one strategy at first, `Balance first` seems like the best default for this add-on.

Reasons:

- it directly matches the stated user preference
- it avoids "punishing" skipped days with heavier future days
- it preserves the main value of grouped scheduling, which is smooth load balancing
- it seems implementable from Anki revlog history rather than fragile custom state

## Notes On Implementation Shape

One likely implementation shape is:

1. Define a stable ordered deck list per schedule.
2. Define the repeating daily budget pattern for the schedule.
3. Read actual new-card introduction history from Anki revlog by deck and Anki day.
4. Infer how many successful turns each deck has consumed.
5. Compute today's queue head by replaying completed turns, not by storing mutable queue state.
6. Offer new cards only to the first `k` eligible decks in today's budget.

A useful tie-break question is how to define the stable order:

- existing stagger order
- alphabetical by deck name
- deck id
- explicit manual order later, if needed

My preference for a first pass would be a deterministic automatic order, not a new manual ordering UI.

## Open Questions

- Should the ordered rotation apply only to `every_n_days`, or also to day-of-week schedules?
- How exactly should the stable deck order be chosen?
- Should a deck count as having consumed its turn only when a new card was actually introduced, or do we need to treat some edge cases differently?
- How should the preview table visualize the queue head and upcoming budget pattern?
- Do we want to keep the current per-deck carry-forward behavior as an alternate strategy, or replace it entirely?
