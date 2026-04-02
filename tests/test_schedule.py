from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

import schedule  # noqa: E402


def deck(deck_id: int, name: str) -> schedule.DeckInfo:
    return schedule.DeckInfo(deck_id=deck_id, name=name)


class ShiftedEveryNDaysIndexTests(unittest.TestCase):
    def test_skipped_positive_day_shifts_next_release_forward(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=8,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices={0},
        )

        self.assertEqual(effective_day_index, 7)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, decks[0], decks, effective_day_index),
            1,
        )

    def test_used_positive_day_does_not_shift_cycle(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=8,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices={0, 7},
        )

        self.assertEqual(effective_day_index, 8)

    def test_consecutive_skips_hold_pending_release_in_place(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=10,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices={0},
        )

        self.assertEqual(effective_day_index, 7)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, decks[0], decks, effective_day_index),
            1,
        )

    def test_no_history_keeps_calendar_anchor(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=30,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices=set(),
        )

        self.assertEqual(effective_day_index, 30)


class StableBalancedPhaseTests(unittest.TestCase):
    def test_new_deck_fills_lightest_phase_without_moving_existing_decks(self) -> None:
        sched = {"type": "every_n_days", "n": 3, "m": 1, "stagger": {"mode": "stable"}}
        decks_before = [deck(1, "A"), deck(2, "B"), deck(3, "C")]
        phases_before = schedule._assign_phases(sched, decks_before)

        self.assertEqual(phases_before, {1: 0, 2: 1, 3: 2})

        decks_after = decks_before + [deck(4, "D")]
        phases_after = schedule._assign_phases(sched, decks_after)

        self.assertEqual(phases_after[1], 0)
        self.assertEqual(phases_after[2], 1)
        self.assertEqual(phases_after[3], 2)
        self.assertEqual(phases_after[4], 0)

    def test_removed_deck_does_not_force_reassignment(self) -> None:
        sched = {"type": "every_n_days", "n": 3, "m": 1, "stagger": {"mode": "stable"}}
        original = [deck(1, "A"), deck(2, "B"), deck(3, "C")]
        schedule._assign_phases(sched, original)

        remaining = [deck(1, "A"), deck(3, "C")]
        phases = schedule._assign_phases(sched, remaining)

        self.assertEqual(phases, {1: 0, 3: 2})

    def test_returning_deck_reuses_old_phase(self) -> None:
        sched = {"type": "every_n_days", "n": 3, "m": 1, "stagger": {"mode": "stable"}}
        original = [deck(1, "A"), deck(2, "B"), deck(3, "C")]
        schedule._assign_phases(sched, original)

        without_b = [deck(1, "A"), deck(3, "C")]
        schedule._assign_phases(sched, without_b)

        restored = [deck(1, "A"), deck(2, "B"), deck(3, "C")]
        phases = schedule._assign_phases(sched, restored)

        self.assertEqual(phases, {1: 0, 2: 1, 3: 2})

    def test_cycle_length_change_resets_old_assignments(self) -> None:
        sched = {"type": "every_n_days", "n": 3, "m": 1, "stagger": {"mode": "stable"}}
        decks = [deck(1, "A"), deck(2, "B"), deck(3, "C"), deck(4, "D")]
        schedule._assign_phases(sched, decks)

        sched["n"] = 2
        phases = schedule._assign_phases(sched, decks)

        self.assertEqual(phases, {1: 0, 2: 1, 3: 0, 4: 1})


if __name__ == "__main__":
    unittest.main()
