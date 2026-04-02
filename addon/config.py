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


DEFAULT_CONFIG = {
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


VALID_STAGGER_MODES = {"stable", "balanced", "hash"}


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
            "notify_descendant_mode": _normalize_notify_descendant_mode(
                sched.get("notify_descendant_mode")
            ),
        }

        if sched_type == "every_n_days":
            m = sched.get("m")
            n = sched.get("n")
            if not isinstance(m, int) or not isinstance(n, int) or m < 0 or n <= 0 or m > n:
                _log("warn", f"Skipping schedule {normalized['id']}: invalid m/n")
                continue
            normalized["m"] = m
            normalized["n"] = n
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

    try:
        cycle_length = int(raw_state.get("cycle_length"))
    except Exception:
        cycle_length = None
    if cycle_length is not None and cycle_length > 0:
        normalized["cycle_length"] = cycle_length

    return normalized


def _schedule_to_dict(sched: Dict[str, Any]) -> Dict[str, Any]:
    persisted: Dict[str, Any] = {
        "id": str(sched.get("id", "")),
        "type": str(sched.get("type", "every_n_days")),
        "targets": list(sched.get("targets", []) or []),
        "leaf_only": bool(sched.get("leaf_only", True)),
        "fractional_enabled": bool(sched.get("fractional_enabled", True)),
        "notify_enabled": bool(sched.get("notify_enabled", False)),
        "notify_descendant_mode": _normalize_notify_descendant_mode(
            sched.get("notify_descendant_mode")
        ),
    }

    if persisted["type"] == "every_n_days":
        persisted["m"] = int(sched.get("m", 1))
        persisted["n"] = int(sched.get("n", 3))
    else:
        persisted["by_day"] = dict(sched.get("by_day", {}) or {})

    if sched.get("stagger") is not None:
        persisted["stagger"] = {"mode": "stable"}

    stagger_state = _normalize_stagger_state(sched.get("stagger_state"))
    if stagger_state is not None:
        persisted["stagger_state"] = stagger_state

    return persisted
