# Scheduling Strategies

Every-N-days schedules use a fractional strategy to decide which matched decks
receive a Today-only new-card limit on a given Anki day.

All strategies are deterministic. They use the schedule id, matched deck
identity, configured epoch, and Anki's new-card introduction history rather than
randomness.

## Balance First

`balance_first` is the default for new schedules.

Use it when a wildcard or group target contains many small decks and you want the
combined workload to stay smooth. The schedule has a stable deck queue and a
shared daily budget derived from the configured fraction. Decks that actually
introduce a new card move to the back of the queue. Decks that were available
but not studied keep their place.

This means a skipped day does not create a catch-up spike the next day. The
tradeoff is that an individual deck may wait a little longer than its nominal
cadence when other decks are also waiting.

## Fraction First

`fraction_first` treats each matched deck as having its own cadence.

Use it when each deck should become due as soon as its own fractional schedule
says it is owed another introduction. This is the closest fit for "this deck
should be available every N days" thinking.

Because each deck is evaluated independently, several decks can become due at
the same time after missed days.

Stable staggering can be enabled with this strategy. Existing matched decks keep
their stored offset, and newly matched decks are assigned to the lightest phase.

## Hash Spread

`hash` uses deterministic hashed offsets for a static spread.

Use it when you want a simple, history-insensitive distribution across decks.
The same deck and schedule keep the same phase, but the strategy does not replay
introduction history to repair missed days.

## Day-Of-Week Schedules

Day-of-week schedules do not use fractional strategies. They apply the configured
weekday limits directly. Stable staggering can rotate those weekday values across
matched decks.

## Choosing A Strategy

- Choose `balance_first` for deck groups where smooth total load matters most.
- Choose `fraction_first` when each deck's own cadence matters most.
- Choose `hash` when you want a static spread that ignores study history.
