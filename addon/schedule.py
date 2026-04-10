from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FEATURE_FRACTIONAL = "fractional_enabled"
FEATURE_NOTIFY = "notify_enabled"
DEFAULT_FRACTIONAL_STRATEGY = "fraction_first"
_LAST_BALANCE_FIRST_DEBUG: Dict[str, Any] = {}
DEBUG_LOG_ENV_VAR = "FRACTIONAL_SCHEDULER_DEBUG"
DEBUG_LOG_PATH = Path("/tmp/fractional-scheduler-debug.log")


@dataclass(frozen=True)
class DeckInfo:
    deck_id: int
    name: str
    is_dynamic: bool = False
    has_children: bool = False


@dataclass(frozen=True)
class DeckLimit:
    deck_id: int
    name: str
    limit: int


@dataclass(frozen=True)
class FractionalDeckHealth:
    deck_id: int
    deck_name: str
    schedule_id: Optional[str]
    cycle_length_days: int
    has_future_positive_limit: bool
    next_positive_day_offset: Optional[int]


@dataclass(frozen=True)
class IntroductionEvent:
    day_index: int
    timestamp_ms: int
    deck_id: int


@dataclass(frozen=True)
class BalanceFirstQueueEntry:
    position: int
    deck_id: int
    deck_name: str
    last_introduction_day_offset: Optional[int]
    next_due_day_offset: Optional[int]


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def _rollover_hours(col) -> int:
    try:
        return int(col.conf.get("rollover", 4))
    except Exception:
        return 4


def anki_day_number_from_timestamp(ts: float, rollover_hours: int) -> int:
    local_dt = datetime.fromtimestamp(ts, tz=_local_tzinfo())
    if (local_dt.hour, local_dt.minute, local_dt.second, local_dt.microsecond) < (
        rollover_hours,
        0,
        0,
        0,
    ):
        local_dt -= timedelta(days=1)
    return local_dt.date().toordinal()


def anki_today(col) -> int:
    if hasattr(col, "sched") and hasattr(col.sched, "day_cutoff"):
        try:
            # day_cutoff is the next rollover timestamp on an absolute Unix basis.
            return anki_day_number_from_timestamp(float(col.sched.day_cutoff) - 1, _rollover_hours(col))
        except Exception:
            pass
    if hasattr(col, "sched") and hasattr(col.sched, "today"):
        try:
            return int(col.sched.today)
        except Exception:
            pass
    return anki_day_number_from_timestamp(time.time(), _rollover_hours(col))


def anki_day_number_from_date_str(date_str: str, rollover_hours: int) -> int:
    del rollover_hours
    year, month, day = [int(x) for x in date_str.split("-")]
    return datetime(year, month, day).date().toordinal()


def _anki_day_start_timestamp(day_number: int, rollover_hours: int) -> float:
    local_dt = datetime.combine(
        date.fromordinal(day_number),
        datetime.min.time(),
        tzinfo=_local_tzinfo(),
    ).replace(hour=rollover_hours)
    return local_dt.timestamp()


def debug_logging_enabled() -> bool:
    value = str(os.environ.get(DEBUG_LOG_ENV_VAR, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _write_debug_log(event: str, payload: Dict[str, Any]) -> None:
    if not debug_logging_enabled():
        return
    try:
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "payload": payload,
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str))
            handle.write("\n")
    except Exception:
        pass


def bresenham_pattern(m: int, n: int) -> List[int]:
    return [int(value > 0) for value in distributed_counts(m, n)]


def distributed_counts(total: int, n: int) -> List[int]:
    if n <= 0:
        return []
    if total <= 0:
        return [0] * n
    counts: List[int] = []
    acc = n - 1
    for _ in range(n):
        acc += total
        count = acc // n
        counts.append(int(count))
        acc -= count * n
    return counts


def _fractional_strategy(schedule: Dict[str, Any]) -> str:
    strategy = str(schedule.get("fractional_strategy") or DEFAULT_FRACTIONAL_STRATEGY)
    if strategy in {"hash", "fraction_first", "balance_first"}:
        return strategy
    return DEFAULT_FRACTIONAL_STRATEGY


