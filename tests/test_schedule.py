from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

import schedule  # noqa: E402


class ShiftedEveryNDaysIndexTests(unittest.TestCase):
    def test_skipped_positive_day_shifts_next_release_forward(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=8,
            schedule=sched,
            deck_name="Deck",
            deck_names=["Deck"],
            introduced_day_indices={0},
        )

        self.assertEqual(effective_day_index, 7)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, "Deck", ["Deck"], effective_day_index),
            1,
        )

    def test_used_positive_day_does_not_shift_cycle(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=8,
            schedule=sched,
            deck_name="Deck",
            deck_names=["Deck"],
            introduced_day_indices={0, 7},
        )

        self.assertEqual(effective_day_index, 8)

    def test_consecutive_skips_hold_pending_release_in_place(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=10,
            schedule=sched,
            deck_name="Deck",
            deck_names=["Deck"],
            introduced_day_indices={0},
        )

        self.assertEqual(effective_day_index, 7)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, "Deck", ["Deck"], effective_day_index),
            1,
        )

    def test_no_history_keeps_calendar_anchor(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=30,
            schedule=sched,
            deck_name="Deck",
            deck_names=["Deck"],
            introduced_day_indices=set(),
        )

        self.assertEqual(effective_day_index, 30)


if __name__ == "__main__":
    unittest.main()
