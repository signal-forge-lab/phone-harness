"""Generic producer selection/burst planning.

The planner is game-agnostic.  A caller supplies possible producer outputs and
the same deterministic merge transitions used by ``merge_planner``.  Producer
choice is scored by how much one hypothetical output improves the reachable
merge plan, including chain merges created at higher levels.
"""

from __future__ import annotations

from dataclasses import dataclass

from .merge_planner import MergeToken, plan_merge_rounds
from .order_planner import score_producer_output_for_orders


@dataclass(frozen=True)
class ProducerOutput:
    identity: str
    level: int
    weight: float = 1.0


@dataclass(frozen=True)
class ProducerSpec:
    producer_id: str
    outputs: tuple[ProducerOutput, ...]
    outputs_complete: bool = False


@dataclass(frozen=True)
class ProducerBurst:
    producer_id: str
    emissions: int
    expected_merge_gain: float
    uncertain_output: bool
    reason: str


def _expected_gain(items, producer, transitions, baseline_merges):
    if not producer.outputs:
        return -1.0
    total_weight = sum(output.weight for output in producer.outputs)
    if total_weight <= 0:
        raise ValueError("producer output weights must sum to a positive value")

    expected = 0.0
    for index, output in enumerate(producer.outputs):
        if output.level < 0 or output.weight < 0:
            raise ValueError("producer output level/weight must be non-negative")
        hypothetical = [*items, MergeToken(
            cell_id=f"__producer_hypothetical_{index}",
            identity=output.identity,
            level=output.level,
        )]
        merges = plan_merge_rounds(hypothetical, transitions)["merge_count"]
        expected += max(0, merges - baseline_merges) * (output.weight / total_weight)
    return expected


def plan_producer_burst(
    items,
    producers,
    transitions,
    *,
    free_cells,
    energy=None,
    max_emissions=20,
    uncertain_burst_size=6,
    order_analysis=None,
):
    """Choose one producer and a local emission burst for the current board.

    Deterministic producers may consume the entire available local budget in
    one burst. Producers with multiple possible outputs are intentionally
    re-observed after a smaller burst; a surrounding local workflow may call
    this planner repeatedly without returning to a remote agent.
    """
    for name, value in {
        "free_cells": free_cells,
        "max_emissions": max_emissions,
        "uncertain_burst_size": uncertain_burst_size,
    }.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if energy is not None and (
        isinstance(energy, bool) or not isinstance(energy, int) or energy < 0
    ):
        raise ValueError("energy must be a non-negative integer or None")
    if not producers:
        return None

    capacity = min(free_cells, max_emissions)
    if energy is not None:
        capacity = min(capacity, energy)
    if capacity <= 0:
        return None

    baseline_merges = plan_merge_rounds(items, transitions)["merge_count"]
    scored = []
    for producer in producers:
        if not isinstance(producer, ProducerSpec):
            raise TypeError("producers must contain ProducerSpec values")
        if not producer.producer_id:
            raise ValueError("producer_id must not be empty")
        gain = _expected_gain(items, producer, transitions, baseline_merges)
        order_score = 0.0
        direct_deficit = 0
        if order_analysis is not None:
            for output in producer.outputs:
                weighted_score = score_producer_output_for_orders(
                    output.identity, output.level, order_analysis
                ) * output.weight
                order_score += weighted_score
                direct_deficit = max(
                    direct_deficit,
                    int(order_analysis["deficit"].get((output.identity, output.level), 0)),
                )
        # A known mergeable output is preferable when expected gains tie.
        transition_coverage = sum(
            1 for output in producer.outputs
            if any(
                transition.input_identity == output.identity
                and transition.input_level == output.level
                for transition in transitions
            )
        )
        scored.append((
            order_score,
            gain,
            transition_coverage,
            producer.producer_id,
            direct_deficit,
            producer,
        ))

    order_score, gain, _coverage, _producer_id, direct_deficit, producer = max(scored)
    uncertain = not producer.outputs_complete or len(producer.outputs) != 1
    if not uncertain and direct_deficit > 0:
        emissions = min(capacity, direct_deficit)
    else:
        emissions = min(capacity, uncertain_burst_size if uncertain else capacity)
    if order_score > 0:
        reason = "highest_order_demand_score"
    elif gain > 0:
        reason = "highest_expected_chain_merge_gain"
    else:
        reason = "no_immediate_merge_gain_best_known_output_coverage"
    return ProducerBurst(
        producer_id=producer.producer_id,
        emissions=emissions,
        expected_merge_gain=round(gain, 6),
        uncertain_output=uncertain,
        reason=reason,
    )