def _hash_int(value: str) -> int:
    digest = hashlib.sha1(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _hashed_phase(schedule: Dict[str, Any], deck: DeckInfo, modulo: int) -> int:
    if modulo <= 0:
        return 0
    seed = f"{schedule.get('id', '')}\0{deck.name}\0{deck.deck_id}"
    return _hash_int(seed) % modulo


def _stable_deck_order(schedule: Dict[str, Any], decks: List[DeckInfo]) -> List[DeckInfo]:
    return sorted(
        decks,
        key=lambda deck: (
            _hash_int(f"{schedule.get('id', '')}\0{deck.name}\0{deck.deck_id}"),
            deck.name.lower(),
            deck.deck_id,
        ),
    )


def match_deck_names(targets: Iterable[str], deck_names: Iterable[str]) -> List[str]:
    matches: List[str] = []
    for name in deck_names:
        for target in targets:
            if _target_matches(target, name) is not None:
                matches.append(name)
                break
    return matches


def match_deck_names_for_feature(
    schedule: Dict[str, Any],
    deck_names: Iterable[str],
    *,
    feature_key: str,
) -> List[str]:
    matches: List[str] = []
    for name in deck_names:
        for target in schedule.get("targets", []):
            if _target_match_for_feature(target, name, schedule, feature_key) is not None:
                matches.append(name)
                break
    return matches


def filter_deck_names_for_schedule(schedule: Dict[str, Any], deck_names: Iterable[str]) -> List[str]:
    all_names = list(deck_names)
    matches = match_deck_names(schedule.get("targets", []), all_names)
    if not schedule.get("leaf_only", True):
        return matches
    return [name for name in matches if not _deck_name_has_children(name, all_names)]


def preview_schedule(
    col,
    schedule: Dict[str, Any],
    deck_names: List[str],
    epoch: str,
    days: int = 14,
) -> Dict[str, List[int]]:
    if days <= 0:
        return {name: [] for name in deck_names}

    rollover_hours = _rollover_hours(col)
    anki_today_num = anki_today(col)
    epoch_day = anki_day_number_from_date_str(epoch, rollover_hours)
    day_index = anki_today_num - epoch_day

    weekday_idx = date.fromordinal(anki_today_num).weekday()
    preview_decks = _decks_for_names(col, deck_names)

    if schedule["type"] == "every_n_days":
        sequences = _preview_every_n_days_sequences_by_deck(
            col,
            schedule,
            preview_decks,
            epoch,
            day_index,
            days,
        )
        return {deck.name: list(sequences.get(deck.deck_id, [])) for deck in preview_decks}

    # day-of-week
    results: Dict[str, List[int]] = {}
    phases = _assign_phases(schedule, preview_decks)
    by_day = schedule.get("by_day") or {}
    for deck in preview_decks:
        phase = phases.get(deck.deck_id, 0)
        seq = []
        for i in range(days):
            idx = (weekday_idx + phase + i) % 7
            day_key = VALID_DAYS[idx]
            seq.append(int(by_day.get(day_key, 0)))
        results[deck.name] = seq
    return results


def rebalance_schedule_offsets(col, schedule: Dict[str, Any]) -> Dict[int, int]:
    if col is None:
        return {}

    decks = _matched_decks_for_schedule(col, schedule)
    if not decks:
        schedule.pop("stagger_state", None)
        return {}

    schedule.pop("stagger_state", None)
    return _assign_phases(schedule, decks)


def _target_matches(target: str, deck_name: str) -> Optional[Tuple[int, int]]:
    if deck_name == target:
        return (3, len(target))
    if target.endswith("*") and "*" not in target[:-1] and "?" not in target:
        prefix = target[:-1]
        if deck_name.startswith(prefix):
            return (2, len(prefix))
        return None
    if "*" in target or "?" in target:
        if fnmatch.fnmatchcase(deck_name.lower(), target.lower()):
            literal_chars = len(target.replace("*", "").replace("?", ""))
            return (1, literal_chars)
    return None


def _best_schedule_for_deck(
    deck_name: str,
    schedules: List[Dict[str, Any]],
    *,
    feature_key: str = FEATURE_FRACTIONAL,
) -> Optional[Dict[str, Any]]:
    best = None
    best_score: Tuple[int, int, int] = (-1, -1, -1)

    for idx, sched in enumerate(schedules):
        if not _schedule_feature_enabled(sched, feature_key):
            continue
        targets = sched.get("targets") or []
        best_target_score: Optional[Tuple[int, int]] = None

        for target in targets:
            match = _target_match_for_feature(target, deck_name, sched, feature_key)
            if match is None:
                continue
            if best_target_score is None or match > best_target_score:
                best_target_score = match

        if best_target_score is None:
            continue

        spec, plen = best_target_score
        # Earlier schedules win on final tie-breaker
        score = (spec, plen, -idx)
        if score > best_score:
            best = sched
            best_score = score

    return best


def _target_match_for_feature(
    target: str,
    deck_name: str,
    schedule: Dict[str, Any],
    feature_key: str,
) -> Optional[Tuple[int, int]]:
    if feature_key != FEATURE_NOTIFY:
        return _target_matches(target, deck_name)

    direct_match = _target_matches(target, deck_name)
    if direct_match is not None:
        return direct_match

    if schedule.get("notify_descendant_mode") == "direct_only":
        return None
    if "*" in target or "?" in target:
        return None
    if deck_name.startswith(f"{target}::"):
        return (2, len(target))
    return None


def _schedule_feature_enabled(schedule: Dict[str, Any], feature_key: str) -> bool:
    if feature_key == FEATURE_NOTIFY:
        return bool(schedule.get("notify_enabled", False))
    return bool(schedule.get("fractional_enabled", True))


def _schedule_cycle_length(schedule: Dict[str, Any]) -> int:
    if schedule["type"] == "every_n_days":
        return int(schedule["n"])
    return 7


def _assign_phases(schedule: Dict[str, Any], decks: List[DeckInfo]) -> Dict[int, int]:
    modulo = _schedule_cycle_length(schedule)
    if modulo <= 0:
        return {deck.deck_id: 0 for deck in decks}

    stagger = schedule.get("stagger")
    if stagger is None:
        return {deck.deck_id: 0 for deck in decks}

    state = _stable_stagger_state(schedule, modulo)
    assignments = dict(state.get("assignments", {}))
    active_ids = {deck.deck_id for deck in decks}
    phase_counts = [0] * modulo

    for deck in decks:
        phase = assignments.get(str(deck.deck_id))
        if isinstance(phase, int) and 0 <= phase < modulo:
            phase_counts[phase] += 1

    missing = [
        deck
        for deck in sorted(decks, key=lambda deck: (deck.name.lower(), deck.deck_id))
        if str(deck.deck_id) not in assignments
    ]
    for deck in missing:
        phase = min(range(modulo), key=lambda idx: (phase_counts[idx], idx))
        assignments[str(deck.deck_id)] = phase
        phase_counts[phase] += 1

    state["assignments"] = assignments
    schedule["stagger_state"] = state

    return {deck.deck_id: assignments.get(str(deck.deck_id), 0) for deck in decks if deck.deck_id in active_ids}


def _stable_stagger_state(schedule: Dict[str, Any], modulo: int) -> Dict[str, Any]:
    raw_state = schedule.get("stagger_state")
    assignments: Dict[str, int] = {}
    if isinstance(raw_state, dict):
        raw_assignments = raw_state.get("assignments")
        if isinstance(raw_assignments, dict):
            for raw_deck_id, raw_phase in raw_assignments.items():
                try:
                    deck_id = str(int(raw_deck_id))
                    phase = int(raw_phase)
                except Exception:
                    continue
                if 0 <= phase < modulo:
                    assignments[deck_id] = phase

        if raw_state.get("schedule_type") != schedule.get("type") or int(raw_state.get("cycle_length") or 0) != modulo:
            assignments = {}

    return {
        "schedule_type": str(schedule.get("type", "every_n_days")),
        "cycle_length": modulo,
        "assignments": assignments,
    }


def compute_deck_limits(col, config) -> List[DeckLimit]:
    decks = _collect_decks(col)
    assignments, schedule_to_decks = _schedule_assignments(
        decks,
        config.schedules,
        feature_key=FEATURE_FRACTIONAL,
    )
    calendar_state = _calendar_state(col, config.epoch)
    every_n_limits: Dict[int, int] = {}

    for scheduled_decks in schedule_to_decks.values():
        if not scheduled_decks:
            continue
        schedule = assignments[scheduled_decks[0].deck_id]
        if schedule["type"] != "every_n_days":
            continue
        every_n_limits.update(
            _today_every_n_days_limits_for_schedule(
                col,
                schedule,
                decks,
                scheduled_decks,
                config.epoch,
                calendar_state["day_index"],
            )
        )

    results: List[DeckLimit] = []

    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue

        scheduled_decks = schedule_to_decks.get(str(sched["id"]), [])
        if sched["type"] == "every_n_days":
            limit = int(every_n_limits.get(deck.deck_id, 0))
        else:
            limit = _limit_for_offset(
                sched,
                deck,
                scheduled_decks,
                calendar_state["day_index"],
                calendar_state["weekday_idx"],
                0,
            )

        results.append(DeckLimit(deck_id=deck.deck_id, name=deck.name, limit=limit))

    return results


def compute_schedule_health_snapshot(col, config) -> Dict[int, FractionalDeckHealth]:
    decks = _collect_decks(col)
    assignments, schedule_to_decks = _schedule_assignments(
        decks,
        config.schedules,
        feature_key=FEATURE_FRACTIONAL,
    )
    calendar_state = _calendar_state(col, config.epoch)
    every_n_previews: Dict[str, Dict[int, List[int]]] = {}

    snapshot: Dict[int, FractionalDeckHealth] = {}

    for schedule_id, scheduled_decks in schedule_to_decks.items():
        if not scheduled_decks:
            continue
        schedule = assignments[scheduled_decks[0].deck_id]
        if schedule["type"] != "every_n_days":
            continue
        every_n_previews[schedule_id] = _preview_every_n_days_sequences_by_deck(
            col,
            schedule,
            scheduled_decks,
            config.epoch,
            calendar_state["day_index"],
            _schedule_cycle_length(schedule),
            all_decks=decks,
        )

    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue

        cycle_length = _schedule_cycle_length(sched)
        scheduled_decks = schedule_to_decks.get(str(sched["id"]), [])
        next_positive_offset: Optional[int] = None

        if sched["type"] == "every_n_days":
            sequence = every_n_previews.get(str(sched["id"]), {}).get(deck.deck_id, [])
            for offset, limit in enumerate(sequence):
                if limit > 0:
                    next_positive_offset = offset
                    break
        else:
            for offset in range(cycle_length):
                limit = _limit_for_offset(
                    sched,
                    deck,
                    scheduled_decks,
                    calendar_state["day_index"],
                    calendar_state["weekday_idx"],
                    offset,
                )
                if limit > 0:
                    next_positive_offset = offset
                    break

        snapshot[deck.deck_id] = FractionalDeckHealth(
            deck_id=deck.deck_id,
            deck_name=deck.name,
            schedule_id=str(sched["id"]),
            cycle_length_days=cycle_length,
            has_future_positive_limit=next_positive_offset is not None,
            next_positive_day_offset=next_positive_offset,
        )

    return snapshot


def _schedule_assignments(
    decks: List[DeckInfo],
    schedules: List[Dict[str, Any]],
    *,
    feature_key: str = FEATURE_FRACTIONAL,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, List[DeckInfo]]]:
    assignments: Dict[int, Dict[str, Any]] = {}
    enabled_schedules = [sched for sched in schedules if _schedule_feature_enabled(sched, feature_key)]

    for deck in decks:
        if deck.is_dynamic:
            continue
        sched = _best_schedule_for_deck(
            deck.name,
            enabled_schedules,
            feature_key=feature_key,
        )
        if sched is None:
            continue
        if feature_key == FEATURE_FRACTIONAL and sched.get("leaf_only", True) and deck.has_children:
            continue
        assignments[deck.deck_id] = sched

    schedule_to_decks: Dict[str, List[DeckInfo]] = {}
    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue
        schedule_to_decks.setdefault(str(sched["id"]), []).append(deck)

    return assignments, schedule_to_decks


