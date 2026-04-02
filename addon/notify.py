from __future__ import annotations

import re
from html import escape
from typing import Dict, Optional, Tuple

from .notify_status import (
    NotifyDeckEntry,
    NotifyDirectMetrics,
    badge_label,
    badge_tooltip,
    compute_notify_states,
    should_show_badge,
)
from .schedule import (
    FEATURE_NOTIFY,
    collect_decks,
    compute_schedule_health_snapshot,
    schedule_assignments_for_feature,
)

try:
    from aqt import mw
except Exception:  # pragma: no cover
    mw = None


BADGE_STYLE = """
<style>
.fractional-scheduler-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.15em;
  height: 1.15em;
  margin-left: 0.45em;
  border-radius: 999px;
  color: #fff;
  font-size: 0.72em;
  font-weight: 700;
  line-height: 1;
  vertical-align: middle;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.08);
  cursor: help;
}

.fractional-scheduler-badge-limits {
  background: #c0392b;
}

.fractional-scheduler-badge-availability {
  background: #f39c12;
}
</style>
"""

DECK_LINK_RE = re.compile(
    r'(<a class="deck [^"]*"\s*href=# onclick="return pycmd\(\'open:(\d+)\'\)">.*?</a>)',
    re.DOTALL,
)


def decorate_deck_browser(_deck_browser, content, config) -> None:
    if mw is None or mw.col is None:
        return

    states = _compute_notify_states(config)
    if not states:
        return

    badges_by_did = {
        deck_id: _render_badge_html(state)
        for deck_id, state in states.items()
        if should_show_badge(state)
    }
    if not badges_by_did:
        return

    content.tree = BADGE_STYLE + _inject_badges(content.tree, badges_by_did)


def refresh_deck_browser() -> None:
    if mw is None:
        return
    deck_browser = getattr(mw, "deckBrowser", None)
    if deck_browser and getattr(mw, "state", None) == "deckBrowser":
        deck_browser.refresh()


def _compute_notify_states(config) -> Dict[int, object]:
    decks = collect_decks(mw.col)
    assignments, _schedule_to_decks = schedule_assignments_for_feature(
        decks,
        config.schedules,
        FEATURE_NOTIFY,
    )
    if not assignments:
        return {}

    effective_new_counts = _build_effective_new_count_map()
    total_cards_by_deck_id = {deck.deck_id: _count_total_cards(deck.deck_id) for deck in decks}
    entries = [
        NotifyDeckEntry(
            deck_id=deck.deck_id,
            name=deck.name,
            has_children=deck.has_children,
            is_filtered=deck.is_dynamic,
            is_container=total_cards_by_deck_id.get(deck.deck_id, 0) == 0 and deck.has_children,
        )
        for deck in decks
    ]
    metrics_by_deck_id = {
        deck.deck_id: NotifyDirectMetrics(
            total_cards=total_cards_by_deck_id.get(deck.deck_id, 0),
            new_limit=_get_config_new_limit(deck.deck_id)[0],
            unsuspended_new=_count_new_cards(deck.deck_id, suspended=False),
            suspended_new=_count_new_cards(deck.deck_id, suspended=True),
            effective_new_count=effective_new_counts.get(deck.deck_id, 0),
        )
        for deck in decks
    }
    return compute_notify_states(
        entries,
        metrics_by_deck_id,
        assignments,
        compute_schedule_health_snapshot(mw.col, config),
    )


def _count_new_cards(did: int, suspended: bool) -> int:
    queue = -1 if suspended else 0
    try:
        count = mw.col.db.scalar(
            "select count() from cards where did=? and type=0 and queue=?",
            did,
            queue,
        )
        return int(count or 0)
    except Exception:
        return 0


def _count_total_cards(did: int) -> int:
    try:
        count = mw.col.db.scalar("select count() from cards where did=?", did)
        return int(count or 0)
    except Exception:
        return 0


def _build_effective_new_count_map() -> Dict[int, int]:
    counts: Dict[int, int] = {}

    def visit(node) -> None:
        deck_id = getattr(node, "deck_id", None)
        if deck_id is not None:
            try:
                counts[int(deck_id)] = int(getattr(node, "new_count", 0) or 0)
            except Exception:
                pass
        for child in getattr(node, "children", []) or []:
            visit(child)

    try:
        tree = mw.col.sched.deck_due_tree()
    except Exception:
        return counts

    visit(tree)
    return counts


def _get_deck_config(did: int) -> dict:
    decks = mw.col.decks
    for attr in (
        "config_dict_for_deck_id",
        "config_dict_for_did",
        "deck_config_for_did",
        "config_for_did",
    ):
        fn = getattr(decks, attr, None)
        if callable(fn):
            try:
                return fn(did)
            except Exception:
                continue

    deck = decks.get(did)
    if deck:
        conf_id = deck.get("conf")
        if conf_id is not None:
            fn = getattr(decks, "get_config", None)
            if callable(fn):
                try:
                    return fn(conf_id)
                except Exception:
                    pass

    return {}


def _get_config_new_limit(did: int) -> Tuple[Optional[int], str]:
    deck = mw.col.decks.get(did) or {}
    for key in ("new_per_day", "newPerDay", "newLimit", "new_limit"):
        if key in deck:
            try:
                return int(deck[key]), "deck"
            except Exception:
                pass

    limits = deck.get("limits")
    if isinstance(limits, dict):
        for key in ("new", "perDay", "new_per_day"):
            if key in limits:
                try:
                    return int(limits[key]), "deck"
                except Exception:
                    pass

    config = _get_deck_config(did)
    per_day = config.get("new", {}).get("perDay")
    if per_day is None:
        return None, "unknown"

    try:
        return int(per_day), "config"
    except Exception:
        return None, "unknown"


def _render_badge_html(state) -> str:
    if getattr(state, "agg_status", "") == "limits":
        badge_class = "fractional-scheduler-badge fractional-scheduler-badge-limits"
    else:
        badge_class = "fractional-scheduler-badge fractional-scheduler-badge-availability"

    tooltip = escape(badge_tooltip(state), quote=True)
    aria_label = escape(badge_label(state), quote=True)
    return f'<span class="{badge_class}" title="{tooltip}" aria-label="{aria_label}">!</span>'


def _inject_badges(tree_html: str, badges_by_did: Dict[int, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        badge = badges_by_did.get(int(match.group(2)))
        if not badge:
            return match.group(1)
        return f"{match.group(1)}{badge}"

    return DECK_LINK_RE.sub(repl, tree_html)
