from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


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

    phases = _assign_phases(schedule, deck_names)
    results: Dict[str, List[int]] = {}

    if schedule["type"] == "every_n_days":
        n = int(schedule["n"])
        m = int(schedule["m"])
        pattern = bresenham_pattern(m, n)
        for name in deck_names:
            phase = phases.get(name, 0)
            seq = []
            for i in range(days):
                idx = (day_index + phase + i) % n
                seq.append(int(pattern[idx]))
            results[name] = seq
        return results

    # day-of-week
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
    assignments: Dict[int, Dict[str, Any]] = {}

    for deck in decks:
        if deck.is_dynamic:
            continue
        sched = _best_schedule_for_deck(deck.name, config.schedules)
        if sched is None:
            continue
        if sched.get("leaf_only", True) and deck.has_children:
            continue
        assignments[deck.deck_id] = sched

    # group by schedule id
    schedule_to_deck_names: Dict[str, List[str]] = {}
    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue
        schedule_to_deck_names.setdefault(sched["id"], []).append(deck.name)

    anki_today_num = anki_today(col)
    rollover_hours = _rollover_hours(col)
    epoch_day = anki_day_number_from_date_str(config.epoch, rollover_hours)
    day_index = anki_today_num - epoch_day

    # compute weekday index based on anki day start
    day_start_ts = (anki_today_num * 86400) + (rollover_hours * 3600)
    weekday_idx = datetime.fromtimestamp(day_start_ts, tz=_local_tzinfo()).weekday()  # Mon=0

    results: List[DeckLimit] = []

    for deck in decks:
        sched = assignments.get(deck.deck_id)
        if not sched:
            continue

        deck_names = schedule_to_deck_names.get(sched["id"], [])
        phases = _assign_phases(sched, deck_names)
        phase = phases.get(deck.name, 0)

        if sched["type"] == "every_n_days":
            n = int(sched["n"])
            m = int(sched["m"])
            pattern = bresenham_pattern(m, n)
            idx = (day_index + phase) % n
            limit = int(pattern[idx])
        else:
            idx = (weekday_idx + phase) % 7
            day_key = VALID_DAYS[idx]
            limit = int(sched["by_day"].get(day_key, 0))

        results.append(DeckLimit(deck_id=deck.deck_id, name=deck.name, limit=limit))

    return results


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
