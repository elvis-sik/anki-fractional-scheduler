from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

import schedule  # noqa: E402


def deck(deck_id: int, name: str) -> schedule.DeckInfo:
    return schedule.DeckInfo(deck_id=deck_id, name=name)


class FakeBalanceCol:
    def __init__(
        self,
        decks: list[schedule.DeckInfo] | None = None,
        *,
        today: int = 10,
        card_rows: list[tuple[int, int, int]] | None = None,
        revlog_rows: list[tuple[int, int]] | None = None,
        deck_card_ids: dict[int, list[int]] | None = None,
        card_first_reviews: dict[int, int | None] | None = None,
    ) -> None:
        self.conf = {"rollover": 4}
        self.db = self
        self.sched = type("Sched", (), {"today": today})()
        self.decks = self
        self._decks = decks or [deck(1, "A"), deck(2, "B"), deck(3, "C"), deck(4, "D")]
        self._card_rows = card_rows or []
        self._revlog_rows = revlog_rows or []
        self._deck_card_ids = deck_card_ids or {}
        self._card_first_reviews = card_first_reviews or {}

    def all(self, sql: str, *_args):
        lowered = sql.lower()
        if "from cards" in lowered:
            return list(self._card_rows)
        if "from revlog" in lowered:
            return list(self._revlog_rows)
        return []

    def all_names_and_ids(self):
        return [{"name": item.name, "id": item.deck_id} for item in self._decks]

    def get(self, deck_id: int):
        return {
            "id": deck_id,
            "name": next(item.name for item in self._decks if item.deck_id == deck_id),
            "dyn": False,
        }

    def cids(self, deck_id: int, children: bool = False):
        return list(self._deck_card_ids.get(deck_id, []))

    def card_stats_data(self, card_id: int):
        first_review = self._card_first_reviews.get(card_id)

        class Stats:
            def __init__(self, first_review_value):
                self.first_review = first_review_value or 0
                self._has_first_review = first_review_value is not None

            def HasField(self, name: str) -> bool:
                return name == "first_review" and self._has_first_review

        return Stats(first_review)


class SqliteAllWrapper:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("create table cards (id integer primary key, did integer, odid integer)")
        self._conn.execute("create table revlog (id integer primary key, cid integer, ease integer default 1)")

    def all(self, sql: str, *args):
        return list(self._conn.execute(sql, args))


class FakeSqliteBalanceCol:
    def __init__(self, decks: list[schedule.DeckInfo], *, today: int) -> None:
        self.conf = {"rollover": 4}
        self.db = SqliteAllWrapper()
        self.sched = type("Sched", (), {"today": today})()
        self.decks = self
        self._decks = decks

    def all_names_and_ids(self):
        return [{"name": item.name, "id": item.deck_id} for item in self._decks]

    def get(self, deck_id: int):
        return {
            "id": deck_id,
            "name": next(item.name for item in self._decks if item.deck_id == deck_id),
            "dyn": False,
        }


def revlog_id_for_day_index(day_index: int, *, epoch: str = "2026-01-01", rollover: int = 4) -> int:
    year, month, day = [int(x) for x in epoch.split("-")]
    target_date = date(year, month, day) + timedelta(days=day_index)
    dt = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        rollover,
        1,
        0,
        tzinfo=datetime.now().astimezone().tzinfo,
    )
    return int(dt.timestamp() * 1000)


def anki_today_for_day_index(day_index: int, *, epoch: str = "2026-01-01", rollover: int = 4) -> int:
    return schedule.anki_day_number_from_date_str(epoch, rollover) + day_index