def schedule_assignments_for_feature(
    decks: List[DeckInfo],
    schedules: List[Dict[str, Any]],
    feature_key: str,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, List[DeckInfo]]]:
    return _schedule_assignments(decks, schedules, feature_key=feature_key)


def collect_decks(col) -> List[DeckInfo]:
    return _collect_decks(col)


def _set_last_balance_first_debug(debug: Dict[str, Any], *, event: Optional[str] = None) -> None:
    global _LAST_BALANCE_FIRST_DEBUG
    _LAST_BALANCE_FIRST_DEBUG = dict(debug)
    if event is not None:
        _write_debug_log(event, _LAST_BALANCE_FIRST_DEBUG)


def balance_first_queue_snapshot(
    col,
    schedule: Dict[str, Any],
    deck_names: List[str],
    epoch: str,
) -> List[BalanceFirstQueueEntry]:
    _write_debug_log(
        "queue_snapshot_start",
        {
            "schedule_id": schedule.get("id"),
            "epoch": epoch,
            "deck_count": len(deck_names),
            "deck_names_sample": deck_names[:10],
        },
    )
    if schedule.get("type") != "every_n_days" or _fractional_strategy(schedule) != "balance_first":
        return []

    scheduled_decks = _decks_for_names(col, deck_names)
    if not scheduled_decks:
        return []

    queue_state = _balance_first_queue_state(
        col,
        schedule,
        scheduled_decks,
        epoch,
        all_decks=_collect_decks(col),
    )
    if queue_state is None:
        return []

    by_id = {deck.deck_id: deck for deck in scheduled_decks}
    return [
        BalanceFirstQueueEntry(
            position=idx + 1,
            deck_id=deck_id,
            deck_name=by_id[deck_id].name,
            last_introduction_day_offset=queue_state["last_offsets"].get(deck_id),
            next_due_day_offset=queue_state["next_offsets"].get(deck_id),
        )
        for idx, deck_id in enumerate(queue_state["queue_now"])
        if deck_id in by_id
    ]


