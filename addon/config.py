from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from aqt import mw
except Exception:  # pragma: no cover - when imported outside Anki
    mw = None


VALID_TYPES = {"every_n_days", "dow"}
VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_FRACTIONAL_STRATEGY = "fraction_first"
VALID_FRACTIONAL_STRATEGIES = {"hash", "fraction_first", "balance_first"}
DEFAULT_NOTIFY_DESCENDANT_MODE = "direct_only"
VALID_NOTIFY_DESCENDANT_MODES = {
    DEFAULT_NOTIFY_DESCENDANT_MODE,
    "any_blocked_descendant",
    "all_included_descendants_blocked",
    "hide_container_rows",
}


@dataclass(frozen=True)
class AddonConfig:
    epoch: str
    schedules: List[Dict[str, Any]]
    defaults: Dict[str, Any]
    migration: Dict[str, Any]


DEFAULT_CONFIG: Dict[str, Any] = {
    "epoch": "2026-01-01",
    "schedules": [],
    "defaults": {
        "apply_on_profile_open": True,
        "apply_on_collection_open": True,
        "apply_on_sync": False,
        "apply_once_per_day": True,
        "dry_run": False,
        "log_level": "info",
    },
    "migration": {
        "notify_empty_decks_imported": False,
        "notify_empty_decks_status": "pending",
    },
}


VALID_STAGGER_MODES = {"stable"}


def _log(level: str, message: str) -> None:
    if mw is None:
        return
    print(f"[FractionalScheduler] {level.upper()}: {message}")


def _get_config(addon_name: str) -> Dict[str, Any]:
    if mw is None:
        return DEFAULT_CONFIG.copy()
    raw = mw.addonManager.getConfig(addon_name)
    if raw is None:
        return DEFAULT_CONFIG.copy()
    return raw


def load_config(addon_name: str) -> AddonConfig:
    raw = _get_config(addon_name)
    return normalize_config(raw)


def save_config(addon_name: str, config: AddonConfig | Dict[str, Any]) -> None:
    if mw is None:
        return
    mw.addonManager.writeConfig(addon_name, config_to_dict(config))


