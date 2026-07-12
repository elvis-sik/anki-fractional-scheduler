from __future__ import annotations

from .api import FractionalSchedulerAPI
from .apply import apply_limits
from .config import load_config, save_config, sync_deck_target_names
from .notify import decorate_deck_browser
from .schedule import DEBUG_LOG_PATH, compute_deck_limits, debug_logging_enabled
from .ui import SchedulerConfigDialog

try:
    from aqt import gui_hooks, mw
    from aqt.qt import QAction
except Exception:  # pragma: no cover
    mw = None
    gui_hooks = None
    QAction = None


LAST_APPLIED_DAY_CONFIG_KEY = "fractional_scheduler.last_applied_day"


def _log(message: str) -> None:
    if mw is None:
        return
    print(f"[FractionalScheduler] {message}")


def _cleanup_legacy_debug_log() -> None:
    if debug_logging_enabled():
        return
    try:
        DEBUG_LOG_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _register_api_service() -> None:
    if mw is None:
        return
    try:
        mw.fractional_scheduler_api = FractionalSchedulerAPI(__name__)
    except Exception:
        pass


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


def _load_config_for_collection(col):
    config = load_config(__name__)
    if sync_deck_target_names(col, config):
        save_config(__name__, config)
    return config


def _apply(col, source: str) -> tuple[int, int, bool]:
    config = _load_config_for_collection(col)
    dry_run = bool(config.defaults.get("dry_run", False))

    limits = compute_deck_limits(col, config)
    save_config(__name__, config)
    applied = apply_limits(col, limits, dry_run=dry_run)

    if not dry_run:
        _record_applied_day(col)
    _log(f"Applied {len(applied)} today-only new limits ({source}, dry_run={dry_run})")
    return (len(applied), len(limits), dry_run)


def _on_profile_open() -> None:
    if mw is None or mw.col is None:
        return
    _cleanup_legacy_debug_log()
    _register_api_service()
    config = _load_config_for_collection(mw.col)
    if not config.defaults.get("apply_on_profile_open", True):
        return
    if _should_skip_automatic_apply(mw.col, config):
        return
    _apply(mw.col, "profile_open")


def _on_collection_open(col) -> None:
    _cleanup_legacy_debug_log()
    config = _load_config_for_collection(col)
    if not config.defaults.get("apply_on_collection_open", True):
        return
    if _should_skip_automatic_apply(col, config):
        return
    _apply(col, "collection_open")


def _apply_for_sync(source: str) -> None:
    if mw is None or mw.col is None:
        return
    config = _load_config_for_collection(mw.col)
    if not config.defaults.get("apply_on_sync", False):
        return
    if _should_skip_automatic_apply(mw.col, config):
        return
    _apply(mw.col, source)


def _on_sync_start() -> None:
    _apply_for_sync("sync_start")


def _on_sync_finish() -> None:
    _apply_for_sync("sync_finish")


def _register_sync_apply_hook(hooks) -> None:
    if hasattr(hooks, "sync_will_start"):
        hooks.sync_will_start.append(_on_sync_start)
    elif hasattr(hooks, "sync_did_finish"):
        hooks.sync_did_finish.append(_on_sync_finish)


def _open_config() -> bool:
    if mw is None:
        return False
    if mw.col is not None:
        _load_config_for_collection(mw.col)
    dialog = SchedulerConfigDialog(__name__, parent=mw, apply_callback=_manual_apply)
    dialog.exec()
    return True


def _manual_apply() -> tuple[int, int, bool]:
    if mw is None or mw.col is None:
        return (0, 0, False)
    return _apply(mw.col, "manual")


def _setup_menu() -> None:
    if mw is None or QAction is None:
        return

    _register_api_service()

    action_config = QAction("Fractional Scheduler: Open Config", mw)
    action_config.triggered.connect(_open_config)
    mw.form.menuTools.addAction(action_config)

    try:
        mw.addonManager.setConfigAction(__name__, _open_config)
    except Exception:
        pass


def _decorate_decks_screen(deck_browser, content) -> None:
    if mw is None or mw.col is None:
        return
    config = _load_config_for_collection(mw.col)
    decorate_deck_browser(deck_browser, content, config)


if mw is not None:
    _register_api_service()

if gui_hooks is not None:
    gui_hooks.profile_did_open.append(_on_profile_open)
    if hasattr(gui_hooks, "collection_did_open"):
        gui_hooks.collection_did_open.append(_on_collection_open)
    elif hasattr(gui_hooks, "collection_did_load"):
        gui_hooks.collection_did_load.append(_on_collection_open)
    _register_sync_apply_hook(gui_hooks)
    if hasattr(gui_hooks, "deck_browser_will_render_content"):
        gui_hooks.deck_browser_will_render_content.append(_decorate_decks_screen)
    gui_hooks.main_window_did_init.append(_setup_menu)
