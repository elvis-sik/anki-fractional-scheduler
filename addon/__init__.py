from __future__ import annotations

from .apply import apply_limits
from .config import load_config
from .schedule import compute_deck_limits
from .ui import SchedulerConfigDialog

try:
    from aqt import mw, gui_hooks
    from aqt.qt import QAction
    from aqt.utils import tooltip
except Exception:  # pragma: no cover
    mw = None
    gui_hooks = None
    QAction = None
    tooltip = None


LAST_APPLIED_DAY_CONFIG_KEY = "fractional_scheduler.last_applied_day"


def _log(message: str) -> None:
    if mw is None:
        return
    print(f"[FractionalScheduler] {message}")


def _today_key(col) -> int | None:
    sched = getattr(col, "sched", None)
    if sched is None:
        return None
    try:
        return int(sched.today)
    except Exception:
        return None


def _last_applied_day(col) -> int | None:
    try:
        value = col.get_config(LAST_APPLIED_DAY_CONFIG_KEY, None)
    except Exception:
        return None
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _record_applied_day(col) -> None:
    today = _today_key(col)
    if today is None:
        return
    try:
        col.set_config(LAST_APPLIED_DAY_CONFIG_KEY, today)
    except Exception:
        pass


def _should_skip_automatic_apply(col, config) -> bool:
    if not config.defaults.get("apply_once_per_day", True):
        return False
    today = _today_key(col)
    if today is None:
        return False
    return _last_applied_day(col) == today


def _apply(col, source: str) -> tuple[int, int, bool]:
    config = load_config(__name__)
    dry_run = bool(config.defaults.get("dry_run", False))

    limits = compute_deck_limits(col, config)
    applied = apply_limits(col, limits, dry_run=dry_run)

    if not dry_run:
        _record_applied_day(col)
    _log(f"Applied {len(applied)} today-only new limits ({source}, dry_run={dry_run})")
    return (len(applied), len(limits), dry_run)


def _on_profile_open() -> None:
    if mw is None or mw.col is None:
        return
    config = load_config(__name__)
    if not config.defaults.get("apply_on_profile_open", True):
        return
    if _should_skip_automatic_apply(mw.col, config):
        return
    _apply(mw.col, "profile_open")


def _on_collection_open(col) -> None:
    config = load_config(__name__)
    if not config.defaults.get("apply_on_collection_open", True):
        return
    if _should_skip_automatic_apply(col, config):
        return
    _apply(col, "collection_open")


def _on_sync_finish() -> None:
    if mw is None or mw.col is None:
        return
    config = load_config(__name__)
    if not config.defaults.get("apply_on_sync", False):
        return
    if _should_skip_automatic_apply(mw.col, config):
        return
    _apply(mw.col, "sync_finish")


def _apply_now() -> None:
    if mw is None or mw.col is None:
        return
    applied, total, dry_run = _apply(mw.col, "manual")
    if hasattr(mw, "reset"):
        try:
            mw.reset()
        except Exception:
            pass
    if tooltip is not None:
        mode = "dry-run: " if dry_run else ""
        tooltip(f"Fractional scheduler {mode}applied {applied}/{total} deck limits.", period=3500)


def _open_config() -> bool:
    if mw is None:
        return False
    dialog = SchedulerConfigDialog(__name__, parent=mw)
    dialog.exec()
    return True


def _setup_menu() -> None:
    if mw is None or QAction is None:
        return

    action_apply = QAction("Apply Fractional Schedule Now", mw)
    action_apply.triggered.connect(_apply_now)
    mw.form.menuTools.addAction(action_apply)

    action_config = QAction("Fractional Scheduler: Open Config", mw)
    action_config.triggered.connect(_open_config)
    mw.form.menuTools.addAction(action_config)

    try:
        mw.addonManager.setConfigAction(__name__, _open_config)
    except Exception:
        pass


if gui_hooks is not None:
    gui_hooks.profile_did_open.append(_on_profile_open)
    if hasattr(gui_hooks, "collection_did_open"):
        gui_hooks.collection_did_open.append(_on_collection_open)
    elif hasattr(gui_hooks, "collection_did_load"):
        gui_hooks.collection_did_load.append(_on_collection_open)
    if hasattr(gui_hooks, "sync_did_finish"):
        gui_hooks.sync_did_finish.append(_on_sync_finish)
    gui_hooks.main_window_did_init.append(_setup_menu)
