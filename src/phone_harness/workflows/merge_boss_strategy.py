"""Merge Boss strategy adapter over generic order/merge/producer planners.

This module contains game semantics (customers, producers, learned catalog) but
no screen coordinates, OCR, WDA calls or phone control. Perception supplies a
structured snapshot; control executes the returned strategy decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from phone_harness.merge_planner import MergeToken
from phone_harness.order_planner import (
    OrderDemand,
    OrderRequest,
    analyze_order_demand,
    plan_completable_orders,
    plan_order_merge_rounds,
)
from phone_harness.producer_planner import plan_producer_burst
from phone_harness.workflows.merge_boss_catalog import MergeBossCatalog


@dataclass(frozen=True)
class MergeBossBoardItem:
    cell_id: str
    identity: str
    level: int


@dataclass(frozen=True)
class MergeBossOrder:
    order_id: str
    items: tuple[tuple[str, int], ...]
    ready: bool = False


@dataclass(frozen=True)
class MergeBossProducer:
    cell_id: str
    identity: str
    level: int


@dataclass(frozen=True)
class MergeBossVisualMerge:
    source_cell: str
    target_cell: str


def _tokens(items):
    return [
        MergeToken(item.cell_id, item.identity, item.level)
        for item in items
    ]


def _requests(orders):
    return [
        OrderRequest(
            order.order_id,
            tuple(OrderDemand(identity, level, order_id=order.order_id)
                  for identity, level in order.items),
        )
        for order in orders
    ]


def _flatten_demands(requests):
    return [demand for request in requests for demand in request.demands]


def _forward_transition_map(transitions):
    return {
        (transition.input_identity, int(transition.input_level)): (
            transition.output_identity,
            int(transition.output_level),
        )
        for transition in transitions
    }


def _merge_distance(source, target, forward):
    """Return deterministic merge-chain distance, or None when unreachable."""
    current = source
    for distance in range(33):
        if current == target:
            return distance
        current = forward.get(current)
        if current is None:
            return None
    return None


def _rank_requests_by_board_distance(tokens, requests, transitions):
    """Rank orders by practical distance from current board inventory."""
    forward = _forward_transition_map(transitions)
    ranked = []
    for position, request in enumerate(requests):
        remaining = list(tokens)
        distances = []
        demands = sorted(request.demands, key=lambda item: item.level, reverse=True)
        for demand in demands:
            target = (demand.identity, int(demand.level))
            for _ in range(int(demand.quantity)):
                candidates = []
                for index, token in enumerate(remaining):
                    distance = _merge_distance(
                        (token.identity, int(token.level)), target, forward
                    )
                    if distance is not None:
                        candidates.append((distance, index))
                if not candidates:
                    distances.append(None)
                    continue
                distance, index = min(candidates, key=lambda value: value[0])
                distances.append(distance)
                remaining.pop(index)
        unreachable = sum(value is None for value in distances)
        known = [value for value in distances if value is not None]
        score = (
            unreachable,
            sum(known) + unreachable * 99,
            max(known, default=0) if not unreachable else 99,
            position,
        )
        ranked.append((score, request, tuple(distances)))
    ranked.sort(key=lambda value: value[0])
    return ranked


def _planning_tokens_for_request(tokens, ranked_requests, primary_order_id):
    """Protect exact items already belonging to other visible orders."""
    cells_by_key = {}
    for token in tokens:
        cells_by_key.setdefault((token.identity, int(token.level)), []).append(token.cell_id)
    for cells in cells_by_key.values():
        cells.sort()

    allocated = set()
    protected = set()
    for _score, request, _distances in ranked_requests:
        for demand in request.demands:
            key = (demand.identity, int(demand.level))
            available = [cell for cell in cells_by_key.get(key, ()) if cell not in allocated]
            for cell in available[: int(demand.quantity)]:
                allocated.add(cell)
                if request.order_id != primary_order_id:
                    protected.add(cell)
    return [token for token in tokens if token.cell_id not in protected]


def plan_merge_boss_turn(
    items,
    orders,
    producers,
    *,
    free_cells,
    energy=None,
    catalog=None,
    max_merges=16,
    max_emissions=20,
    uncertain_burst_size=6,
    visual_merge_pairs=(),
):
    """Choose the next local phase: deliver, merge, produce, or idle."""
    catalog = catalog or MergeBossCatalog.load()
    tokens = _tokens(items)
    requests = _requests(orders)

    ui_ready_order_ids = [order.order_id for order in orders if order.ready]
    if ui_ready_order_ids:
        return {
            "kind": "deliver",
            "order_ids": ui_ready_order_ids,
            "reason": "order_ui_ready",
        }

    deliveries = plan_completable_orders(tokens, requests)
    if deliveries["order_ids"]:
        return {
            "kind": "deliver",
            "order_ids": deliveries["order_ids"],
            "reason": "orders_already_complete",
        }

    # Board capacity is a first-class resource. Confirmed duplicate items are
    # compacted even when they do not advance a current customer order. A later
    # low-level request can be rebuilt from fresh producer output; keeping
    # duplicates around "just in case" makes a full board unable to spend
    # energy at all.
    if max_merges > 0 and visual_merge_pairs:
        visual_merges = [
            MergeBossVisualMerge(source, target)
            for source, target in tuple(visual_merge_pairs)[:max_merges]
        ]
        if visual_merges:
            return {
                "kind": "merge",
                "rounds": [visual_merges],
                "merges": visual_merges,
                "reason": "strict_visual_compaction",
            }

    all_demands = _flatten_demands(requests)
    if not all_demands:
        # The current play objective may intentionally be to spend safely
        # available game energy even while the customer strip is temporarily
        # unreadable/empty.  If a visible producer is already known in the
        # catalog, use a bounded exploration burst instead of idling.  This is
        # still capacity-limited and never authorizes an unknown producer.
        if free_cells > 0 and max_emissions > 0:
            known = []
            for producer in producers:
                entry = catalog.producer(producer.identity, producer.level)
                if entry is None or not entry.spec.outputs:
                    continue
                known.append((producer.cell_id, entry.spec))
            if known:
                producer_cell, spec = min(known, key=lambda item: item[0])
                emissions = min(
                    int(max_emissions),
                    int(uncertain_burst_size),
                    int(free_cells),
                )
                if energy is not None:
                    emissions = min(emissions, max(0, int(energy)))
                if emissions > 0:
                    return {
                        "kind": "produce",
                        "producer_id": spec.producer_id,
                        "producer_cell": producer_cell,
                        "emissions": emissions,
                        "uncertain_output": not bool(spec.outputs_complete),
                        "expected_merge_gain": 0,
                        "reason": "bounded_energy_spend_without_visible_orders",
                        "exploration": True,
                    }
        return {"kind": "idle", "reason": "no_visible_or_known_orders"}

    ranked_requests = _rank_requests_by_board_distance(tokens, requests, catalog.transitions)
    for score, request, distances in ranked_requests:
        planning_tokens = _planning_tokens_for_request(tokens, ranked_requests, request.order_id)
        merge_plan = plan_order_merge_rounds(
            planning_tokens,
            catalog.transitions,
            request.demands,
            max_merges=max_merges,
        )
        if merge_plan["merge_count"]:
            return {
                "kind": "merge",
                "rounds": merge_plan["rounds"],
                "merges": merge_plan["merges"],
                "reason": "advance_order_demand",
                "priority_order_id": request.order_id,
                "priority_distance": list(distances),
                "priority_score": list(score[:3]),
                "order_analysis": merge_plan["order_analysis"],
            }

    # High-level order replacement is deliberately a human-reviewed policy,
    # not an automatic fixed threshold. Level 9/10-heavy screens are an
    # explicit operator-provided example of when the rebuild cost may be
    # unreasonable. Only raise the review after currently useful merges have
    # been exhausted; the controller can then ask whether to refresh one of
    # the worst visible customers.
    requested_levels = [
        int(demand.level)
        for request in requests
        for demand in request.demands
        for _ in range(int(demand.quantity))
    ]
    if requested_levels and min(requested_levels) >= 9 and ranked_requests:
        worst_score, worst_request, worst_distances = ranked_requests[-1]
        return {
            "kind": "review_refresh",
            "order_id": worst_request.order_id,
            "reason": "all_visible_orders_extremely_high_level",
            "requested_levels": requested_levels,
            "priority_distance": list(worst_distances),
            "priority_score": list(worst_score[:3]),
        }

    # A completely full board is a recovery condition, never a valid idle
    # outcome. Perception has already exhausted normal + full-resolution jam
    # merge confirmation before reaching the planner. If no safe merge or
    # delivery exists, ask whether to rotate the least attractive visible
    # customer and immediately re-scan; a replacement order may match existing
    # inventory and free cells without requiring production space.
    if free_cells <= 0 and ranked_requests:
        worst_score, worst_request, worst_distances = ranked_requests[-1]
        return {
            "kind": "review_refresh",
            "order_id": worst_request.order_id,
            "reason": "board_full_no_merge_candidate",
            "requested_levels": requested_levels,
            "priority_distance": list(worst_distances),
            "priority_score": list(worst_score[:3]),
        }

    producer_specs = []
    producer_cell_by_id = {}
    seen_producer_ids = set()
    for producer in producers:
        entry = catalog.producer(producer.identity, producer.level)
        if entry is None or not entry.spec.outputs:
            continue
        producer_id = entry.spec.producer_id
        if producer_id not in seen_producer_ids:
            producer_specs.append(entry.spec)
            seen_producer_ids.add(producer_id)
        current_cell = producer_cell_by_id.get(producer_id)
        if current_cell is None or producer.cell_id < current_cell:
            producer_cell_by_id[producer_id] = producer.cell_id

    fallback_burst = None
    fallback_context = None
    for score, request, distances in ranked_requests:
        planning_tokens = _planning_tokens_for_request(tokens, ranked_requests, request.order_id)
        analysis = analyze_order_demand(planning_tokens, catalog.transitions, request.demands)
        burst = plan_producer_burst(
            planning_tokens,
            producer_specs,
            catalog.transitions,
            free_cells=free_cells,
            energy=energy,
            max_emissions=max_emissions,
            uncertain_burst_size=uncertain_burst_size,
            order_analysis=analysis,
        )
        if burst is None:
            continue
        if burst.reason == "highest_order_demand_score":
            return {
                "kind": "produce",
                "producer_id": burst.producer_id,
                "producer_cell": producer_cell_by_id[burst.producer_id],
                "emissions": burst.emissions,
                "uncertain_output": burst.uncertain_output,
                "expected_merge_gain": burst.expected_merge_gain,
                "reason": burst.reason,
                "priority_order_id": request.order_id,
                "priority_distance": list(distances),
                "priority_score": list(score[:3]),
                "order_analysis": analysis,
            }

        # Merge Boss inventory is not disposable: an output that does not help
        # the currently preferred order can still satisfy a later customer.
        # Keep the first safe bounded fallback while checking whether another
        # visible order has a directly useful producer. If none does, producing
        # and re-observing is preferable to idling, especially while producer
        # output knowledge is still incomplete.
        if fallback_burst is None:
            fallback_burst = burst
            fallback_context = (score, request, distances, analysis)

    if fallback_burst is not None and fallback_context is not None:
        score, request, distances, analysis = fallback_context
        return {
            "kind": "produce",
            "producer_id": fallback_burst.producer_id,
            "producer_cell": producer_cell_by_id[fallback_burst.producer_id],
            "emissions": fallback_burst.emissions,
            "uncertain_output": fallback_burst.uncertain_output,
            "expected_merge_gain": fallback_burst.expected_merge_gain,
            "reason": fallback_burst.reason,
            "exploration": True,
            "priority_order_id": request.order_id,
            "priority_distance": list(distances),
            "priority_score": list(score[:3]),
            "order_analysis": analysis,
        }

    analysis = analyze_order_demand(tokens, catalog.transitions, all_demands)
    return {
        "kind": "idle",
        "reason": "no_known_producer_can_advance_orders",
        "order_analysis": analysis,
    }

