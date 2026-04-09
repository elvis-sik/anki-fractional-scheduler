from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

STATUS_LIMITS = "limits"
STATUS_AVAILABILITY = "availability"
STATUS_NORMAL = "normal"

NOTIFY_MODE_DIRECT = "direct_only"
NOTIFY_MODE_ANY = "any_blocked_descendant"
NOTIFY_MODE_ALL = "all_included_descendants_blocked"
NOTIFY_MODE_HIDE = "hide_container_rows"


@dataclass(frozen=True)
class NotifyDeckEntry:
    deck_id: int
    name: str
    has_children: bool = False
    is_filtered: bool = False
    is_container: bool = False


@dataclass(frozen=True)
class NotifyDirectMetrics:
    total_cards: int
    new_limit: Optional[int]
    unsuspended_new: int
    suspended_new: int
    effective_new_count: int


@dataclass
class NotifyDeckState:
    deck_id: int
    name: str
    monitored: bool
    notify_mode: str
    is_filtered: bool
    is_container: bool
    has_children: bool
    total_cards: int
    new_limit: Optional[int]
    unsuspended_new: int
    suspended_new: int
    effective_new_count: int
    direct_status: Optional[str]
    descendant_status: Optional[str]
    agg_status: Optional[str]
    agg_unsuspended_new: int
    agg_suspended_new: int
    has_monitored_descendants: bool


def compute_notify_states(
    decks: Iterable[NotifyDeckEntry],
    metrics_by_deck_id: Dict[int, NotifyDirectMetrics],
    notify_assignments: Dict[int, dict],
    fractional_health_by_deck_id: Dict[int, object],
) -> Dict[int, NotifyDeckState]:
    by_name: Dict[str, NotifyDeckState] = {}
    deck_order = []
    subtree_monitored_counts: Dict[str, int] = {}
    subtree_problem_counts: Dict[str, int] = {}
    subtree_any_limits: Dict[str, bool] = {}
    subtree_any_availability: Dict[str, bool] = {}
    descendant_monitored_counts: Dict[str, int] = {}
    descendant_problem_counts: Dict[str, int] = {}
    descendant_any_limits: Dict[str, bool] = {}
    descendant_any_availability: Dict[str, bool] = {}

    for deck in decks:
        metrics = metrics_by_deck_id.get(deck.deck_id, NotifyDirectMetrics(0, None, 0, 0, 0))
        monitored = deck.deck_id in notify_assignments and not deck.is_filtered
        notify_mode = _notify_mode_for_assignment(notify_assignments.get(deck.deck_id))
        direct_status = None
        if monitored and not deck.is_container:
            direct_status = _compute_self_status(
                metrics.new_limit,
                metrics.unsuspended_new,
                metrics.effective_new_count,
                _has_future_positive_limit(fractional_health_by_deck_id.get(deck.deck_id)),
            )
        state = NotifyDeckState(
            deck_id=deck.deck_id,
            name=deck.name,
            monitored=monitored,
            notify_mode=notify_mode,
            is_filtered=deck.is_filtered,
            is_container=deck.is_container,
            has_children=deck.has_children,
            total_cards=metrics.total_cards,
            new_limit=metrics.new_limit,
            unsuspended_new=metrics.unsuspended_new,
            suspended_new=metrics.suspended_new,
            effective_new_count=metrics.effective_new_count,
            direct_status=direct_status,
            descendant_status=None,
            agg_status=direct_status,
            agg_unsuspended_new=metrics.unsuspended_new if monitored else 0,
            agg_suspended_new=metrics.suspended_new if monitored else 0,
            has_monitored_descendants=False,
        )
        by_name[deck.name] = state
        deck_order.append(deck.name)

        direct_problem = direct_status in {STATUS_LIMITS, STATUS_AVAILABILITY}
        subtree_monitored_counts[deck.name] = 1 if monitored and not deck.is_container else 0
        subtree_problem_counts[deck.name] = 1 if direct_problem else 0
        subtree_any_limits[deck.name] = direct_status == STATUS_LIMITS
        subtree_any_availability[deck.name] = direct_status == STATUS_AVAILABILITY
        descendant_monitored_counts[deck.name] = 0
        descendant_problem_counts[deck.name] = 0
        descendant_any_limits[deck.name] = False
        descendant_any_availability[deck.name] = False

    for name in sorted(deck_order, key=lambda item: item.count("::"), reverse=True):
        parent = _parent_name(name)
        if not parent or parent not in by_name:
            continue
        child_state = by_name[name]
        parent_state = by_name[parent]
        parent_state.agg_unsuspended_new += child_state.agg_unsuspended_new
        parent_state.agg_suspended_new += child_state.agg_suspended_new
        descendant_monitored_counts[parent] += subtree_monitored_counts[name]
        descendant_problem_counts[parent] += subtree_problem_counts[name]
        descendant_any_limits[parent] = descendant_any_limits[parent] or subtree_any_limits[name]
        descendant_any_availability[parent] = descendant_any_availability[parent] or subtree_any_availability[name]
        subtree_monitored_counts[parent] += subtree_monitored_counts[name]
        subtree_problem_counts[parent] += subtree_problem_counts[name]
        subtree_any_limits[parent] = subtree_any_limits[parent] or subtree_any_limits[name]
        subtree_any_availability[parent] = subtree_any_availability[parent] or subtree_any_availability[name]

    for name, state in by_name.items():
        if not state.monitored:
            state.agg_status = None
            state.agg_unsuspended_new = 0
            state.agg_suspended_new = 0
            continue

        state.has_monitored_descendants = descendant_monitored_counts[name] > 0
        state.descendant_status = _descendant_status_for_mode(
            state.notify_mode,
            descendant_monitored_counts[name],
            descendant_problem_counts[name],
            descendant_any_limits[name],
            descendant_any_availability[name],
        )
        state.agg_status = _aggregate_status(state.direct_status, state.descendant_status)

    return {state.deck_id: state for state in by_name.values()}


