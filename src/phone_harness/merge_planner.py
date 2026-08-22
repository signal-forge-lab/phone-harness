"""Generic pair-merge planning primitives.

This module knows nothing about Merge Boss, producers, screen coordinates, or
phone control.  It only models a common mechanic: two equal tokens combine into
one deterministic result token.  Callers provide the transition table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MergeToken:
    cell_id: str
    identity: str
    level: int


@dataclass(frozen=True)
class MergeTransition:
    input_identity: str
    input_level: int
    output_identity: str
    output_level: int


@dataclass(frozen=True)
class PlannedMerge:
    round_index: int
    source_cell: str
    target_cell: str
    input_identity: str
    input_level: int
    output_identity: str
    output_level: int


def _transition_map(transitions):
    mapping = {}
    for transition in transitions:
        if not isinstance(transition, MergeTransition):
            raise TypeError("transitions must contain MergeTransition values")
        key = (transition.input_identity, transition.input_level)
        if key in mapping:
            raise ValueError(f"duplicate merge transition for {key!r}")
        if transition.input_level < 0 or transition.output_level < 0:
            raise ValueError("merge levels must be non-negative")
        mapping[key] = transition
    return mapping


def plan_merge_rounds(items, transitions, *, max_merges=64):
    """Plan every deterministic merge, grouped into dependency-safe rounds.

    All merges in one round use only tokens that existed at the beginning of
    that round, so a caller may execute the round as one input batch.  Results
    are then simulated before planning the next round, which naturally predicts
    chain merges created by earlier merges.
    """
    if isinstance(max_merges, bool) or not isinstance(max_merges, int) or max_merges < 0:
        raise ValueError("max_merges must be a non-negative integer")

    transition_by_key = _transition_map(transitions)
    board = {}
    for item in items:
        if not isinstance(item, MergeToken):
            raise TypeError("items must contain MergeToken values")
        if not item.cell_id:
            raise ValueError("cell_id must not be empty")
        if item.cell_id in board:
            raise ValueError(f"duplicate occupied cell {item.cell_id!r}")
        if item.level < 0:
            raise ValueError("item levels must be non-negative")
        board[item.cell_id] = item

    rounds = []
    total = 0
    round_index = 0
    while total < max_merges:
        groups = {}
        for cell_id, token in board.items():
            groups.setdefault((token.identity, token.level), []).append(cell_id)

        planned = []
        for key in sorted(groups, key=lambda value: (value[1], value[0])):
            transition = transition_by_key.get(key)
            if transition is None:
                continue
            cells = sorted(groups[key])
            for offset in range(0, len(cells) - 1, 2):
                if total + len(planned) >= max_merges:
                    break
                source = cells[offset]
                target = cells[offset + 1]
                planned.append(PlannedMerge(
                    round_index=round_index,
                    source_cell=source,
                    target_cell=target,
                    input_identity=transition.input_identity,
                    input_level=transition.input_level,
                    output_identity=transition.output_identity,
                    output_level=transition.output_level,
                ))
            if total + len(planned) >= max_merges:
                break

        if not planned:
            break

        # Apply the complete round only after planning it, preserving the
        # dependency-safe property for callers that batch all drags together.
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

    return {
        "rounds": rounds,
        "merges": [step for planned in rounds for step in planned],
        "final_items": [board[cell] for cell in sorted(board)],
        "merge_count": total,
    }