def _calendar_state(col, epoch: str) -> Dict[str, int]:
    anki_today_num = anki_today(col)
    rollover_hours = _rollover_hours(col)
    epoch_day = anki_day_number_from_date_str(epoch, rollover_hours)
    day_index = anki_today_num - epoch_day
    weekday_idx = date.fromordinal(anki_today_num).weekday()
    return {
        "day_index": day_index,
        "weekday_idx": weekday_idx,
    }


def _today_every_n_days_limits_for_schedule(
    col,
    schedule: Dict[str, Any],
    all_decks: List[DeckInfo],
    scheduled_decks: List[DeckInfo],
    epoch: str,
    raw_day_index: int,
) -> Dict[int, int]:
    sequences = _preview_every_n_days_sequences_by_deck(
        col,
        schedule,
        scheduled_decks,
        epoch,
        raw_day_index,
        1,
        all_decks=all_decks,
    )
    return {deck_id: int(sequence[0]) if sequence else 0 for deck_id, sequence in sequences.items()}


def _preview_every_n_days_sequences_by_deck(
    col,
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    epoch: str,
    raw_day_index: int,
    days: int,
    *,
    all_decks: Optional[List[DeckInfo]] = None,
) -> Dict[int, List[int]]:
    if days <= 0 or not scheduled_decks:
        return {deck.deck_id: [] for deck in scheduled_decks}

    strategy = _fractional_strategy(schedule)
    if strategy == "balance_first":
        return _preview_balance_first_sequences_by_deck(
            col,
            schedule,
            scheduled_decks,
            epoch,
            raw_day_index,
            days,
            all_decks=all_decks,
        )

    if strategy == "hash":
        return {
            deck.deck_id: [
                _every_n_days_limit_for_day_index(
                    schedule,
                    deck,
                    scheduled_decks,
                    raw_day_index + offset,
                )
                for offset in range(days)
            ]
            for deck in scheduled_decks
        }

    effective_day_indices = _effective_every_n_days_day_indices_for_schedule(
        col,
        epoch,
        all_decks or _collect_decks(col),
        schedule,
        scheduled_decks,
        raw_day_index,
    )
    results: Dict[int, List[int]] = {}
    for deck in scheduled_decks:
        effective_day_index = effective_day_indices.get(deck.deck_id, raw_day_index)
        pattern, phase = _every_n_days_pattern_and_phase(schedule, deck, scheduled_decks)
        results[deck.deck_id] = [
            _every_n_days_limit_from_pattern(pattern, phase, effective_day_index + offset) for offset in range(days)
        ]
    return results


def _effective_every_n_days_day_indices_for_schedule(
    col,
    epoch: str,
    all_decks: List[DeckInfo],
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    raw_day_index: int,
) -> Dict[int, int]:
    if raw_day_index <= 0 or not scheduled_decks:
        return {}

    introduction_events = _new_introduction_events_by_assigned_deck(
        col,
        epoch,
        all_decks,
        scheduled_decks,
        raw_day_index,
        start_day_index=0,
    )
    introduced_days = {
        deck_id: {event.day_index for event in events} for deck_id, events in introduction_events.items()
    }

    effective: Dict[int, int] = {}
    for deck in scheduled_decks:
        effective[deck.deck_id] = _shifted_every_n_days_day_index(
            raw_day_index,
            schedule,
            deck,
            scheduled_decks,
            introduced_days.get(deck.deck_id, set()),
        )
    return effective