class AnkiTodayTests(unittest.TestCase):
    def test_anki_today_prefers_absolute_day_cutoff_over_relative_sched_today(self) -> None:
        rollover = 4
        expected_day_number = schedule.anki_day_number_from_date_str("2026-04-10", rollover)
        day_cutoff = datetime(2026, 4, 11, rollover, 0, 0, tzinfo=datetime.now().astimezone().tzinfo).timestamp()
        col = type(
            "Col",
            (),
            {
                "conf": {"rollover": rollover},
                "sched": type("Sched", (), {"today": 5, "day_cutoff": day_cutoff})(),
            },
        )()

        self.assertEqual(schedule.anki_today(col), expected_day_number)

    def test_anki_today_falls_back_to_sched_today_without_day_cutoff(self) -> None:
        col = type(
            "Col",
            (),
            {
                "conf": {"rollover": 4},
                "sched": type("Sched", (), {"today": 12})(),
            },
        )()

        self.assertEqual(schedule.anki_today(col), 12)


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

    def test_no_history_keeps_first_scheduled_release_pending(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=30,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices=set(),
        )

        self.assertEqual(effective_day_index, 0)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, decks[0], decks, effective_day_index),
            1,
        )

    def test_missed_scheduled_day_is_still_available_the_day_after(self) -> None:
        sched = {"type": "every_n_days", "m": 1, "n": 7}
        decks = [deck(1, "Deck")]

        effective_day_index = schedule._shifted_every_n_days_day_index(
            raw_day_index=8,
            schedule=sched,
            deck=decks[0],
            scheduled_decks=decks,
            introduced_day_indices=set(),
        )

        self.assertEqual(effective_day_index, 0)
        self.assertEqual(
            schedule._every_n_days_limit_for_day_index(sched, decks[0], decks, effective_day_index),
            1,
        )


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

    def test_rebalance_schedule_offsets_rebuilds_assignments_from_current_matches(self) -> None:
        class FakeCol:
            def __init__(self, decks: list[schedule.DeckInfo]) -> None:
                self._decks = decks
                self.decks = self

            def all_names_and_ids(self):
                return [{"name": deck.name, "id": deck.deck_id} for deck in self._decks]

            def get(self, deck_id: int):
                return {
                    "id": deck_id,
                    "name": next(deck.name for deck in self._decks if deck.deck_id == deck_id),
                    "dyn": False,
                }

        sched = {
            "type": "every_n_days",
            "n": 3,
            "m": 1,
            "targets": ["Parent::*"],
            "leaf_only": True,
            "stagger": {"mode": "stable"},
            "stagger_state": {
                "schedule_type": "every_n_days",
                "cycle_length": 3,
                "assignments": {"1": 0, "2": 1, "3": 2},
            },
        }
        col = FakeCol([deck(1, "Parent::A"), deck(3, "Parent::C"), deck(4, "Parent::D")])

        phases = schedule.rebalance_schedule_offsets(col, sched)

        self.assertEqual(phases, {1: 0, 3: 1, 4: 2})
        self.assertEqual(
            sched["stagger_state"]["assignments"],
            {"1": 0, "3": 1, "4": 2},
        )