def should_show_badge(state: NotifyDeckState) -> bool:
    if not state.monitored or state.agg_status not in {STATUS_LIMITS, STATUS_AVAILABILITY}:
        return False
    if state.is_container and state.notify_mode in {NOTIFY_MODE_DIRECT, NOTIFY_MODE_HIDE}:
        return False
    return True


def badge_tooltip(state: NotifyDeckState) -> str:
    if state.agg_status == STATUS_LIMITS:
        if state.descendant_status == STATUS_LIMITS and state.direct_status != STATUS_LIMITS:
            return (
                "A monitored descendant is blocked by a 0/day limit. "
                f"Unsuspended new in subtree: {state.agg_unsuspended_new}. "
                f"Suspended new in subtree: {state.agg_suspended_new}."
            )
        return (
            "This deck is blocked by a 0/day limit. "
            f"Unsuspended new: {state.unsuspended_new}. "
            f"Suspended new: {state.suspended_new}."
            if state.direct_status == STATUS_LIMITS
            else (
                "This deck or a monitored descendant is blocked by a 0/day limit. "
                f"Unsuspended new in subtree: {state.agg_unsuspended_new}. "
                f"Suspended new in subtree: {state.agg_suspended_new}."
            )
        )
    if state.descendant_status == STATUS_AVAILABILITY and state.direct_status != STATUS_AVAILABILITY:
        return (
            "A monitored descendant has no unsuspended new cards available. "
            f"Suspended new in subtree: {state.agg_suspended_new}."
        )
    return (
        f"This deck has no unsuspended new cards available. Suspended new: {state.suspended_new}."
        if state.direct_status == STATUS_AVAILABILITY
        else (
            "This deck or a monitored descendant has no unsuspended new cards available. "
            f"Suspended new in subtree: {state.agg_suspended_new}."
        )
    )


def badge_label(state: NotifyDeckState) -> str:
    if state.agg_status == STATUS_LIMITS:
        return "0/day new-card limit"
    return "No unsuspended new cards available"


def _compute_self_status(
    new_limit: Optional[int],
    unsuspended_new: int,
    effective_new_count: int,
    has_future_positive_limit: bool,
) -> str:
    if effective_new_count > 0:
        return STATUS_NORMAL
    if unsuspended_new > 0 and has_future_positive_limit:
        return STATUS_NORMAL
    if new_limit is not None and new_limit <= 0 and unsuspended_new > 0:
        return STATUS_LIMITS
    if unsuspended_new <= 0:
        return STATUS_AVAILABILITY
    return STATUS_NORMAL


def _aggregate_status(direct_status: Optional[str], descendant_status: Optional[str]) -> Optional[str]:
    if direct_status == STATUS_LIMITS or descendant_status == STATUS_LIMITS:
        return STATUS_LIMITS
    if direct_status == STATUS_AVAILABILITY or descendant_status == STATUS_AVAILABILITY:
        return STATUS_AVAILABILITY
    return None


def _descendant_status_for_mode(
    mode: str,
    monitored_descendants: int,
    problematic_descendants: int,
    any_limits: bool,
    any_availability: bool,
) -> Optional[str]:
    if mode == NOTIFY_MODE_DIRECT or monitored_descendants <= 0:
        return None
    if mode in {NOTIFY_MODE_ANY, NOTIFY_MODE_HIDE}:
        if any_limits:
            return STATUS_LIMITS
        if any_availability:
            return STATUS_AVAILABILITY
        return None
    if problematic_descendants > 0 and monitored_descendants == problematic_descendants:
        if any_limits:
            return STATUS_LIMITS
        if any_availability:
            return STATUS_AVAILABILITY
    return None


def _notify_mode_for_assignment(assignment: Optional[dict]) -> str:
    if not isinstance(assignment, dict):
        return NOTIFY_MODE_DIRECT
    mode = str(assignment.get("notify_descendant_mode") or NOTIFY_MODE_DIRECT)
    if mode == "aggregate_children":
        mode = NOTIFY_MODE_ANY
    if mode not in {NOTIFY_MODE_DIRECT, NOTIFY_MODE_ANY, NOTIFY_MODE_ALL, NOTIFY_MODE_HIDE}:
        return NOTIFY_MODE_DIRECT
    return mode


def _has_future_positive_limit(entry: object) -> bool:
    if isinstance(entry, dict):
        return bool(entry.get("has_future_positive_limit", False))
    return bool(getattr(entry, "has_future_positive_limit", False))


def _parent_name(deck_name: str) -> Optional[str]:
    if "::" not in deck_name:
        return None
    return deck_name.rsplit("::", 1)[0]
