from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def _rollover_hours(col) -> int:
    try:
        return int(col.conf.get("rollover", 4))
    except Exception:
        return 4


def anki_day_number_from_timestamp(ts: float, rollover_hours: int) -> int:
    rollover_seconds = rollover_hours * 3600
    return int((ts - rollover_seconds) // 86400)


def anki_today(col) -> int:
    if hasattr(col, "sched") and hasattr(col.sched, "today"):
        try:
            return int(col.sched.today)
        except Exception:
            pass
    return anki_day_number_from_timestamp(time.time(), _rollover_hours(col))


def anki_day_number_from_date_str(date_str: str, rollover_hours: int) -> int:
    year, month, day = [int(x) for x in date_str.split("-")]
    dt = datetime(year, month, day, 0, 0, 0, tzinfo=_local_tzinfo())
    return anki_day_number_from_timestamp(dt.timestamp(), rollover_hours)


def bresenham_pattern(m: int, n: int) -> List[int]:
    if n <= 0:
        return []
    if m <= 0:
        return [0] * n
    if m >= n:
        return [1] * n

    pattern: List[int] = []
    acc = n - m
    for _ in range(n):
        acc += m
        if acc >= n:
            pattern.append(1)
            acc -= n
        else:
            pattern.append(0)
    return pattern


def match_deck_names(targets: Iterable[str], deck_names: Iterable[str]) -> List[str]:
    matches: List[str] = []
    for name in deck_names:
        for target in targets:
            if _target_matches(target, name) is not None:
                matches.append(name)
                break
    return matches


def filter_deck_names_for_schedule(
    schedule: Dict[str, Any], deck_names: Iterable[str]
) -> List[str]:
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

    day_start_ts = (anki_today_num * 86400) + (rollover_hours * 3600)
    weekday_idx = datetime.fromtimestamp(day_start_ts, tz=_local_tzinfo()).weekday()

    results: Dict[str, List[int]] = {}

    if schedule["type"] == "every_n_days":
        effective_day_indices = _preview_every_n_days_day_indices(
            col,
            schedule,
            deck_names,
            epoch,
            day_index,
        )
        for name in deck_names:
            effective_day_index = effective_day_indices.get(name, day_index)
            pattern, phase = _every_n_days_pattern_and_phase(schedule, name, deck_names)
            seq = []
            for i in range(days):
                seq.append(_every_n_days_limit_from_pattern(pattern, phase, effective_day_index + i))
            results[name] = seq
        return results

    # day-of-week
    phases = _assign_phases(schedule, deck_names)
    by_day = schedule.get("by_day") or {}
    for name in deck_names:
        phase = phases.get(name, 0)
        seq = []
        for i in range(days):
            idx = (weekday_idx + phase + i) % 7
            day_key = VALID_DAYS[idx]
            seq.append(int(by_day.get(day_key, 0)))
        results[name] = seq
    return results


def _target_matches(target: str, deck_name: str) -> Optional[Tuple[int, int]]:
    if target.endswith("*"):
        prefix = target[:-1]
        if deck_name.startswith(prefix):
            return (1, len(prefix))
        return None
    if deck_name == target:
        return (2, len(target))
    return None


def _best_schedule_for_deck(
    deck_name: str, schedules: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    best = None
    best_score: Tuple[int, int, int] = (-1, -1, -1)

    for idx, sched in enumerate(schedules):
        targets = sched.get("targets") or []
        best_target_score: Optional[Tuple[int, int]] = None

        for target in targets:
            match = _target_matches(target, deck_name)
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


def _schedule_cycle_length(schedule: Dict[str, Any]) -> int:
    if schedule["type"] == "every_n_days":
        return int(schedule["n"])
    return 7


def _hash_phase(seed: str, deck_name: str, modulo: int) -> int:
    h = hashlib.sha256((seed + "::" + deck_name).encode("utf-8")).hexdigest()
    return int(h, 16) % modulo


def _assign_phases(schedule: Dict[str, Any], deck_names: List[str]) -> Dict[str, int]:
    modulo = _schedule_cycle_length(schedule)
    if modulo <= 0:
        return {name: 0 for name in deck_names}

    stagger = schedule.get("stagger")
    if stagger is None and len(deck_names) > 1:
        stagger = {"mode": "balanced"}

    if stagger is None:
        return {name: 0 for name in deck_names}

    mode = stagger.get("mode")
    if mode == "hash":
        seed = str(stagger.get("seed") or schedule.get("id") or "")
        return {name: _hash_phase(seed, name, modulo) for name in deck_names}

    # balanced (default)
    sorted_names = sorted(deck_names)
    return {name: idx % modulo for idx, name in enumerate(sorted_names)}


def compute_deck_limits(col, config) -> List[DeckLimit]:
    decks = _collect_decks(col)
    assignments, schedule_to_deck_names = _schedule_assignments(decks, config.schedules)
    calendar_state = _calendar_state(col, config.epoch)
    every_n_day_indices = _effective_every_n_days_day_indices(
        col,
        config.epoch,
        decks,
        assignments,
        schedule_to_deck_names,
        calendar_state["day_index"],
    )

    results: List[DeckLimit] = []

    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue

        deck_names = schedule_to_deck_names.get(sched["id"], [])
        day_index = every_n_day_indices.get(deck.deck_id, calendar_state["day_index"])
        limit = _limit_for_offset(
            sched,
            deck.name,
            deck_names,
            day_index,
            calendar_state["weekday_idx"],
            0,
        )

        results.append(DeckLimit(deck_id=deck.deck_id, name=deck.name, limit=limit))

    return results


def compute_schedule_health_snapshot(col, config) -> Dict[int, FractionalDeckHealth]:
    decks = _collect_decks(col)
    assignments, schedule_to_deck_names = _schedule_assignments(decks, config.schedules)
    calendar_state = _calendar_state(col, config.epoch)
    every_n_day_indices = _effective_every_n_days_day_indices(
        col,
        config.epoch,
        decks,
        assignments,
        schedule_to_deck_names,
        calendar_state["day_index"],
    )

    snapshot: Dict[int, FractionalDeckHealth] = {}

    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue

        cycle_length = _schedule_cycle_length(sched)
        deck_names = schedule_to_deck_names.get(str(sched["id"]), [])
        day_index = every_n_day_indices.get(deck.deck_id, calendar_state["day_index"])
        next_positive_offset: Optional[int] = None

        for offset in range(cycle_length):
            limit = _limit_for_offset(
                sched,
                deck.name,
                deck_names,
                day_index,
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
    decks: List[DeckInfo], schedules: List[Dict[str, Any]]
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, List[str]]]:
    assignments: Dict[int, Dict[str, Any]] = {}

    for deck in decks:
        if deck.is_dynamic:
            continue
        sched = _best_schedule_for_deck(deck.name, schedules)
        if sched is None:
            continue
        if sched.get("leaf_only", True) and deck.has_children:
            continue
        assignments[deck.deck_id] = sched

    schedule_to_deck_names: Dict[str, List[str]] = {}
    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue
        schedule_to_deck_names.setdefault(str(sched["id"]), []).append(deck.name)

    return assignments, schedule_to_deck_names


def _calendar_state(col, epoch: str) -> Dict[str, int]:
    anki_today_num = anki_today(col)
    rollover_hours = _rollover_hours(col)
    epoch_day = anki_day_number_from_date_str(epoch, rollover_hours)
    day_index = anki_today_num - epoch_day
    day_start_ts = (anki_today_num * 86400) + (rollover_hours * 3600)
    weekday_idx = datetime.fromtimestamp(day_start_ts, tz=_local_tzinfo()).weekday()
    return {
        "day_index": day_index,
        "weekday_idx": weekday_idx,
    }


def _preview_every_n_days_day_indices(
    col,
    schedule: Dict[str, Any],
    deck_names: List[str],
    epoch: str,
    raw_day_index: int,
) -> Dict[str, int]:
    if raw_day_index <= 0 or not deck_names:
        return {}

    decks = _collect_decks(col)
    decks_by_name = {deck.name: deck for deck in decks}
    scheduled_decks = [decks_by_name[name] for name in deck_names if name in decks_by_name]
    if not scheduled_decks:
        return {}

    day_indices_by_deck = _effective_every_n_days_day_indices_for_schedule(
        col,
        epoch,
        decks,
        schedule,
        scheduled_decks,
        deck_names,
        raw_day_index,
    )
    return {deck.name: day_indices_by_deck.get(deck.deck_id, raw_day_index) for deck in scheduled_decks}


def _effective_every_n_days_day_indices(
    col,
    epoch: str,
    decks: List[DeckInfo],
    assignments: Dict[int, Dict[str, Any]],
    schedule_to_deck_names: Dict[str, List[str]],
    raw_day_index: int,
) -> Dict[int, int]:
    if raw_day_index <= 0:
        return {}

    effective: Dict[int, int] = {}

    schedule_to_decks: Dict[str, List[DeckInfo]] = {}
    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched or sched["type"] != "every_n_days":
            continue
        schedule_to_decks.setdefault(str(sched["id"]), []).append(deck)

    for schedule_id, scheduled_decks in schedule_to_decks.items():
        if not scheduled_decks:
            continue
        schedule = assignments[scheduled_decks[0].deck_id]
        deck_names = schedule_to_deck_names.get(schedule_id, [])
        effective.update(
            _effective_every_n_days_day_indices_for_schedule(
                col,
                epoch,
                decks,
                schedule,
                scheduled_decks,
                deck_names,
                raw_day_index,
            )
        )

    return effective


def _effective_every_n_days_day_indices_for_schedule(
    col,
    epoch: str,
    all_decks: List[DeckInfo],
    schedule: Dict[str, Any],
    scheduled_decks: List[DeckInfo],
    deck_names: List[str],
    raw_day_index: int,
) -> Dict[int, int]:
    if raw_day_index <= 0 or not scheduled_decks:
        return {}

    introduced_days = _new_introduction_days_by_assigned_deck(
        col,
        epoch,
        all_decks,
        scheduled_decks,
        raw_day_index,
    )

    effective: Dict[int, int] = {}
    for deck in scheduled_decks:
        effective[deck.deck_id] = _shifted_every_n_days_day_index(
            raw_day_index,
            schedule,
            deck.name,
            deck_names,
            introduced_days.get(deck.deck_id, set()),
        )
    return effective


def _new_introduction_days_by_assigned_deck(
    col,
    epoch: str,
    all_decks: List[DeckInfo],
    scheduled_decks: List[DeckInfo],
    raw_day_index: int,
) -> Dict[int, Set[int]]:
    if raw_day_index <= 0 or not scheduled_decks:
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
        return {}

    rollover_hours = _rollover_hours(col)
    epoch_day = anki_day_number_from_date_str(epoch, rollover_hours)
    start_ms = int(((epoch_day * 86400) + (rollover_hours * 3600)) * 1000)
    end_day = epoch_day + raw_day_index
    end_ms = int(((end_day * 86400) + (rollover_hours * 3600)) * 1000)

    placeholders = ",".join("?" for _ in sorted(relevant_source_ids))
    sql = f"""
select r.id,
       case when c.odid = 0 then c.did else c.odid end as original_did
from revlog as r
join cards as c on c.id = r.cid
where r.id >= ?
  and r.id < ?
  and r.ease > 0
  and r.type = 0
  and r.lastIvl = 0
  and (case when c.odid = 0 then c.did else c.odid end) in ({placeholders})
"""

    try:
        rows = col.db.all(sql, start_ms, end_ms, *sorted(relevant_source_ids))
    except Exception:
        return {}

    introduced: Dict[int, Set[int]] = {}
    for revlog_id, source_did in rows:
        try:
            day_num = anki_day_number_from_timestamp(float(revlog_id) / 1000.0, rollover_hours)
            day_index = day_num - epoch_day
            source_deck_id = int(source_did)
        except Exception:
            continue
        if day_index < 0 or day_index >= raw_day_index:
            continue
        for target_deck_id in source_to_targets.get(source_deck_id, []):
            introduced.setdefault(target_deck_id, set()).add(day_index)

    return introduced


def _shifted_every_n_days_day_index(
    raw_day_index: int,
    schedule: Dict[str, Any],
    deck_name: str,
    deck_names: List[str],
    introduced_day_indices: Set[int],
) -> int:
    if raw_day_index <= 0 or not introduced_day_indices:
        return raw_day_index

    start_day = min(day for day in introduced_day_indices if day < raw_day_index)
    paused_days = 0
    pattern, phase = _every_n_days_pattern_and_phase(schedule, deck_name, deck_names)

    for calendar_day in range(start_day, raw_day_index):
        effective_day = calendar_day - paused_days
        scheduled_limit = _every_n_days_limit_from_pattern(pattern, phase, effective_day)
        if scheduled_limit > 0 and calendar_day not in introduced_day_indices:
            paused_days += 1

    return raw_day_index - paused_days


def _every_n_days_pattern_and_phase(
    schedule: Dict[str, Any],
    deck_name: str,
    deck_names: List[str],
) -> Tuple[List[int], int]:
    n = int(schedule["n"])
    if n <= 0:
        return ([], 0)
    phase = _assign_phases(schedule, deck_names).get(deck_name, 0)
    pattern = bresenham_pattern(int(schedule["m"]), n)
    return (pattern, phase)


def _every_n_days_limit_from_pattern(pattern: List[int], phase: int, day_index: int) -> int:
    if not pattern:
        return 0
    return int(pattern[(day_index + phase) % len(pattern)])


def _every_n_days_limit_for_day_index(
    schedule: Dict[str, Any],
    deck_name: str,
    deck_names: List[str],
    day_index: int,
) -> int:
    pattern, phase = _every_n_days_pattern_and_phase(schedule, deck_name, deck_names)
    return _every_n_days_limit_from_pattern(pattern, phase, day_index)


def _limit_for_offset(
    schedule: Dict[str, Any],
    deck_name: str,
    deck_names: List[str],
    day_index: int,
    weekday_idx: int,
    offset: int,
) -> int:
    phases = _assign_phases(schedule, deck_names)
    phase = phases.get(deck_name, 0)

    if schedule["type"] == "every_n_days":
        return _every_n_days_limit_for_day_index(
            schedule,
            deck_name,
            deck_names,
            day_index + offset,
        )

    idx = (weekday_idx + phase + offset) % 7
    day_key = VALID_DAYS[idx]
    return int((schedule.get("by_day") or {}).get(day_key, 0))


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