class DailyBudgetPatternTests(unittest.TestCase):
    def test_distributed_counts_handles_group_budget(self) -> None:
        self.assertEqual(schedule.distributed_counts(20, 7), [3, 3, 3, 3, 3, 3, 2])

    def test_balance_first_preview_rotates_queue_by_budget(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A"), deck(2, "B"), deck(3, "C"), deck(4, "D")]

        preview = schedule._preview_balance_first_sequences_by_deck(
            FakeBalanceCol(),
            sched,
            decks,
            epoch="2026-01-01",
            raw_day_index=0,
            days=2,
            all_decks=decks,
        )

        todays_due = {deck_id for deck_id, seq in preview.items() if seq[0] > 0}
        tomorrows_due = {deck_id for deck_id, seq in preview.items() if seq[1] > 0}

        self.assertEqual(len(todays_due), 2)
        self.assertEqual(len(tomorrows_due), 2)
        self.assertTrue(todays_due.isdisjoint(tomorrows_due))

    def test_balance_first_replays_out_of_order_history(self) -> None:
        queue = [1, 2, 3, 4]

        queue = schedule._rotate_deck_to_back(queue, 3)
        queue = schedule._rotate_deck_to_back(queue, 1)

        self.assertEqual(queue, [2, 4, 3, 1])

    def test_hash_strategy_uses_deterministic_phase(self) -> None:
        sched = {
            "id": "hashy",
            "type": "every_n_days",
            "m": 1,
            "n": 7,
            "fractional_strategy": "hash",
        }
        decks = [deck(1, "Deck")]

        first = schedule._every_n_days_pattern_and_phase(sched, decks[0], decks)
        second = schedule._every_n_days_pattern_and_phase(sched, decks[0], decks)

        self.assertEqual(first, second)
        self.assertEqual(sum(first[0]), 1)

    def test_hash_strategy_ignores_introduction_history(self) -> None:
        sched = {
            "id": "hashy",
            "type": "every_n_days",
            "m": 1,
            "n": 7,
            "fractional_strategy": "hash",
        }
        decks = [deck(1, "A"), deck(2, "B")]
        today = anki_today_for_day_index(8)
        plain_col = FakeBalanceCol(decks, today=today)
        history_col = FakeBalanceCol(
            decks,
            today=today,
            card_rows=[
                (101, 1, 0),
                (202, 2, 0),
            ],
            revlog_rows=[
                (101, revlog_id_for_day_index(0)),
                (202, revlog_id_for_day_index(7)),
            ],
        )

        plain = schedule._preview_every_n_days_sequences_by_deck(
            plain_col,
            sched,
            decks,
            epoch="2026-01-01",
            raw_day_index=8,
            days=7,
            all_decks=decks,
        )
        with_history = schedule._preview_every_n_days_sequences_by_deck(
            history_col,
            sched,
            decks,
            epoch="2026-01-01",
            raw_day_index=8,
            days=7,
            all_decks=decks,
        )

        self.assertEqual(plain, with_history)

    def test_fraction_first_today_limit_stays_due_after_missed_day(self) -> None:
        sched = {
            "id": "fractiony",
            "type": "every_n_days",
            "m": 1,
            "n": 7,
            "fractional_strategy": "fraction_first",
            "stagger": {"mode": "stable"},
        }
        decks = [deck(1, "A")]
        col = FakeBalanceCol(decks, today=anki_today_for_day_index(8), card_rows=[], revlog_rows=[])

        limits = schedule._today_every_n_days_limits_for_schedule(
            col,
            sched,
            decks,
            decks,
            "2026-01-01",
            raw_day_index=8,
        )

        self.assertEqual(limits, {1: 1})

    def test_fraction_first_today_limit_advances_after_yesterdays_introduction(self) -> None:
        sched = {
            "id": "fractiony",
            "type": "every_n_days",
            "m": 1,
            "n": 7,
            "fractional_strategy": "fraction_first",
            "stagger": {"mode": "stable"},
        }
        decks = [deck(1, "A")]
        col = FakeBalanceCol(
            decks,
            today=anki_today_for_day_index(9),
            card_rows=[(101, 1, 0)],
            revlog_rows=[(101, revlog_id_for_day_index(8))],
        )

        limits = schedule._today_every_n_days_limits_for_schedule(
            col,
            sched,
            decks,
            decks,
            "2026-01-01",
            raw_day_index=9,
        )

        self.assertEqual(limits, {1: 0})

    def test_balance_first_queue_snapshot_shows_pending_due_decks_first(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A"), deck(2, "B"), deck(3, "C"), deck(4, "D")]
        stable_order = [item.deck_id for item in schedule._stable_deck_order(sched, decks)]
        day0_deck = stable_order[0]
        day1_deck = stable_order[1]

        col = FakeBalanceCol(
            decks,
            today=anki_today_for_day_index(1),
            card_rows=[
                (1001, day0_deck, 0),
                (1002, day1_deck, 0),
            ],
            revlog_rows=[
                (1001, revlog_id_for_day_index(0)),
                (1002, revlog_id_for_day_index(1)),
            ],
        )

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        queue_order = [entry.deck_id for entry in snapshot]
        self.assertEqual(queue_order[0], stable_order[2])
        self.assertEqual(snapshot[0].next_due_day_offset, 0)
        self.assertEqual(snapshot[-1].deck_id, day1_deck)
        self.assertEqual(snapshot[-1].last_introduction_day_offset, 0)
        self.assertEqual(snapshot[-1].next_due_day_offset, 2)

    def test_balance_first_queue_snapshot_without_history_uses_initial_order(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A"), deck(2, "B"), deck(3, "C"), deck(4, "D")]
        col = FakeBalanceCol(decks, today=anki_today_for_day_index(0), card_rows=[], revlog_rows=[])

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        self.assertEqual(
            [entry.deck_id for entry in snapshot],
            [item.deck_id for item in schedule._stable_deck_order(sched, decks)],
        )
        self.assertEqual([entry.next_due_day_offset for entry in snapshot[:2]], [0, 0])
        self.assertEqual([entry.next_due_day_offset for entry in snapshot[2:]], [1, 1])
        self.assertEqual([entry.last_introduction_day_offset for entry in snapshot], [None] * 4)

    def test_introduction_history_query_runs_against_real_sqlite(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A"), deck(2, "B")]
        col = FakeSqliteBalanceCol(decks, today=anki_today_for_day_index(1))
        col.db._conn.executemany(
            "insert into cards(id, did, odid) values (?, ?, ?)",
            [
                (101, 1, 0),
                (202, 2, 0),
            ],
        )
        col.db._conn.executemany(
            "insert into revlog(id, cid, ease) values (?, ?, ?)",
            [
                (revlog_id_for_day_index(0), 101, 3),
                (revlog_id_for_day_index(1), 202, 3),
            ],
        )

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        self.assertEqual([entry.last_introduction_day_offset for entry in snapshot], [1, 0])

    def test_balance_first_last_new_includes_pre_epoch_history(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A")]
        col = FakeSqliteBalanceCol(decks, today=anki_today_for_day_index(1))
        col.db._conn.execute(
            "insert into cards(id, did, odid) values (?, ?, ?)",
            (101, 1, 0),
        )
        col.db._conn.execute(
            "insert into revlog(id, cid, ease) values (?, ?, ?)",
            (revlog_id_for_day_index(-2), 101, 3),
        )

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        self.assertEqual([entry.last_introduction_day_offset for entry in snapshot], [3])

    def test_introduction_history_ignores_manual_reschedules(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A")]
        col = FakeSqliteBalanceCol(decks, today=anki_today_for_day_index(1))
        col.db._conn.execute(
            "insert into cards(id, did, odid) values (?, ?, ?)",
            (101, 1, 0),
        )
        col.db._conn.executemany(
            "insert into revlog(id, cid, ease) values (?, ?, ?)",
            [
                (revlog_id_for_day_index(0), 101, 0),
                (revlog_id_for_day_index(1), 101, 3),
            ],
        )

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        self.assertEqual([entry.last_introduction_day_offset for entry in snapshot], [0])

    def test_balance_first_falls_back_to_backend_card_stats_when_revlog_query_is_empty(self) -> None:
        sched = {
            "id": "group",
            "type": "every_n_days",
            "m": 1,
            "n": 2,
            "fractional_strategy": "balance_first",
        }
        decks = [deck(1, "A")]
        col = FakeBalanceCol(
            decks,
            today=anki_today_for_day_index(1),
            card_rows=[],
            revlog_rows=[],
            deck_card_ids={1: [101]},
            card_first_reviews={101: revlog_id_for_day_index(0) // 1000},
        )

        snapshot = schedule.balance_first_queue_snapshot(
            col,
            sched,
            [item.name for item in decks],
            "2026-01-01",
        )

        self.assertEqual([entry.last_introduction_day_offset for entry in snapshot], [1])


class FeatureAssignmentTests(unittest.TestCase):
    def test_notify_assignment_can_inherit_descendants_from_exact_parent_target(self) -> None:
        schedules = [
            {
                "id": "notify-parent",
                "type": "every_n_days",
                "m": 1,
                "n": 1,
                "targets": ["Parent"],
                "fractional_enabled": False,
                "notify_enabled": True,
                "notify_descendant_mode": "any_blocked_descendant",
            }
        ]
        decks = [deck(1, "Parent"), deck(2, "Parent::Child")]

        assignments, _schedule_to_decks = schedule.schedule_assignments_for_feature(
            decks,
            schedules,
            schedule.FEATURE_NOTIFY,
        )

        self.assertEqual(assignments[1]["id"], "notify-parent")
        self.assertEqual(assignments[2]["id"], "notify-parent")

    def test_more_specific_notify_schedule_wins_over_parent_inheritance(self) -> None:
        schedules = [
            {
                "id": "notify-parent",
                "type": "every_n_days",
                "m": 1,
                "n": 1,
                "targets": ["Parent"],
                "fractional_enabled": False,
                "notify_enabled": True,
                "notify_descendant_mode": "any_blocked_descendant",
            },
            {
                "id": "notify-child",
                "type": "every_n_days",
                "m": 1,
                "n": 1,
                "targets": ["Parent::Child"],
                "fractional_enabled": False,
                "notify_enabled": True,
                "notify_descendant_mode": "direct_only",
            },
        ]
        decks = [deck(1, "Parent"), deck(2, "Parent::Child")]

        assignments, _schedule_to_decks = schedule.schedule_assignments_for_feature(
            decks,
            schedules,
            schedule.FEATURE_NOTIFY,
        )

        self.assertEqual(assignments[1]["id"], "notify-parent")
        self.assertEqual(assignments[2]["id"], "notify-child")

    def test_fractional_assignment_ignores_notify_only_schedule(self) -> None:
        schedules = [
            {
                "id": "notify-only",
                "type": "every_n_days",
                "m": 1,
                "n": 1,
                "targets": ["Parent::*"],
                "fractional_enabled": False,
                "notify_enabled": True,
                "notify_descendant_mode": "any_blocked_descendant",
            },
            {
                "id": "fractional",
                "type": "every_n_days",
                "m": 1,
                "n": 3,
                "targets": ["Parent::*"],
                "fractional_enabled": True,
                "notify_enabled": False,
                "leaf_only": True,
            },
        ]
        decks = [deck(1, "Parent::Child")]

        assignments, _schedule_to_decks = schedule.schedule_assignments_for_feature(
            decks,
            schedules,
            schedule.FEATURE_FRACTIONAL,
        )

        self.assertEqual(assignments[1]["id"], "fractional")

    def test_general_wildcard_matching_supports_legacy_contains_patterns(self) -> None:
        matches = schedule.match_deck_names(["*Chemistry*"], ["Organic Chemistry", "Biology"])

        self.assertEqual(matches, ["Organic Chemistry"])


if __name__ == "__main__":
    unittest.main()
