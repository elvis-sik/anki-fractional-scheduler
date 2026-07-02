from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = ROOT / "addon"
PACKAGE_NAME = "fractional_scheduler_lifecycle_test"
MISSING = object()


def _clear_test_package() -> None:
    for name in list(sys.modules):
        if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}."):
            del sys.modules[name]


def _restore_module(name: str, previous: object) -> None:
    if previous is MISSING:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = previous


def _load_addon_module():
    _clear_test_package()

    previous_aqt = sys.modules.get("aqt", MISSING)
    previous_aqt_qt = sys.modules.get("aqt.qt", MISSING)

    fake_aqt = types.ModuleType("aqt")
    fake_aqt.gui_hooks = None
    fake_aqt.mw = None

    fake_aqt_qt = types.ModuleType("aqt.qt")
    fake_aqt_qt.QAction = type("QAction", (), {})

    stub_ui = types.ModuleType(f"{PACKAGE_NAME}.ui")
    stub_ui.SchedulerConfigDialog = type("SchedulerConfigDialog", (), {})

    sys.modules["aqt"] = fake_aqt
    sys.modules["aqt.qt"] = fake_aqt_qt
    sys.modules[f"{PACKAGE_NAME}.ui"] = stub_ui

    try:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            ADDON_ROOT / "__init__.py",
            submodule_search_locations=[str(ADDON_ROOT)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load add-on module spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = module
        spec.loader.exec_module(module)
        return module
    finally:
        _restore_module("aqt", previous_aqt)
        _restore_module("aqt.qt", previous_aqt_qt)


class SyncHookTests(unittest.TestCase):
    def test_register_prefers_sync_will_start(self) -> None:
        addon = _load_addon_module()
        hooks = types.SimpleNamespace(sync_will_start=[], sync_did_finish=[])

        addon._register_sync_apply_hook(hooks)

        self.assertEqual([addon._on_sync_start], hooks.sync_will_start)
        self.assertEqual([], hooks.sync_did_finish)

    def test_register_falls_back_to_sync_did_finish(self) -> None:
        addon = _load_addon_module()
        hooks = types.SimpleNamespace(sync_did_finish=[])

        addon._register_sync_apply_hook(hooks)

        self.assertEqual([addon._on_sync_finish], hooks.sync_did_finish)

    def test_sync_start_applies_when_enabled(self) -> None:
        addon = _load_addon_module()
        col = object()
        config = types.SimpleNamespace(defaults={"apply_on_sync": True})
        calls = []

        addon.mw = types.SimpleNamespace(col=col)
        addon.load_config = lambda _addon_name: config
        addon._should_skip_automatic_apply = lambda _col, _config: False
        addon._apply = lambda applied_col, source: calls.append((applied_col, source))

        addon._on_sync_start()

        self.assertEqual([(col, "sync_start")], calls)

    def test_sync_start_respects_once_per_day_skip(self) -> None:
        addon = _load_addon_module()
        config = types.SimpleNamespace(defaults={"apply_on_sync": True})
        calls = []

        addon.mw = types.SimpleNamespace(col=object())
        addon.load_config = lambda _addon_name: config
        addon._should_skip_automatic_apply = lambda _col, _config: True
        addon._apply = lambda applied_col, source: calls.append((applied_col, source))

        addon._on_sync_start()

        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