def _new_introduction_events_by_assigned_deck(
    col,
    epoch: str,
    all_decks: List[DeckInfo],
    scheduled_decks: List[DeckInfo],
    raw_day_index: int,
    *,
    start_day_index: Optional[int] = 0,
    allow_pre_epoch_events: bool = False,
) -> Dict[int, List[IntroductionEvent]]:
    debug: Dict[str, Any] = {
        "history_path": "sql",
        "candidate_cards": 0,
        "first_reviews": 0,
        "backend_cards_checked": 0,
        "relevant_source_ids": 0,
        "matched_decks": len(scheduled_decks),
        "raw_day_index": raw_day_index,
    }
    if raw_day_index <= 0 or not scheduled_decks:
        _set_last_balance_first_debug(debug, event="history_skipped")
        return {}

    relevant_source_ids: Set[int] = set()
    source_to_targets: Dict[int, List[int]] = {}

    for deck in scheduled_decks:
        prefix = f"{deck.name}::"
        for candidate in all_decks:
            if candidate.name == deck.name or candidate.name.startswith(prefix):
                relevant_source_ids.add(candidate.deck_id)
                source_to_targets.setdefault(candidate.deck_id, []).append(deck.deck_id)

    if not relevant_source_ids:
        _set_last_balance_first_debug(debug, event="history_no_sources")
        return {}

    rollover_hours = _rollover_hours(col)
    epoch_day = anki_day_number_from_date_str(epoch, rollover_hours)
    start_ms: Optional[int] = None
    if start_day_index is not None:
        start_day = epoch_day + int(start_day_index)
        start_ms = int(_anki_day_start_timestamp(start_day, rollover_hours) * 1000)
    ordered_source_ids = sorted(relevant_source_ids)
    placeholders = ",".join("?" for _ in ordered_source_ids)
    debug["relevant_source_ids"] = len(ordered_source_ids)
    debug["source_id_sample"] = ordered_source_ids[:12]
    debug["source_to_targets_sample"] = {
        str(deck_id): source_to_targets.get(deck_id, [])[:6] for deck_id in ordered_source_ids[:8]
    }
    cards_sql = f"""
select id, did, odid
from cards
where did in ({placeholders}) or odid in ({placeholders})
"""

    try:
        card_rows = col.db.all(cards_sql, *ordered_source_ids, *ordered_source_ids)
    except Exception as exc:
        print(f"fractional scheduler: failed to query candidate cards: {exc}")
        debug["history_path"] = "sql_error"
        debug["error"] = str(exc)
        _set_last_balance_first_debug(debug, event="history_card_query_error")
        return {}

    card_sources: Dict[int, int] = {}
    for card_id, current_did, original_did in card_rows:
        try:
            card_id_int = int(card_id)
            source_deck_id = int(original_did) if int(original_did) != 0 else int(current_did)
        except Exception:
            continue
        if source_deck_id not in source_to_targets:
            continue
        card_sources[card_id_int] = source_deck_id

    debug["candidate_cards"] = len(card_sources)
    debug["candidate_card_sample"] = sorted(card_sources.items())[:12]
    _write_debug_log("history_candidate_cards", debug)
    if not card_sources:
        introduced = _introduction_events_via_backend_card_stats(
            col,
            relevant_source_ids,
            source_to_targets,
            epoch_day,
            rollover_hours,
            raw_day_index,
            start_ms=start_ms,
            allow_pre_epoch_events=allow_pre_epoch_events,
            debug=debug,
        )
        for target_deck_id in list(introduced.keys()):
            introduced[target_deck_id] = sorted(
                introduced[target_deck_id],
                key=lambda event: (event.day_index, event.timestamp_ms, event.deck_id),
            )
        _set_last_balance_first_debug(debug, event="history_backend_only")
        return introduced

    first_review_by_card: Dict[int, int] = {}
    card_ids = sorted(card_sources)
    chunk_size = 400
    for start in range(0, len(card_ids), chunk_size):
        chunk = card_ids[start : start + chunk_size]
        chunk_placeholders = ",".join("?" for _ in chunk)
        revlog_sql = f"""
select cid, min(id)
from revlog
where ease != 0 and cid in ({chunk_placeholders})
group by cid
"""
        try:
            revlog_rows = col.db.all(revlog_sql, *chunk)
        except Exception as exc:
            print(f"fractional scheduler: failed to query introduction revlog chunk: {exc}")
            debug["history_path"] = "revlog_error"
            debug["error"] = str(exc)
            debug["first_reviews"] = len(first_review_by_card)
            _set_last_balance_first_debug(debug, event="history_revlog_query_error")
            return {}
        for card_id, revlog_id in revlog_rows:
            try:
                first_review_by_card[int(card_id)] = int(revlog_id)
            except Exception:
                continue

    debug["first_reviews"] = len(first_review_by_card)
    debug["first_review_sample"] = sorted(first_review_by_card.items())[:12]
    introduced = _introduction_events_from_first_reviews(
        first_review_by_card,
        card_sources,
        source_to_targets,
        epoch_day,
        rollover_hours,
        raw_day_index,
        start_ms=start_ms,
        allow_pre_epoch_events=allow_pre_epoch_events,
    )

    if not introduced:
        debug["history_path"] = "backend"
        introduced = _introduction_events_via_backend_card_stats(
            col,
            relevant_source_ids,
            source_to_targets,
            epoch_day,
            rollover_hours,
            raw_day_index,
            start_ms=start_ms,
            allow_pre_epoch_events=allow_pre_epoch_events,
            debug=debug,
        )

    debug["introduced_decks"] = sorted(int(deck_id) for deck_id in introduced.keys())[:20]
    debug["introduced_event_count"] = sum(len(events) for events in introduced.values())
    _set_last_balance_first_debug(debug, event="history_complete")
    for target_deck_id in list(introduced.keys()):
        introduced[target_deck_id] = sorted(
            introduced[target_deck_id],
            key=lambda event: (event.day_index, event.timestamp_ms, event.deck_id),
        )

    return introduced