def config_to_dict(config: AddonConfig | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(config, AddonConfig):
        source = {
            "epoch": config.epoch,
            "schedules": config.schedules,
            "defaults": config.defaults,
            "migration": config.migration,
        }
    else:
        source = config

    normalized = normalize_config(source)
    return {
        "epoch": normalized.epoch,
        "schedules": [_schedule_to_dict(sched) for sched in normalized.schedules],
        "defaults": dict(normalized.defaults),
        "migration": dict(normalized.migration),
    }


def sync_deck_target_names(col, config: AddonConfig | Dict[str, Any]) -> bool:
    """Update exact deck targets after Anki has renamed their decks.

    Schedule targets remain human-readable names, but an exact target is bound
    to the deck ID that existed when it was first seen. Deck IDs survive both
    direct deck renames and parent-deck renames, which also rename descendants.
    Exact targets and simple subtree rules (for example ``!Archive*``) are
    tracked too; their ``!`` and trailing ``*`` are retained while the
    underlying deck name changes. General wildcards such as ``*Archive*``
    intentionally keep their text-only semantics.
    """
    names_by_id = _deck_names_by_id(col)
    if not names_by_id:
        return False

    schedules = config.schedules if isinstance(config, AddonConfig) else config.get("schedules", [])
    changed = False
    for schedule in schedules:
        if not isinstance(schedule, dict):
            continue

        targets = list(schedule.get("targets", []) or [])
        bindings = _normalize_target_deck_ids(schedule.get("target_deck_ids"), targets)
        updated_targets: List[str] = []
        updated_bindings: Dict[str, int] = {}

        for target in targets:
            deck_id = bindings.get(target)
            current_name = names_by_id.get(deck_id) if deck_id is not None else None
            if current_name is None and _is_rename_tracking_target(target):
                target_name = _target_name_for_binding(target)
                deck_id = _deck_id_for_name(names_by_id, target_name)
                if deck_id is None and _is_subtree_target(target):
                    # Migrate old ``Deck*`` rules after their root deck has
                    # been moved under a parent. Only a unique suffix match is
                    # safe to adopt automatically.
                    deck_id = _deck_id_for_unique_suffix(names_by_id, target_name)
                current_name = names_by_id.get(deck_id) if deck_id is not None else None

            updated_target = _format_target(target, current_name) if current_name else target
            if updated_target not in updated_targets:
                updated_targets.append(updated_target)
            if deck_id is not None and current_name is not None:
                updated_bindings[updated_target] = deck_id

        if targets != updated_targets:
            schedule["targets"] = updated_targets
            changed = True
        if bindings != updated_bindings:
            if updated_bindings:
                schedule["target_deck_ids"] = updated_bindings
            else:
                schedule.pop("target_deck_ids", None)
            changed = True

    return changed


def normalize_config(raw: Dict[str, Any]) -> AddonConfig:
    epoch = raw.get("epoch", DEFAULT_CONFIG["epoch"])

    defaults = DEFAULT_CONFIG["defaults"].copy()
    defaults.update(raw.get("defaults") or {})
    migration = DEFAULT_CONFIG["migration"].copy()
    migration.update(raw.get("migration") or {})

    schedules_in = raw.get("schedules") or []
    schedules: List[Dict[str, Any]] = []

    for idx, sched in enumerate(schedules_in):
        if not isinstance(sched, dict):
            _log("warn", f"Skipping schedule at index {idx}: not a dict")
            continue

        sched_type = sched.get("type")
        if sched_type not in VALID_TYPES:
            _log("warn", f"Skipping schedule at index {idx}: invalid type {sched_type}")
            continue

        targets = sched.get("targets") or []
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            _log("warn", f"Skipping schedule at index {idx}: targets must be list of strings")
            continue

        normalized: Dict[str, Any] = {
            "_uid": str(sched.get("_uid") or uuid.uuid4()),
            "id": str(sched.get("id") or f"Schedule {idx + 1}"),
            "type": sched_type,
            "targets": targets,
            "leaf_only": bool(sched.get("leaf_only", True)),
            "fractional_enabled": bool(sched.get("fractional_enabled", True)),
            "notify_enabled": bool(sched.get("notify_enabled", False)),
            "notify_descendant_mode": _normalize_notify_descendant_mode(sched.get("notify_descendant_mode")),
        }

        target_deck_ids = _normalize_target_deck_ids(sched.get("target_deck_ids"), targets)
        if target_deck_ids:
            normalized["target_deck_ids"] = target_deck_ids

        if sched_type == "every_n_days":
            m = sched.get("m")
            n = sched.get("n")
            if not isinstance(m, int) or not isinstance(n, int) or m < 0 or n <= 0 or m > n:
                _log("warn", f"Skipping schedule {normalized['id']}: invalid m/n")
                continue
            normalized["m"] = m
            normalized["n"] = n
            normalized["fractional_strategy"] = _normalize_fractional_strategy(sched)
        else:
            by_day = sched.get("by_day") or {}
            if not isinstance(by_day, dict):
                _log("warn", f"Skipping schedule {normalized['id']}: by_day must be dict")
                continue
            normalized_by_day: Dict[str, int] = {d: int(by_day.get(d, 0) or 0) for d in VALID_DAYS}
            normalized["by_day"] = normalized_by_day

        stagger = sched.get("stagger")
        if stagger is not None:
            if not isinstance(stagger, dict) or stagger.get("mode") not in VALID_STAGGER_MODES:
                _log("warn", f"Ignoring stagger for {normalized['id']}: invalid mode")
            else:
                normalized["stagger"] = {
                    "mode": "stable",
                }

        stagger_state = _normalize_stagger_state(sched.get("stagger_state"))
        if stagger_state is not None:
            normalized["stagger_state"] = stagger_state

        schedules.append(normalized)

    return AddonConfig(
        epoch=epoch,
        schedules=schedules,
        defaults=defaults,
        migration=migration,
    )


def _normalize_notify_descendant_mode(value: Any) -> str:
    mode = str(value or DEFAULT_NOTIFY_DESCENDANT_MODE)
    if mode == "aggregate_children":
        mode = "any_blocked_descendant"
    if mode not in VALID_NOTIFY_DESCENDANT_MODES:
        return DEFAULT_NOTIFY_DESCENDANT_MODE
    return mode


def _normalize_fractional_strategy(sched: Dict[str, Any]) -> str:
    explicit = str(sched.get("fractional_strategy") or "")
    if explicit in VALID_FRACTIONAL_STRATEGIES:
        return explicit

    stagger = sched.get("stagger")
    legacy_mode = str(stagger.get("mode") or "") if isinstance(stagger, dict) else ""
    if legacy_mode == "hash":
        return "hash"

    return DEFAULT_FRACTIONAL_STRATEGY


def _normalize_stagger_state(raw_state: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_state, dict):
        return None

    assignments_in = raw_state.get("assignments")
    assignments: Dict[str, int] = {}
    if isinstance(assignments_in, dict):
        for raw_deck_id, raw_phase in assignments_in.items():
            try:
                deck_id = str(int(raw_deck_id))
                phase = int(raw_phase)
            except Exception:
                continue
            if phase >= 0:
                assignments[deck_id] = phase

    normalized: Dict[str, Any] = {"assignments": assignments}

    schedule_type = raw_state.get("schedule_type")
    if isinstance(schedule_type, str) and schedule_type in VALID_TYPES:
        normalized["schedule_type"] = schedule_type

    raw_cycle_length = raw_state.get("cycle_length")
    try:
        cycle_length = int(raw_cycle_length) if raw_cycle_length is not None else None
    except Exception:
        cycle_length = None
    if cycle_length is not None and cycle_length > 0:
        normalized["cycle_length"] = cycle_length

    return normalized


def _is_exact_target(target: str) -> bool:
    target_name = _target_name(target)
    return bool(target_name) and "*" not in target_name and "?" not in target_name


def _is_subtree_target(target: str) -> bool:
    target_name = _target_name(target)
    return (
        len(target_name) > 1
        and target_name.endswith("*")
        and "*" not in target_name[:-1]
        and "?" not in target_name
    )


def _is_rename_tracking_target(target: str) -> bool:
    return _is_exact_target(target) or _is_subtree_target(target)


def _target_name(target: str) -> str:
    return target[1:] if target.startswith("!") else target


def _target_name_for_binding(target: str) -> str:
    target_name = _target_name(target)
    return target_name[:-1] if _is_subtree_target(target) else target_name


def _format_target(original_target: str, name: str) -> str:
    prefix = "!" if original_target.startswith("!") else ""
    suffix = "*" if _is_subtree_target(original_target) else ""
    return f"{prefix}{name}{suffix}"


def _normalize_target_deck_ids(raw_bindings: Any, targets: List[str]) -> Dict[str, int]:
    if not isinstance(raw_bindings, dict):
        return {}

    target_set = set(targets)
    bindings: Dict[str, int] = {}
    for raw_target, raw_deck_id in raw_bindings.items():
        if (
            not isinstance(raw_target, str)
            or raw_target not in target_set
            or not _is_rename_tracking_target(raw_target)
        ):
            continue
        try:
            bindings[raw_target] = int(raw_deck_id)
        except Exception:
            continue
    return bindings


def _deck_names_by_id(col) -> Dict[int, str]:
    decks = getattr(col, "decks", None)
    all_names_and_ids = getattr(decks, "all_names_and_ids", None)
    if not callable(all_names_and_ids):
        return {}

    names_by_id: Dict[int, str] = {}
    try:
        entries = all_names_and_ids()
    except Exception:
        return {}

    for entry in entries:
        if isinstance(entry, dict):
            name, deck_id = entry.get("name"), entry.get("id")
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            name, deck_id = entry[0], entry[1]
        else:
            name, deck_id = getattr(entry, "name", None), getattr(entry, "id", None)
        try:
            if isinstance(name, str) and deck_id is not None:
                names_by_id[int(deck_id)] = name
        except Exception:
            continue
    return names_by_id


def _deck_id_for_name(names_by_id: Dict[int, str], target: str) -> Optional[int]:
    for deck_id, name in names_by_id.items():
        if name == target:
            return deck_id
    return None


def _deck_id_for_unique_suffix(names_by_id: Dict[int, str], target: str) -> Optional[int]:
    suffix = f"::{target}"
    candidates = [deck_id for deck_id, name in names_by_id.items() if name.endswith(suffix)]
    return candidates[0] if len(candidates) == 1 else None


def _schedule_to_dict(sched: Dict[str, Any]) -> Dict[str, Any]:
    persisted: Dict[str, Any] = {
        "id": str(sched.get("id", "")),
        "type": str(sched.get("type", "every_n_days")),
        "targets": list(sched.get("targets", []) or []),
        "leaf_only": bool(sched.get("leaf_only", True)),
        "fractional_enabled": bool(sched.get("fractional_enabled", True)),
        "notify_enabled": bool(sched.get("notify_enabled", False)),
        "notify_descendant_mode": _normalize_notify_descendant_mode(sched.get("notify_descendant_mode")),
    }

    if persisted["type"] == "every_n_days":
        persisted["m"] = int(sched.get("m", 1))
        persisted["n"] = int(sched.get("n", 3))
        persisted["fractional_strategy"] = _normalize_fractional_strategy(sched)
    else:
        persisted["by_day"] = dict(sched.get("by_day", {}) or {})

    if sched.get("stagger") is not None:
        persisted["stagger"] = {"mode": "stable"}

    stagger_state = _normalize_stagger_state(sched.get("stagger_state"))
    if stagger_state is not None:
        persisted["stagger_state"] = stagger_state

    target_deck_ids = _normalize_target_deck_ids(sched.get("target_deck_ids"), persisted["targets"])
    if target_deck_ids:
        persisted["target_deck_ids"] = target_deck_ids

    return persisted
