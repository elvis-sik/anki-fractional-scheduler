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

    def test_exact_targets_follow_deck_and_parent_renames_by_id(self) -> None:
        class Decks:
            def all_names_and_ids(self):
                return [
                    {"name": "Renamed", "id": 1},
                    {"name": "Renamed::Child", "id": 2},
                    {"name": "Other", "id": 3},
                ]

        col = type("Col", (), {"decks": Decks()})()
        config_data = config.normalize_config(
            {
                "schedules": [
                    {
                        "id": "renamed",
                        "type": "every_n_days",
                        "m": 1,
                        "n": 3,
                        "targets": ["Original", "Original::Child", "Other*"],
                        "target_deck_ids": {"Original": 1, "Original::Child": 2},
                    }
                ]
            }
        )

        self.assertTrue(config.sync_deck_target_names(col, config_data))
        schedule = config_data.schedules[0]
        self.assertEqual(schedule["targets"], ["Renamed", "Renamed::Child", "Other*"])
        self.assertEqual(schedule["target_deck_ids"], {"Renamed": 1, "Renamed::Child": 2, "Other*": 3})
        self.assertEqual(
            config.config_to_dict(config_data)["schedules"][0]["target_deck_ids"],
            {"Renamed": 1, "Renamed::Child": 2, "Other*": 3},
        )
        self.assertFalse(config.sync_deck_target_names(col, config_data))

    def test_existing_exact_target_is_bound_on_first_collection_read(self) -> None:
        class Decks:
            def all_names_and_ids(self):
                return [("Geography", 42)]

        col = type("Col", (), {"decks": Decks()})()
        config_data = config.normalize_config(
            {
                "schedules": [
                    {
                        "id": "geography",
                        "type": "every_n_days",
                        "m": 1,
                        "n": 3,
                        "targets": ["Geography", "Geo*"],
                    }
                ]
            }
        )

        self.assertTrue(config.sync_deck_target_names(col, config_data))
        self.assertEqual(config_data.schedules[0]["target_deck_ids"], {"Geography": 42})

    def test_exact_exclusion_target_follows_deck_rename_by_id(self) -> None:
        class Decks:
            def all_names_and_ids(self):
                return [("Archive Renamed", 42)]

        col = type("Col", (), {"decks": Decks()})()
        config_data = config.normalize_config(
            {
                "schedules": [
                    {
                        "id": "exclude",
                        "type": "every_n_days",
                        "m": 1,
                        "n": 3,
                        "targets": ["!Archive"],
                        "target_deck_ids": {"!Archive": 42},
                    }
                ]
            }
        )

        self.assertTrue(config.sync_deck_target_names(col, config_data))
        self.assertEqual(config_data.schedules[0]["targets"], ["!Archive Renamed"])
        self.assertEqual(config_data.schedules[0]["target_deck_ids"], {"!Archive Renamed": 42})

    def test_subtree_exclusion_target_follows_parent_rename_by_id(self) -> None:
        class Decks:
            def all_names_and_ids(self):
                return [("Decks::Geography::GeoTrainer", 42)]

        col = type("Col", (), {"decks": Decks()})()
        config_data = config.normalize_config(
            {
                "schedules": [
                    {
                        "id": "exclude-subtree",
                        "type": "every_n_days",
                        "m": 1,
                        "n": 3,
                        "targets": ["!GeoTrainer*"],
                        "target_deck_ids": {"!GeoTrainer*": 42},
                    }
                ]
            }
        )

        self.assertTrue(config.sync_deck_target_names(col, config_data))
        self.assertEqual(config_data.schedules[0]["targets"], ["!Decks::Geography::GeoTrainer*"])
        self.assertEqual(
            config_data.schedules[0]["target_deck_ids"], {"!Decks::Geography::GeoTrainer*": 42}
        )

    def test_legacy_subtree_rule_recovers_a_unique_moved_deck(self) -> None:
        class Decks:
            def all_names_and_ids(self):
                return [
                    ("Decks::Geography::GeoTrainer", 42),
                    ("Decks::Geography::GeoTrainer::World", 43),
                ]

        col = type("Col", (), {"decks": Decks()})()
        config_data = config.normalize_config(
            {
                "schedules": [
                    {
                        "id": "migrate-subtree",
                        "type": "every_n_days",
                        "m": 1,
                        "n": 3,
                        "targets": ["!GeoTrainer*"],
                    }
                ]
            }
        )

        self.assertTrue(config.sync_deck_target_names(col, config_data))
        self.assertEqual(config_data.schedules[0]["targets"], ["!Decks::Geography::GeoTrainer*"])
        self.assertEqual(
            config_data.schedules[0]["target_deck_ids"], {"!Decks::Geography::GeoTrainer*": 42}
        )


if __name__ == "__main__":
    unittest.main()
