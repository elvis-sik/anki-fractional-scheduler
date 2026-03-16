from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any, Dict, List, Optional

try:
    from aqt import mw
except Exception:  # pragma: no cover - when imported outside Anki
    mw = None


VALID_TYPES = {"every_n_days", "dow"}
VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class AddonConfig:
    epoch: str
    schedules: List[Dict[str, Any]]
    defaults: Dict[str, Any]


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
}


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


def normalize_config(raw: Dict[str, Any]) -> AddonConfig:
    epoch = raw.get("epoch", DEFAULT_CONFIG["epoch"])

    defaults = DEFAULT_CONFIG["defaults"].copy()
    defaults.update(raw.get("defaults") or {})

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
            if not isinstance(stagger, dict) or stagger.get("mode") not in {"balanced", "hash"}:
                _log("warn", f"Ignoring stagger for {normalized['id']}: invalid mode")
            else:
                normalized["stagger"] = {
                    "mode": stagger.get("mode"),
                    "seed": str(stagger.get("seed") or ""),
                }

        schedules.append(normalized)

    return AddonConfig(epoch=epoch, schedules=schedules, defaults=defaults)