def _introduction_events_from_first_reviews(
    first_review_by_card: Dict[int, int],
    card_sources: Dict[int, int],
    source_to_targets: Dict[int, List[int]],
    epoch_day: int,
    rollover_hours: int,
    raw_day_index: int,
    *,
    start_ms: Optional[int],
    allow_pre_epoch_events: bool,
) -> Dict[int, List[IntroductionEvent]]:
    introduced: Dict[int, List[IntroductionEvent]] = {}
    for card_id_int, source_deck_id in card_sources.items():
        revlog_id = first_review_by_card.get(card_id_int)
        if revlog_id is None:
            continue
        timestamp_ms = revlog_id
        day_num = anki_day_number_from_timestamp(float(revlog_id) / 1000.0, rollover_hours)
        day_index = day_num - epoch_day
        if start_ms is not None and timestamp_ms < start_ms:
            continue
        if day_index >= raw_day_index:
            continue
        if day_index < 0 and not allow_pre_epoch_events:
            continue
        for target_deck_id in source_to_targets.get(source_deck_id, []):
            introduced.setdefault(target_deck_id, []).append(
                IntroductionEvent(
                    day_index=day_index,
                    timestamp_ms=timestamp_ms,
                    deck_id=target_deck_id,
                )
            )
    return introduced


def _introduction_events_via_backend_card_stats(
    col,
    source_deck_ids: Set[int],
    source_to_targets: Dict[int, List[int]],
    epoch_day: int,
    rollover_hours: int,
    raw_day_index: int,
    *,
    start_ms: Optional[int],
    allow_pre_epoch_events: bool,
    debug: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[IntroductionEvent]]:
    decks = getattr(col, "decks", None)
    card_stats_data = getattr(col, "card_stats_data", None)
    if decks is None or not callable(card_stats_data):
        return {}
    cids_for_deck = getattr(decks, "cids", None)
    if not callable(cids_for_deck):
        return {}

    introduced: Dict[int, List[IntroductionEvent]] = {}
    seen_cards: Set[int] = set()
    for source_deck_id in sorted(source_deck_ids):
        try:
            card_ids = list(cids_for_deck(source_deck_id, children=False))
        except Exception:
            continue
        for card_id in card_ids:
            try:
                card_id_int = int(card_id)
            except Exception:
                continue
            if card_id_int in seen_cards:
                continue
            seen_cards.add(card_id_int)
            if debug is not None:
                debug["backend_cards_checked"] = int(debug.get("backend_cards_checked", 0)) + 1
            try:
                stats = card_stats_data(card_id_int)
            except Exception:
                continue
            try:
                has_first_review = stats.HasField("first_review")
            except Exception:
                has_first_review = False
            if not has_first_review:
                continue
            try:
                timestamp_ms = int(stats.first_review) * 1000
            except Exception:
                continue
            if start_ms is not None and timestamp_ms < start_ms:
                continue
            day_num = anki_day_number_from_timestamp(float(timestamp_ms) / 1000.0, rollover_hours)
            day_index = day_num - epoch_day
            if day_index >= raw_day_index:
                continue
            if day_index < 0 and not allow_pre_epoch_events:
                continue
            for target_deck_id in source_to_targets.get(source_deck_id, []):
                introduced.setdefault(target_deck_id, []).append(
                    IntroductionEvent(
                        day_index=day_index,
                        timestamp_ms=timestamp_ms,
                        deck_id=target_deck_id,
                    )
                )
    if debug is not None:
        debug["backend_introduced_event_count"] = sum(len(events) for events in introduced.values())
    return introduced


def _shifted_every_n_days_day_index(
    raw_day_index: int,
    schedule: Dict[str, Any],
    deck: DeckInfo,
    scheduled_decks: List[DeckInfo],
    introduced_day_indices: Set[int],
) -> int:
    if raw_day_index <= 0:
        return raw_day_index

    pattern, phase = _every_n_days_pattern_and_phase(schedule, deck, scheduled_decks)
    effective_day = 0

    for calendar_day in range(raw_day_index):
        scheduled_limit = _every_n_days_limit_from_pattern(pattern, phase, effective_day)
        if scheduled_limit > 0 and calendar_day not in introduced_day_indices:
            # Hold the current release in place until the user actually introduces it.
            continue
        effective_day += 1

    return effective_day


