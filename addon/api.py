from __future__ import annotations

from .config import load_config
from .schedule import FractionalDeckHealth, compute_schedule_health_snapshot


class FractionalSchedulerAPI:
    def __init__(self, addon_name: str) -> None:
        self._addon_name = addon_name

    def get_schedule_health_snapshot(self, col) -> dict[int, FractionalDeckHealth]:
        if col is None:
            return {}
        config = load_config(self._addon_name)
        return compute_schedule_health_snapshot(col, config)
