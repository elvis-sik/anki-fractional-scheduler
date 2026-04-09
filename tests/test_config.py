from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

import config  # noqa: E402


class NormalizeConfigTests(unittest.TestCase):
    def test_legacy_hash_stagger_migrates_to_hash_strategy(self) -> None:
        raw = {
            "schedules": [
                {
                    "id": "Legacy",
                    "type": "every_n_days",
                    "m": 1,
                    "n": 7,
                    "targets": ["Deck::*"],
                    "stagger": {"mode": "hash"},
                }
            ]
        }

        normalized = config.normalize_config(raw)

        self.assertEqual(normalized.schedules[0]["fractional_strategy"], "hash")
        self.assertNotIn("stagger", normalized.schedules[0])

    def test_unknown_every_n_days_strategy_falls_back_to_fraction_first(self) -> None:
        raw = {
            "schedules": [
                {
                    "id": "Legacy",
                    "type": "every_n_days",
                    "m": 1,
                    "n": 7,
                    "targets": ["Deck::*"],
                    "fractional_strategy": "mystery_mode",
                }
            ]
        }

        normalized = config.normalize_config(raw)

        self.assertEqual(
            normalized.schedules[0]["fractional_strategy"],
            config.DEFAULT_FRACTIONAL_STRATEGY,
        )

    def test_only_stable_stagger_mode_is_persisted(self) -> None:
        raw = {
            "schedules": [
                {
                    "id": "Stable",
                    "type": "every_n_days",
                    "m": 1,
                    "n": 7,
                    "targets": ["Deck::*"],
                    "fractional_strategy": "fraction_first",
                    "stagger": {"mode": "stable"},
                }
            ]
        }

        normalized = config.normalize_config(raw)

        self.assertEqual(normalized.schedules[0]["stagger"], {"mode": "stable"})


if __name__ == "__main__":
    unittest.main()
