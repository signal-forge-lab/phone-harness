"""Generic order-demand planning over deterministic pair-merge chains.

This module is intentionally independent of any app UI. It reserves inventory
that already satisfies requested targets, expands missing targets backward
through merge transitions, and produces demand-aware priorities that an app
adapter can use for merge and producer planning.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from .merge_planner import MergeToken, MergeTransition
from .merge_planner import PlannedMerge


@dataclass(frozen=True)
class OrderDemand:
    identity: str
    level: int
    quantity: int = 1
    order_id: str | None = None


@dataclass(frozen=True)
class OrderRequest:
    order_id: str
    demands: tuple[OrderDemand, ...]


@dataclass(frozen=True)
class RequiredToken:
    identity: str
    level: int
    quantity: int
    distance_to_order: int


def _key(identity, level):
    return identity, int(level)


def _validate_demands(demands):
    values = []
    for demand in demands:
        if not isinstance(demand, OrderDemand):
            raise TypeError("demands must contain OrderDemand values")
        if not demand.identity:
            raise ValueError("demand identity must not be empty")
        if demand.level < 0 or demand.quantity <= 0:
            raise ValueError("demand level must be non-negative and quantity positive")
        values.append(demand)
    return values


def _reverse_transitions(transitions):
    reverse = {}
    forward = {}
    for transition in transitions:
        if not isinstance(transition, MergeTransition):
            raise TypeError("transitions must contain MergeTransition values")
        source = _key(transition.input_identity, transition.input_level)
        target = _key(transition.output_identity, transition.output_level)
        if target in reverse and reverse[target] != source:
            raise ValueError(f"ambiguous predecessor for merge target {target!r}")
        reverse[target] = source
        forward[source] = target
    return reverse, forward


def inventory_counts(items):
    counts = Counter()
    for item in items:
        if not isinstance(item, MergeToken):
            raise TypeError("items must contain MergeToken values")
        counts[_key(item.identity, item.level)] += 1
    return counts


def plan_completable_orders(items, requests):
    """Greedily allocate current inventory to fully completable order requests.

    Inventory allocated to an earlier request is removed before considering the
    next request so one token is never promised to two deliveries. Callers may
    order ``requests`` by their own game-specific priority before invoking this
    generic helper.
    """
    available = inventory_counts(items)
    completable = []
    consumed = Counter()
    for request in requests:
        if not isinstance(request, OrderRequest):
            raise TypeError("requests must contain OrderRequest values")
        if not request.order_id:
            raise ValueError("order_id must not be empty")
        needs = Counter()
        for demand in _validate_demands(request.demands):
            needs[_key(demand.identity, demand.level)] += demand.quantity
        if all(available[token] >= quantity for token, quantity in needs.items()):
            completable.append(request.order_id)
            for token, quantity in needs.items():
                available[token] -= quantity
                consumed[token] += quantity
    return {
        "order_ids": completable,
        "consumed": consumed,
        "remaining_inventory": available,
    }


def analyze_order_demand(items, transitions, demands):
    """Reserve fulfilled demand and expand missing targets into precursor needs.

    Existing exact target items are consumed by the virtual reservation first.
    Only the remaining deficit is expanded backward. This prevents a merge
    planner from combining away an item that is already needed for delivery.
    """
    demands = _validate_demands(demands)
    reverse, forward = _reverse_transitions(transitions)
    available = inventory_counts(items)
    requested = Counter()
    for demand in demands:
        requested[_key(demand.identity, demand.level)] += demand.quantity

    reserved = Counter()
    outstanding = Counter()
    for token, quantity in requested.items():
        used = min(quantity, available[token])
        if used:
            reserved[token] += used
            available[token] -= used
        missing = quantity - used
        if missing:
            outstanding[token] += missing

    required = Counter()
    deficit = Counter()
    distance = {}
    queue = deque((token, quantity, 0) for token, quantity in outstanding.items())
    while queue:
        token, quantity, depth = queue.popleft()
        if quantity <= 0:
            continue
        have = min(quantity, available[token])
        if have:
            available[token] -= have
            required[token] += have
            distance[token] = min(distance.get(token, depth), depth)
        missing = quantity - have
        if missing <= 0:
            continue
        required[token] += missing
        deficit[token] += missing
        distance[token] = min(distance.get(token, depth), depth)
        predecessor = reverse.get(token)
        if predecessor is not None:
            queue.append((predecessor, missing * 2, depth + 1))

    # Build a priority score for every token that lies on a route to an
    # outstanding order. Smaller distance is more directly useful.
    target_distance = {}
    frontier = deque((token, 0) for token in outstanding)
    while frontier:
        token, depth = frontier.popleft()
        previous = target_distance.get(token)
        if previous is not None and previous <= depth:
            continue
        target_distance[token] = depth
        predecessor = reverse.get(token)
        if predecessor is not None:
            frontier.append((predecessor, depth + 1))

    # A merge is useful only if its result lies on an outstanding path.
    useful_merges = {}
    for source, target in forward.items():
        if target in target_distance:
            useful_merges[source] = {
                "output": target,
                "distance_to_order": target_distance[target],
            }

    return {
        "requested": requested,
        "reserved": reserved,
        "outstanding": outstanding,
        "remaining_inventory": available,
        "required": required,
        "deficit": deficit,
        "required_tokens": [
            RequiredToken(identity=token[0], level=token[1], quantity=quantity,
                          distance_to_order=distance.get(token, 0))
            for token, quantity in sorted(required.items(), key=lambda item: (item[0][1], item[0][0]))
        ],
        "target_distance": target_distance,
        "useful_merges": useful_merges,
        "complete": not outstanding,
    }


def score_producer_output_for_orders(identity, level, order_analysis):
    """Return a generic demand score for one hypothetical producer output."""
    token = _key(identity, level)
    outstanding = order_analysis["outstanding"]
    if outstanding.get(token, 0) > 0:
        return 1000.0
    required = order_analysis["required"]
    if required.get(token, 0) > 0:
        depth = order_analysis["target_distance"].get(token, 99)
        return 500.0 / (depth + 1)
    depth = order_analysis["target_distance"].get(token)
    if depth is not None:
        return 100.0 / (depth + 1)
    return 0.0


def plan_order_merge_rounds(items, transitions, demands, *, max_merges=64):
    """Plan only merges that advance currently outstanding order demand.

    Existing exact-order items are reserved. Each round is dependency-safe and
    may be executed as one local action batch. The board is then simulated
    before the next round, so higher-level chain merges are predicted without a
    remote-agent round trip.
    """
    if isinstance(max_merges, bool) or not isinstance(max_merges, int) or max_merges < 0:
        raise ValueError("max_merges must be a non-negative integer")
    demands = _validate_demands(demands)
    _reverse, forward = _reverse_transitions(transitions)

    board = {}
    for item in items:
        if not isinstance(item, MergeToken):
            raise TypeError("items must contain MergeToken values")
        if item.cell_id in board:
            raise ValueError(f"duplicate occupied cell {item.cell_id!r}")
        board[item.cell_id] = item

    transition_by_source = {
        _key(transition.input_identity, transition.input_level): transition
        for transition in transitions
    }
    rounds = []
    total = 0
    round_index = 0
    while total < max_merges:
        analysis = analyze_order_demand(board.values(), transitions, demands)
        if analysis["complete"]:
            break

        groups = defaultdict(list)
        for cell_id, token in board.items():
            groups[_key(token.identity, token.level)].append(cell_id)

        # Reserve items already satisfying a requested exact target. The
        # remaining cells in that group may still be used for a higher order.
        available_groups = {}
        for token, cells in groups.items():
            cells = sorted(cells)
            reserved_count = int(analysis["reserved"].get(token, 0))
            available_groups[token] = cells[reserved_count:]

        planned = []
        useful_sources = analysis["useful_merges"]
        for source in sorted(useful_sources, key=lambda value: (value[1], value[0])):
            transition = transition_by_source[source]
            target = _key(transition.output_identity, transition.output_level)
            target_deficit = int(analysis["deficit"].get(target, 0))
            if target_deficit <= 0:
                continue
            cells = available_groups.get(source, [])
            pair_count = min(len(cells) // 2, target_deficit)
            for pair_index in range(pair_count):
                if total + len(planned) >= max_merges:
                    break
                source_cell = cells[pair_index * 2]
                target_cell = cells[pair_index * 2 + 1]
                planned.append(PlannedMerge(
                    round_index=round_index,
                    source_cell=source_cell,
                    target_cell=target_cell,
                    input_identity=transition.input_identity,
                    input_level=transition.input_level,
                    output_identity=transition.output_identity,
                    output_level=transition.output_level,
                ))
            if total + len(planned) >= max_merges:
                break

        if not planned:
            break

        for step in planned:
            board.pop(step.source_cell)
            board[step.target_cell] = MergeToken(
                cell_id=step.target_cell,
                identity=step.output_identity,
                level=step.output_level,
            )
        rounds.append(planned)
        total += len(planned)
        round_index += 1

    final_analysis = analyze_order_demand(board.values(), transitions, demands)
    return {
        "rounds": rounds,
        "merges": [step for planned in rounds for step in planned],
        "final_items": [board[cell] for cell in sorted(board)],
        "merge_count": total,
        "order_analysis": final_analysis,
    }