def _preview_balance_first_sequences_by_deck(
    col,
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    epoch: str,
    raw_day_index: int,
    days: int,
    *,
    all_decks: Optional[List[DeckInfo]] = None,
) -> Dict[int, List[int]]:
    if days <= 0 or not scheduled_decks:
        return {deck.deck_id: [] for deck in scheduled_decks}

    queue_state = _balance_first_queue_state_for_day_index(
        col,
        schedule,
        scheduled_decks,
        epoch,
        raw_day_index,
        all_decks=all_decks,
        include_current_day_events=False,
    )
    budget_pattern = _schedule_daily_budget_pattern(schedule, len(scheduled_decks))
    if queue_state is None or not budget_pattern:
        return {deck.deck_id: [0] * days for deck in scheduled_decks}

    results = {deck.deck_id: [0] * days for deck in scheduled_decks}
    queue = list(queue_state["queue_now"])
    for offset in range(days):
        budget = budget_pattern[(raw_day_index + offset) % len(budget_pattern)]
        todays_queue = queue[: max(0, budget)]
        for deck_id in todays_queue:
            results.setdefault(deck_id, [0] * days)[offset] = 1
        queue = _rotate_front_slice_to_back(queue, len(todays_queue))

    return results


def _balance_first_queue_state(
    col,
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    epoch: str,
    *,
    all_decks: Optional[List[DeckInfo]] = None,
) -> Optional[Dict[str, Any]]:
    calendar_state = _calendar_state(col, epoch)
    return _balance_first_queue_state_for_day_index(
        col,
        schedule,
        scheduled_decks,
        epoch,
        calendar_state["day_index"],
        all_decks=all_decks,
        include_current_day_events=True,
    )


def _balance_first_queue_state_for_day_index(
    col,
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    epoch: str,
    raw_day_index: int,
    *,
    all_decks: Optional[List[DeckInfo]] = None,
    include_current_day_events: bool,
) -> Optional[Dict[str, Any]]:
    if not scheduled_decks:
        return None

    queue_before_today = [deck.deck_id for deck in _stable_deck_order(schedule, scheduled_decks)]
    history_limit = raw_day_index + 1 if include_current_day_events else raw_day_index
    events_by_deck = _new_introduction_events_by_assigned_deck(
        col,
        epoch,
        all_decks or _collect_decks(col),
        scheduled_decks,
        history_limit,
        start_day_index=None,
        allow_pre_epoch_events=True,
    )
    all_events = sorted(
        [event for events in events_by_deck.values() for event in events],
        key=lambda event: (event.day_index, event.timestamp_ms, event.deck_id),
    )

    past_events = [event for event in all_events if event.day_index < raw_day_index]
    for event in past_events:
        queue_before_today = _rotate_deck_to_back(queue_before_today, event.deck_id)

    budget_pattern = _schedule_daily_budget_pattern(schedule, len(scheduled_decks))
    if not budget_pattern:
        return {
            "queue_now": list(queue_before_today),
            "last_offsets": {},
            "next_offsets": {deck.deck_id: None for deck in scheduled_decks},
            "debug": {
                "history_path": "none",
                "deck_count": len(scheduled_decks),
                "decks_with_last_new": 0,
            },
        }

    today_budget = budget_pattern[raw_day_index % len(budget_pattern)]
    due_today = queue_before_today[: max(0, today_budget)]
    today_events = [event for event in all_events if event.day_index == raw_day_index]
    studied_due_today = {event.deck_id for event in today_events if event.deck_id in due_today}

    queue_now = list(queue_before_today)
    for event in today_events:
        queue_now = _rotate_deck_to_back(queue_now, event.deck_id)

    last_offsets: Dict[int, Optional[int]] = {}
    for deck in scheduled_decks:
        deck_events = events_by_deck.get(deck.deck_id, [])
        if not deck_events:
            last_offsets[deck.deck_id] = None
            continue
        last_offsets[deck.deck_id] = max(0, raw_day_index - deck_events[-1].day_index)

    next_offsets: Dict[int, Optional[int]] = {deck.deck_id: None for deck in scheduled_decks}
    pending_due_today = [deck_id for deck_id in due_today if deck_id not in studied_due_today]
    for deck_id in due_today:
        if deck_id not in studied_due_today:
            next_offsets[deck_id] = 0

    future_queue = _rotate_front_slice_to_back(queue_now, len(pending_due_today))
    cycle_length = _schedule_cycle_length(schedule)
    for offset in range(1, cycle_length + 1):
        budget = budget_pattern[(raw_day_index + offset) % len(budget_pattern)]
        todays_queue = future_queue[: max(0, budget)]
        for deck_id in todays_queue:
            if next_offsets.get(deck_id) is None:
                next_offsets[deck_id] = offset
        future_queue = _rotate_front_slice_to_back(future_queue, len(todays_queue))

    debug = dict(_LAST_BALANCE_FIRST_DEBUG)
    debug["deck_count"] = len(scheduled_decks)
    debug["decks_with_last_new"] = sum(1 for value in last_offsets.values() if value is not None)
    debug["last_offset_sample"] = {str(deck.deck_id): last_offsets.get(deck.deck_id) for deck in scheduled_decks[:12]}
    _set_last_balance_first_debug(debug, event="queue_state")

    return {
        "queue_now": queue_now,
        "last_offsets": last_offsets,
        "next_offsets": next_offsets,
        "debug": debug,
    }


def _schedule_daily_budget_pattern(schedule: Dict[str, Any], deck_count: int) -> List[int]:
    if deck_count <= 0:
        return []
    return distributed_counts(deck_count * int(schedule.get("m", 0)), int(schedule.get("n", 1)))


