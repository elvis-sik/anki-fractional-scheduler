from __future__ import annotations

import json
import os
import sys

from aqt import gui_hooks, mw
from aqt.qt import QTimer

RESULT_ENV = "ANKI_ADDON_WORKBENCH_RESULT"
ADDON_MODULE = "fractional_scheduler"
TOOLS_ACTION = "Fractional Scheduler: Open Config"


def _write(payload: dict) -> None:
    with open(os.environ[RESULT_ENV], "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _run_checks() -> None:
    try:
        tools_actions = [action.text() for action in mw.form.menuTools.actions()]
        _write(
            {
                "ok": ADDON_MODULE in sys.modules and TOOLS_ACTION in tools_actions,
                "checks": [
                    {"name": "addon module loaded", "ok": ADDON_MODULE in sys.modules},
                    {"name": "Tools action registered", "ok": TOOLS_ACTION in tools_actions},
                ],
                "tools_actions": tools_actions,
            }
        )
    except Exception as exc:
        _write({"ok": False, "error": repr(exc)})
    finally:
        mw.app.quit()


def _after_profile_open() -> None:
    QTimer.singleShot(0, _run_checks)


gui_hooks.profile_did_open.append(_after_profile_open)
