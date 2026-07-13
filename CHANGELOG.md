# Changelog

## Unreleased

- Added ordered include/exclude deck rules. Prefix a target with `!` to exclude it; a later matching target can add it back.
- Deck-picker multi-selection now adds all chosen rules at once. Picker-created subtree rules and legacy unambiguous subtree rules now follow a renamed root deck.

## 0.4.1 - 2026-07-12

- Exact deck targets now stay attached when Anki renames the deck, including descendant renames caused by renaming a parent deck. Wildcard targets intentionally retain their text pattern.

## 0.4.0 - 2026-07-03

- Prepared the add-on for repeatable `.ankiaddon` packaging and AnkiWeb release checks with `anki-addon-release`.
- Refined the config dialog with an empty schedule state, tabbed schedule editing, and a sticky preview/queue area.
- Applied fractional Today-only limits before sync when Anki exposes the pre-sync hook.
- Kept the bundled notify badge settings inside the main scheduler dialog.