def _rotate_deck_to_back(queue: List[int], deck_id: int) -> List[int]:
    if deck_id not in queue:
        return list(queue)
    updated = list(queue)
    updated.remove(deck_id)
    updated.append(deck_id)
    return updated


def _rotate_front_slice_to_back(queue: List[int], count: int) -> List[int]:
    if count <= 0:
        return list(queue)
    actual = min(count, len(queue))
    return list(queue[actual:]) + list(queue[:actual])


def _every_n_days_pattern_and_phase(
    schedule: Dict[str, Any],
    deck: DeckInfo,
    scheduled_decks: List[DeckInfo],
) -> Tuple[List[int], int]:
    n = int(schedule["n"])
    if n <= 0:
        return ([], 0)
    if _fractional_strategy(schedule) == "hash":
        phase = _hashed_phase(schedule, deck, n)
    else:
        phase = _assign_phases(schedule, scheduled_decks).get(deck.deck_id, 0)
    pattern = bresenham_pattern(int(schedule["m"]), n)
    return (pattern, phase)


def _every_n_days_limit_from_pattern(pattern: List[int], phase: int, day_index: int) -> int:
    if not pattern:
        return 0
    return int(pattern[(day_index + phase) % len(pattern)])


def _every_n_days_limit_for_day_index(
    schedule: Dict[str, Any],
    deck: DeckInfo,
    scheduled_decks: List[DeckInfo],
    day_index: int,
) -> int:
    pattern, phase = _every_n_days_pattern_and_phase(schedule, deck, scheduled_decks)
    return _every_n_days_limit_from_pattern(pattern, phase, day_index)


def _limit_for_offset(
    schedule: Dict[str, Any],
    deck: DeckInfo,
    scheduled_decks: List[DeckInfo],
    day_index: int,
    weekday_idx: int,
    offset: int,
) -> int:
    phases = _assign_phases(schedule, scheduled_decks)
    phase = phases.get(deck.deck_id, 0)

    if schedule["type"] == "every_n_days":
        return _every_n_days_limit_for_day_index(
            schedule,
            deck,
            scheduled_decks,
            day_index + offset,
        )

    idx = (weekday_idx + phase + offset) % 7
    day_key = VALID_DAYS[idx]
    return int((schedule.get("by_day") or {}).get(day_key, 0))


def _decks_for_names(col, deck_names: List[str]) -> List[DeckInfo]:
    if not deck_names:
        return []
    wanted = set(deck_names)
    return [deck for deck in _collect_decks(col) if deck.name in wanted]


def _matched_decks_for_schedule(col, schedule: Dict[str, Any]) -> List[DeckInfo]:
    all_decks = _collect_decks(col)
    matched_names = set(filter_deck_names_for_schedule(schedule, [deck.name for deck in all_decks]))
    return [deck for deck in all_decks if deck.name in matched_names]


def _deck_name_and_id(entry: Any) -> Tuple[Optional[str], Optional[int]]:
    if isinstance(entry, dict):
        name = entry.get("name")
        deck_id = entry.get("id")
    elif hasattr(entry, "name") and hasattr(entry, "id"):
        name = getattr(entry, "name", None)
        deck_id = getattr(entry, "id", None)
    else:
        try:
            name, deck_id = entry
        except Exception:
            name = None
            deck_id = None

    if name is None or deck_id is None:
        return (None, None)

    try:
        return (str(name), int(deck_id))
    except Exception:
        return (None, None)


def _collect_decks(col) -> List[DeckInfo]:
    decks: List[DeckInfo] = []

    if hasattr(col.decks, "all_names_and_ids"):
        for entry in col.decks.all_names_and_ids():
            name, deck_id = _deck_name_and_id(entry)
            if name is None or deck_id is None:
                continue
            deck = col.decks.get(deck_id)
            is_dynamic = bool(deck.get("dyn")) if deck else False
            decks.append(DeckInfo(deck_id=deck_id, name=name, is_dynamic=is_dynamic))
        return _mark_parent_decks(decks)

    if hasattr(col.decks, "all"):
        for deck in col.decks.all():
            if not isinstance(deck, dict):
                continue
            name = deck.get("name")
            deck_id = deck.get("id")
            if name is None or deck_id is None:
                continue
            decks.append(DeckInfo(deck_id=deck_id, name=name, is_dynamic=bool(deck.get("dyn"))))
        return _mark_parent_decks(decks)

    # fallback to internal decks dict
    for deck_id, deck in getattr(col.decks, "decks", {}).items():
        name = deck.get("name")
        if name is None:
            continue
        decks.append(DeckInfo(deck_id=int(deck_id), name=name, is_dynamic=bool(deck.get("dyn"))))

    return _mark_parent_decks(decks)


def _deck_name_has_children(name: str, all_names: Iterable[str]) -> bool:
    prefix = f"{name}::"
    for other in all_names:
        if other != name and other.startswith(prefix):
            return True
    return False


def _mark_parent_decks(decks: List[DeckInfo]) -> List[DeckInfo]:
    all_names = [deck.name for deck in decks]
    return [
        DeckInfo(
            deck_id=deck.deck_id,
            name=deck.name,
            is_dynamic=deck.is_dynamic,
            has_children=_deck_name_has_children(deck.name, all_names),
        )
        for deck in decks
    ]
