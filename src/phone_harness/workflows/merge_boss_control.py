"""Local multi-action Merge Boss control loop.

Strategy and generic planning stay outside this module. A perception adapter
supplies structured game state plus current screen coordinates; this controller
turns strategy decisions into bounded PhoneRuntime batches and immediately
re-plans locally without returning to ChatGPT between small actions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from phone_harness.workflows.merge_boss_strategy import plan_merge_boss_turn


@dataclass(frozen=True)
class MergeBossBindings:
    observation_id: int
    cell_centers: dict[str, tuple[float, float]]
    complete_centers: dict[str, tuple[float, float]] = field(default_factory=dict)
    refresh_centers: dict[str, tuple[float, float]] = field(default_factory=dict)
    selected_producer_cell: str | None = None
    order_page_indices: dict[str, int] = field(default_factory=dict)
    order_scan_final_page: int | None = None
    order_scroll_forward_action: dict | None = None
    order_scroll_backward_action: dict | None = None
    preview_path: str | None = None


@dataclass(frozen=True)
class MergeBossSnapshot:
    items: tuple
    orders: tuple
    producers: tuple
    free_cells: int
    energy: int | None
    bindings: MergeBossBindings
    visual_merge_pairs: tuple[tuple[str, str], ...] = ()


class MergeBossTurnController:
    """Execute a bounded order-aware local turn using an injected perception."""

    def __init__(self, runtime, perception, *, catalog=None):
        self.runtime = runtime
        self.perception = perception
        self.catalog = catalog

    def _pause_checkpoint(self, boundary):
        if hasattr(self.runtime, "pause_checkpoint"):
            return self.runtime.pause_checkpoint(boundary=boundary)
        waited = (
            self.runtime.wait_if_paused(boundary=boundary)
            if hasattr(self.runtime, "wait_if_paused")
            else 0.0
        )
        return {"wait_ms": waited, "replan_required": waited > 0}

    def _trace_decision(self, cycle_index, decision, snapshot, *, duration_ms=None):
        trace = getattr(self.runtime, "trace", None)
        if trace is None:
            return
        kind = decision.get("kind", "unknown")
        reason = decision.get("reason", "")
        data = {
            "workflow": "merge_boss_turn",
            "cycle": cycle_index,
            "decision_kind": kind,
            "reason": reason,
            "free_cells": snapshot.free_cells,
            "energy": snapshot.energy,
            "visible_orders": len(snapshot.orders),
            "producer_count": len(snapshot.producers),
            "visual_merge_pair_count": len(snapshot.visual_merge_pairs),
        }
        if duration_ms is not None:
            data["duration_ms"] = round(float(duration_ms), 3)
        for key in ("priority_order_id", "priority_distance", "priority_score"):
            if decision.get(key) is not None:
                data[key] = decision[key]
        if kind == "merge":
            data["merge_count"] = len(decision.get("merges") or ())
            data["merge_steps"] = [
                [step.source_cell, step.target_cell]
                for step in (decision.get("merges") or ())
            ]
        elif kind == "produce":
            data.update({
                "producer_id": decision.get("producer_id"),
                "producer_cell": decision.get("producer_cell"),
                "emissions": decision.get("emissions"),
                "uncertain_output": decision.get("uncertain_output"),
                "expected_merge_gain": decision.get("expected_merge_gain"),
            })
        elif kind == "deliver":
            data["order_ids"] = list(decision.get("order_ids") or ())
        trace.emit(
            "workflow.decision",
            summary=f"Merge Boss: {kind} ({reason})",
            phase="decision",
            status="selected",
            data=data,
        )

    def _trace_failure(
        self,
        cycle_index,
        *,
        reason,
        phase,
        expected=None,
        actual=None,
        next_action=None,
    ):
        trace = getattr(self.runtime, "trace", None)
        if trace is None:
            return
        trace.emit(
            "failure.judgment",
            summary=f"Merge Boss failure: {reason}",
            phase=phase,
            status="failed",
            data={
                "workflow": "merge_boss_turn",
                "cycle": cycle_index,
                "reason": reason,
                "expected": expected,
                "actual": actual,
                "next_action": next_action,
            },
        )

    @staticmethod
    def _item_cells(snapshot):
        return {
            item.cell_id
            for item in snapshot.items
            if isinstance(getattr(item, "cell_id", None), str)
        }

    @staticmethod
    def _items_by_cell(snapshot):
        return {
            item.cell_id: item
            for item in snapshot.items
            if isinstance(getattr(item, "cell_id", None), str)
        }

    def _confirm_production(self, pending, snapshot):
        before_cells = pending["occupied_cells"]
        after_cells = self._item_cells(snapshot)
        new_cells = sorted(after_cells - before_cells)
        free_cell_delta = max(0, int(pending["free_cells"]) - int(snapshot.free_cells))
        confirmed = min(
            int(pending["attempts"]),
            len(new_cells),
            free_cell_delta,
        )
        trace = getattr(self.runtime, "trace", None)
        if trace is not None:
            trace.emit(
                "merge_boss.production.confirm",
                summary=f"Producer output confirmed: {confirmed}/{pending['attempts']}",
                phase="analysis",
                status="confirmed" if confirmed else "unchanged",
                data={
                    "workflow": "merge_boss_turn",
                    "source_cycle": pending["cycle"],
                    "attempted_emissions": pending["attempts"],
                    "confirmed_emissions": confirmed,
                    "new_item_cells": new_cells,
                    "free_cell_delta": free_cell_delta,
                    "observation_id": snapshot.bindings.observation_id,
                },
            )
        return confirmed

    @staticmethod
    def _bubble_candidate_from_pending_merge(pending, snapshot):
        """Return a strong causal bubble candidate after a just-executed merge.

        Human Teaching established that a successful merge can leave the normal
        successor in the destination cell and spawn a second copy in a nearby
        bubble.  When that happens board capacity does not gain the full number
        of cells expected from the merge, and the successor/bubble pair may be
        ranked as another visual merge candidate.  Prefer this causal evidence
        over a global pink/halo heuristic so ordinary pink items are not
        misclassified as bubbles.

        A pair that is simply the original source+target is deliberately left
        ambiguous: that can also mean the drag itself was a no-op.  Tapping an
        unrelated nearby duplicate is low-risk and is the strong case handled
        here; source-cell bubble placement remains a future visual/temporal
        learning case.
        """
        steps = tuple(pending.get("steps") or ())
        if not steps:
            return None
        expected_free_gain = len(steps)
        actual_free_gain = int(snapshot.free_cells) - int(pending["free_cells"])
        if actual_free_gain >= expected_free_gain:
            return None
        before_cells_value = pending.get("occupied_cells")
        before_cells = set(before_cells_value or ())
        after_cells = MergeBossTurnController._item_cells(snapshot)
        new_cells = after_cells - before_cells
        pairs = tuple(snapshot.visual_merge_pairs or ())
        for source, target in steps:
            for left, right in pairs:
                if target == left:
                    other = right
                elif target == right:
                    other = left
                else:
                    continue
                if other == source:
                    continue
                # Once we have a before-board occupancy snapshot, an automatic
                # bubble preemption requires a genuinely new occupied cell.
                # The previous weaker rule accepted a recurring visual pair
                # when new_cells was empty and live two-tap evidence showed that
                # could be an ordinary/no-op pair rather than a bubble.
                if before_cells_value is not None and other not in new_cells:
                    continue
                return {
                    "cell": other,
                    "source_cell": source,
                    "target_cell": target,
                    "expected_free_gain": expected_free_gain,
                    "actual_free_gain": actual_free_gain,
                    "new_cells": sorted(new_cells),
                }
        return None

    @staticmethod
    def _merge_noop_evidence(pending, snapshot):
        """Detect a strong same-state drag/no-op without overusing free-cell count."""
        before = pending.get("item_states") or {}
        if not before:
            return None
        after = MergeBossTurnController._items_by_cell(snapshot)
        unchanged = []
        for source, target in tuple(pending.get("steps") or ()):
            source_before = before.get(source)
            target_before = before.get(target)
            source_after = after.get(source)
            target_after = after.get(target)
            if source_before is None or target_before is None:
                continue
            source_now = None if source_after is None else (
                getattr(source_after, "identity", None), getattr(source_after, "level", None)
            )
            target_now = None if target_after is None else (
                getattr(target_after, "identity", None), getattr(target_after, "level", None)
            )
            if source_now == tuple(source_before) and target_now == tuple(target_before):
                unchanged.append((source, target))
        if not unchanged:
            return None
        return {"pairs": tuple(unchanged)}

    @staticmethod
    def _merge_spawn_evidence(pending, snapshot):
        before_cells = set(pending.get("occupied_cells") or ())
        after_cells = MergeBossTurnController._item_cells(snapshot)
        new_cells = sorted(after_cells - before_cells)
        if not new_cells:
            return None
        by_cell = MergeBossTurnController._items_by_cell(snapshot)
        return {
            "new_cells": new_cells,
            "new_items": [
                {
                    "cell": cell_id,
                    "identity": getattr(by_cell.get(cell_id), "identity", None),
                    "level": getattr(by_cell.get(cell_id), "level", None),
                }
                for cell_id in new_cells
            ],
        }

    @staticmethod
    def _merge_outcome_anomaly(pending, snapshot, *, spawn_evidence=None):
        steps = tuple(pending.get("steps") or ())
        if not steps:
            return None
        expected_free_gain = len(steps)
        actual_free_gain = int(snapshot.free_cells) - int(pending["free_cells"])
        if actual_free_gain >= expected_free_gain:
            return None
        # Merge Boss can create a second board object during a successful merge
        # (known examples: a time-limited bubble duplicate or a random green
        # cash item). If enough genuinely-new occupied cells explain the missing
        # free-space gain, this is a spawn event rather than a merge failure.
        missing_gain = expected_free_gain - actual_free_gain
        new_cells = tuple((spawn_evidence or {}).get("new_cells") or ())
        if len(new_cells) >= missing_gain:
            return None
        return {
            "expected_free_gain": expected_free_gain,
            "actual_free_gain": actual_free_gain,
            "steps": steps,
            "new_cells": list(new_cells),
        }

    @staticmethod
    def _center(bindings, cell_id):
        try:
            return bindings.cell_centers[cell_id]
        except KeyError as exc:
            raise RuntimeError(f"missing center for Merge Boss cell {cell_id}") from exc

    def _merge_actions(self, decision, bindings):
        actions = []
        for step in decision["merges"]:
            source = self._center(bindings, step.source_cell)
            target = self._center(bindings, step.target_cell)
            actions.append({
                "op": "drag",
                "x1": source[0],
                "y1": source[1],
                "x2": target[0],
                "y2": target[1],
                "duration": 0.25,
            })
        return actions

    def _delivery_actions(self, decision, bindings):
        if (
            bindings.order_scan_final_page is not None
            and bindings.order_page_indices
            and (
                bindings.order_scroll_forward_action is not None
                or bindings.order_scroll_backward_action is not None
            )
        ):
            actions = []
            current_page = bindings.order_scan_final_page
            order_ids = sorted(
                decision["order_ids"],
                key=lambda order_id: bindings.order_page_indices.get(order_id, current_page),
                reverse=True,
            )
            for order_id in order_ids:
                target_page = bindings.order_page_indices.get(order_id)
                center = bindings.complete_centers.get(order_id)
                if target_page is None or center is None:
                    continue
                while current_page > target_page:
                    action = bindings.order_scroll_backward_action
                    if action is None:
                        break
                    actions.append(dict(action))
                    current_page -= 1
                while current_page < target_page:
                    action = bindings.order_scroll_forward_action
                    if action is None:
                        break
                    actions.append(dict(action))
                    current_page += 1
                if current_page == target_page:
                    actions.append({"op": "tap", "x": center[0], "y": center[1]})
            return actions

        actions = []
        for order_id in decision["order_ids"]:
            center = bindings.complete_centers.get(order_id)
            if center is None:
                # Do not guess a button coordinate. The next local perception
                # cycle can rediscover it without involving the remote agent.
                continue
            actions.append({"op": "tap", "x": center[0], "y": center[1]})
        return actions

    def _production_actions(self, decision, bindings):
        center = self._center(bindings, decision["producer_cell"])
        # Unknown selection state should not add an optimistic extra tap: if
        # the producer was already selected that would emit one item beyond
        # the planner's free-cell budget. Under-emitting by at most one item is
        # cheap; the next board observation replans from truth.
        selection_taps = 0
        if bindings.selected_producer_cell is not None:
            selection_taps = (
                0 if bindings.selected_producer_cell == decision["producer_cell"] else 1
            )
        return [
            {"op": "tap", "x": center[0], "y": center[1]}
            for _ in range(selection_taps + decision["emissions"])
        ]

    def _refresh_order_actions(self, decision, bindings):
        order_id = decision["order_id"]
        center = bindings.refresh_centers.get(order_id)
        if center is None:
            return []
        target_page = bindings.order_page_indices.get(order_id)
        current_page = bindings.order_scan_final_page
        actions = []
        if target_page is not None and current_page is not None:
            while current_page > target_page:
                action = bindings.order_scroll_backward_action
                if action is None:
                    return []
                actions.append(dict(action))
                current_page -= 1
            while current_page < target_page:
                action = bindings.order_scroll_forward_action
                if action is None:
                    return []
                actions.append(dict(action))
                current_page += 1
        actions.append({"op": "tap", "x": center[0], "y": center[1]})
        return actions

    def _review_refresh_with_operator(self, decision):
        if not hasattr(self.runtime, "post_operator_question") and not hasattr(self.runtime, "ask_operator"):
            return {
                "answered": False,
                "answer": None,
                "choice": None,
                "skipped": True,
                "reason": "operator_unavailable",
            }
        levels = decision.get("requested_levels") or []
        distances = decision.get("priority_distance") or []
        if decision.get("reason") == "board_full_no_merge_candidate":
            question = (
                "盤面が満杯で、高精度の全ペア確認でも安全なマージを確定できませんでした。"
                "この注文者を更新して再探索しますか？"
            )
            context = (
                f"requested_levels={levels}; estimated_distances={distances}; "
                "満杯は停止条件ではありません。更新後は注文・納品可能性・マージ候補を再スキャンします。"
            )
            confidence = 0.65
        else:
            question = "高レベル注文が偏っています。この注文者を更新しますか？"
            context = (
                f"requested_levels={levels}; estimated_distances={distances}; "
                "更新後も要求が遠い場合は再度更新できます。"
            )
            confidence = 0.75
        if not hasattr(self.runtime, "post_operator_question"):
            return self.runtime.ask_operator(
                question,
                choices=("更新する", "今回は維持"),
                context=context,
                confidence=confidence,
                impact="medium",
                timeout=30.0,
            )
        dedupe_key = f"merge-boss.refresh-review:{decision.get('order_id')}:{decision.get('reason')}"
        broker = getattr(self.runtime, "operator_broker", None)
        if broker is not None and hasattr(broker, "list"):
            for record in reversed(broker.list(limit=500)):
                if record.get("dedupe_key") != dedupe_key:
                    continue
                if record.get("status") in {"answered", "consumed"}:
                    return {
                        "answered": True,
                        "answer": record.get("answer"),
                        "choice": record.get("choice"),
                        "skipped": False,
                        "question_id": record.get("question_id"),
                    }
                return {
                    "answered": False,
                    "answer": None,
                    "choice": None,
                    "skipped": False,
                    "reason": "operator_pending",
                    "question_id": record.get("question_id"),
                }
        posted = self.runtime.post_operator_question(
            question,
            choices=("更新する", "今回は維持"),
            context=context,
            confidence=confidence,
            impact="medium",
            promote_answer_to_teaching=True,
            dedupe_key=dedupe_key,
        )
        return {
            "answered": False,
            "answer": None,
            "choice": None,
            "skipped": False,
            "reason": "operator_pending",
            "question_id": posted.get("question_id"),
        }

    def run(
        self,
        *,
        max_cycles=8,
        max_merges=16,
        max_emissions=20,
        uncertain_burst_size=6,
        max_recoveries=3,
        order_rescan_every=3,
    ):
        for name, value, upper in (
            ("max_cycles", max_cycles, 32),
            ("max_merges", max_merges, 64),
            ("max_emissions", max_emissions, 40),
            ("uncertain_burst_size", uncertain_burst_size, 20),
            ("max_recoveries", max_recoveries, 10),
            ("order_rescan_every", order_rescan_every, 16),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
                raise ValueError(f"{name} must be an integer between 0 and {upper}")

        started = time.perf_counter()
        total_merges = 0
        total_emissions = 0
        total_emission_attempts = 0
        unconfirmed_emission_attempts = 0
        pending_production = None
        pending_merge = None
        sticky_producer_id = None
        sticky_producer_cell = None
        deliveries = 0
        order_refreshes = 0
        order_refresh_review = None
        recoveries = 0
        bubble_preemptions = 0
        merge_outcome_questions = 0
        suppressed_merge_pairs = set()
        pause_replans = 0
        pause_wait_ms = 0.0
        cycles = []
        paused_teaching = None

        cycle_index = 0
        while cycle_index < max_cycles:
            pause = self._pause_checkpoint("merge_boss_turn.cycle")
            pause_wait_ms += pause["wait_ms"]
            if pause["replan_required"]:
                pause_replans += 1
                if pending_production is not None:
                    unconfirmed_emission_attempts += pending_production["attempts"]
                    pending_production = None
                pending_merge = None
                sticky_producer_id = None
                sticky_producer_cell = None
                if hasattr(self.perception, "invalidate_order_cache"):
                    self.perception.invalidate_order_cache()
            if hasattr(self.runtime, "poll_operator_answers"):
                self.runtime.poll_operator_answers(limit=20)
            pending_teaching = (
                self.runtime.pending_human_teaching()
                if hasattr(self.runtime, "pending_human_teaching")
                else None
            )
            if pending_teaching is not None:
                paused_teaching = pending_teaching
                trace = getattr(self.runtime, "trace", None)
                if trace is not None:
                    trace.emit(
                        "workflow.paused",
                        summary="Merge Boss paused for Human Teaching",
                        phase="human_input",
                        status="waiting",
                        data={
                            "workflow": "merge_boss_turn",
                            "cycle": cycle_index,
                            **pending_teaching,
                        },
                )
                break
            try:
                snapshot = self.perception.snapshot(
                    # Cycle zero may already have a valid cache from a previous
                    # workflow call in the same long-lived PhoneRuntime. The
                    # perception object scans automatically if its cache is empty.
                    rescan_orders=(
                        cycle_index > 0
                        and cycle_index % max(1, order_rescan_every) == 0
                    ),
                )
            except RuntimeError as exc:
                if getattr(exc, "code", None) == "PAUSE_REPLAN_REQUIRED":
                    pause_replans += 1
                    if hasattr(self.perception, "invalidate_order_cache"):
                        self.perception.invalidate_order_cache()
                    continue
                self._trace_failure(
                    cycle_index,
                    reason="perception_failed",
                    phase="perception",
                    expected="fresh order-strip and board snapshot",
                    actual=str(exc),
                    next_action="request Human Teaching or refresh learned visual knowledge",
                )
                cycles.append({
                    "cycle": cycle_index,
                    "failed": True,
                    "phase": "perception",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                break
            if pending_production is not None:
                confirmed = self._confirm_production(pending_production, snapshot)
                total_emissions += confirmed
                unconfirmed_emission_attempts += (
                    pending_production["attempts"] - confirmed
                )
                pending_production = None
            if pending_merge is not None:
                spawn_evidence = self._merge_spawn_evidence(
                    pending_merge,
                    snapshot,
                )
                bubble_candidate = self._bubble_candidate_from_pending_merge(
                    pending_merge,
                    snapshot,
                )
                merge_anomaly = self._merge_outcome_anomaly(
                    pending_merge,
                    snapshot,
                    spawn_evidence=spawn_evidence,
                )
                if (
                    merge_anomaly is not None
                    and hasattr(self.perception, "record_merge_anomaly")
                ):
                    for pair in self.perception.record_merge_anomaly(
                        pending_merge,
                        snapshot,
                        merge_anomaly,
                        spawn_evidence=spawn_evidence,
                    ):
                        suppressed_merge_pairs.add(tuple(sorted(pair)))
                noop_evidence = self._merge_noop_evidence(pending_merge, snapshot)
                if noop_evidence is not None:
                    for source, target in noop_evidence["pairs"]:
                        suppressed_merge_pairs.add(tuple(sorted((source, target))))
                    trace = getattr(self.runtime, "trace", None)
                    if trace is not None:
                        trace.emit(
                            "merge_boss.merge.noop",
                            summary="Merge drag left source/target state unchanged",
                            phase="analysis",
                            status="observed",
                            data={
                                "workflow": "merge_boss_turn",
                                "cycle": cycle_index,
                                "pairs": [list(pair) for pair in noop_evidence["pairs"]],
                                "suppressed_for_current_burst": True,
                            },
                            preview_path=snapshot.bindings.preview_path,
                        )
                if spawn_evidence is not None:
                    trace = getattr(self.runtime, "trace", None)
                    if trace is not None:
                        trace.emit(
                            "merge_boss.merge.spawn",
                            summary="Merge created additional board object(s)",
                            phase="analysis",
                            status="observed",
                            data={
                                "workflow": "merge_boss_turn",
                                "cycle": cycle_index,
                                "steps": list(pending_merge.get("steps") or ()),
                                "expected_free_gain": len(tuple(pending_merge.get("steps") or ())),
                                "actual_free_gain": int(snapshot.free_cells) - int(pending_merge["free_cells"]),
                                **spawn_evidence,
                            },
                            preview_path=snapshot.bindings.preview_path,
                        )
                pending_merge = None
                if bubble_candidate is not None:
                    center = self._center(snapshot.bindings, bubble_candidate["cell"])
                    trace = getattr(self.runtime, "trace", None)
                    if trace is not None:
                        trace.emit(
                            "merge_boss.bubble.candidate",
                            summary="Probable merge-created bubble candidate before double-tap",
                            phase="analysis",
                            status="candidate",
                            data={
                                "workflow": "merge_boss_turn",
                                "cycle": cycle_index,
                                **bubble_candidate,
                                "action": "double_tap_candidate",
                                "human_review_recommended": True,
                            },
                            preview_path=snapshot.bindings.preview_path,
                        )
                    if hasattr(self.runtime, "post_operator_question"):
                        self.runtime.post_operator_question(
                            "この要確認アイテムをバブル候補として自動処理します。"
                            "あとから画像を確認して、実際にバブルだったか教えてください。",
                            choices=("バブルだった", "通常アイテムだった", "紙幣/別の生成物だった", "判断できない"),
                            context=(
                                f"candidate_cell={bubble_candidate['cell']}; "
                                f"source_cell={bubble_candidate['source_cell']}; "
                                f"target_cell={bubble_candidate['target_cell']}; "
                                f"expected_free_gain={bubble_candidate['expected_free_gain']}; "
                                f"actual_free_gain={bubble_candidate['actual_free_gain']}. "
                                "回答待ちでプレイは停止しません。"
                            ),
                            confidence=0.7,
                            impact="medium",
                            preview_path=snapshot.bindings.preview_path,
                            promote_answer_to_teaching=True,
                            dedupe_key=(
                                "merge-boss.bubble-review:"
                                f"{snapshot.bindings.observation_id}:"
                                f"{bubble_candidate['cell']}"
                            ),
                        )
                    try:
                        result = self.runtime.act(
                            [
                                {"op": "tap", "x": center[0], "y": center[1]},
                                {"op": "tap", "x": center[0], "y": center[1]},
                            ],
                            observation_id=snapshot.bindings.observation_id,
                        )
                    except RuntimeError as exc:
                        self._trace_failure(
                            cycle_index,
                            reason="bubble_preemption_failed",
                            phase="execute",
                            expected="tap probable time-limited bubble before ordinary play",
                            actual=f"{type(exc).__name__}: {exc}",
                            next_action="re-observe before any further board action",
                        )
                        cycles.append({
                            "cycle": cycle_index,
                            "decision_kind": "bubble_preempt",
                            "action_count": 2,
                            "failed": True,
                        })
                        break
                    bubble_preemptions += 1
                    if trace is not None:
                        trace.emit(
                            "merge_boss.bubble.preempt",
                            summary="Probable merge-created bubble preempted normal play",
                            phase="decision",
                            status="selected",
                            data={
                                "workflow": "merge_boss_turn",
                                "cycle": cycle_index,
                                **bubble_candidate,
                                "observation_id": snapshot.bindings.observation_id,
                            },
                            preview_path=snapshot.bindings.preview_path,
                        )
                    cycles.append({
                        "cycle": cycle_index,
                        "decision_kind": "bubble_preempt",
                        "action_count": 2,
                        "duration_ms": result.get("duration_ms"),
                        "bubble_cell": bubble_candidate["cell"],
                    })
                    # Bubble taps normally leave the board for a timed reward/ad
                    # flow.  Do not feed that non-board screen back into the board
                    # reader; return control so the caller can handle/learn the
                    # transition and resume from a fresh board afterwards.
                    break
                if (
                    merge_anomaly is not None
                    and merge_outcome_questions == 0
                    and (
                        hasattr(self.runtime, "post_operator_question")
                        or hasattr(self.runtime, "ask_operator")
                    )
                ):
                    merge_outcome_questions += 1
                    question = (
                        "直前のマージ後、空きセル数が期待どおり増えず、結果を自動判定できません。"
                        "バブル、緑色の紙幣アイテム、その他の追加生成物が見えますか？ "
                        "分かれば見分け方も教えてください。"
                    )
                    question_kwargs = dict(
                        choices=("バブルがある", "紙幣がある", "別の生成物がある", "追加生成物はない", "判断できない"),
                        context=(
                            f"expected_free_gain={merge_anomaly['expected_free_gain']}; "
                            f"actual_free_gain={merge_anomaly['actual_free_gain']}; "
                            f"merge_steps={list(merge_anomaly['steps'])}. "
                            f"new_cells={merge_anomaly.get('new_cells')}. "
                            "回答待ちでプレイは停止しません。回答は再利用可能な学習候補としてHuman Teachingへ引き継ぎます。"
                        ),
                        confidence=0.55,
                        impact="medium",
                        preview_path=snapshot.bindings.preview_path,
                        promote_answer_to_teaching=True,
                    )
                    if hasattr(self.runtime, "post_operator_question"):
                        posted = self.runtime.post_operator_question(
                            question,
                            **question_kwargs,
                        dedupe_key=(
                            "merge-boss.merge-outcome:"
                            f"{tuple(merge_anomaly['steps'])}:"
                            f"{merge_anomaly['expected_free_gain']}:"
                            f"{merge_anomaly['actual_free_gain']}"
                        ),
                        )
                        cycles.append({
                            "cycle": cycle_index,
                            "decision_kind": "merge_outcome_question_posted",
                            "action_count": 0,
                            "question_id": posted.get("question_id"),
                            "anomaly": merge_anomaly,
                        })
                    else:
                        response = self.runtime.ask_operator(
                            question,
                            timeout=120.0,
                            **question_kwargs,
                        )
                        if response.get("answered"):
                            if hasattr(self.runtime, "pending_human_teaching"):
                                paused_teaching = self.runtime.pending_human_teaching()
                            cycles.append({
                                "cycle": cycle_index,
                                "decision_kind": "merge_outcome_teaching",
                                "action_count": 0,
                                "operator_response": response,
                                "anomaly": merge_anomaly,
                            })
                            break
            remaining_merges = max(0, max_merges - total_merges)
            # Keep the action budget bounded by taps sent, not by optimistic
            # output assumptions. Confirmed emissions are reported separately.
            remaining_emissions = max(0, max_emissions - total_emission_attempts)
            decision_started = time.perf_counter()
            visual_merge_pairs = tuple(
                pair for pair in snapshot.visual_merge_pairs
                if tuple(sorted(pair)) not in suppressed_merge_pairs
            )
            decision = plan_merge_boss_turn(
                snapshot.items,
                snapshot.orders,
                snapshot.producers,
                free_cells=snapshot.free_cells,
                energy=snapshot.energy,
                catalog=self.catalog,
                max_merges=remaining_merges,
                max_emissions=remaining_emissions,
                uncertain_burst_size=uncertain_burst_size,
                visual_merge_pairs=visual_merge_pairs,
            )
            if (
                decision.get("kind") == "produce"
                and sticky_producer_id == decision.get("producer_id")
                and sticky_producer_cell is not None
                and sticky_producer_cell
                in {producer.cell_id for producer in snapshot.producers}
            ):
                decision = {
                    **decision,
                    "producer_cell": sticky_producer_cell,
                    "producer_cell_sticky": True,
                }
            decision_duration_ms = (time.perf_counter() - decision_started) * 1000
            self._trace_decision(
                cycle_index,
                decision,
                snapshot,
                duration_ms=decision_duration_ms,
            )

            kind = decision["kind"]
            if kind == "review_refresh":
                operator_response = self._review_refresh_with_operator(decision)
                choice = str(operator_response.get("choice") or "").strip().lower()
                answer = str(operator_response.get("answer") or "").strip().lower()
                approved = choice in {"更新する", "refresh", "yes"} or answer in {
                    "更新する", "refresh", "yes", "はい",
                }
                if not approved:
                    order_refresh_review = {
                        "order_id": decision.get("order_id"),
                        "reason": operator_response.get("reason") or (
                            "operator_declined" if operator_response.get("answered") else "operator_unanswered"
                        ),
                        "requested_levels": decision.get("requested_levels"),
                        "priority_distance": decision.get("priority_distance"),
                    }
                    cycles.append({
                        "cycle": cycle_index,
                        "decision": decision,
                        "operator_response": operator_response,
                        "actions": 0,
                    })
                    break
                decision = {**decision, "kind": "refresh"}
                kind = "refresh"
            if kind == "deliver":
                actions = self._delivery_actions(decision, snapshot.bindings)
            elif kind == "merge":
                actions = self._merge_actions(decision, snapshot.bindings)
            elif kind == "produce":
                actions = self._production_actions(decision, snapshot.bindings)
            elif kind == "refresh":
                actions = self._refresh_order_actions(decision, snapshot.bindings)
            else:
                public_decision = {
                    key: value
                    for key, value in decision.items()
                    if key != "order_analysis"
                }
                cycles.append({"cycle": cycle_index, "decision": public_decision, "actions": 0})
                break

            if not actions:
                self._trace_failure(
                    cycle_index,
                    reason="missing_action_binding",
                    phase="planning",
                    expected=f"action binding for decision {kind}",
                    actual="no safe bound action",
                    next_action="re-observe before attempting another action",
                )
                cycles.append({
                    "cycle": cycle_index,
                    "decision": decision,
                    "actions": 0,
                    "reason": "missing_action_binding",
                })
                break

            pause = self._pause_checkpoint("merge_boss_turn.before_action")
            pause_wait_ms += pause["wait_ms"]
            if pause["replan_required"]:
                pause_replans += 1
                if hasattr(self.perception, "invalidate_order_cache"):
                    self.perception.invalidate_order_cache()
                # Human use makes every pre-pause board/order binding stale.
                # Re-perceive in the same bounded cycle budget instead of
                # executing or counting a stale planned action.
                continue

            try:
                result = self.runtime.act(
                    actions,
                    observation_id=snapshot.bindings.observation_id,
                )
            except RuntimeError as exc:
                if getattr(exc, "code", None) == "PAUSE_REPLAN_REQUIRED":
                    pause_replans += 1
                    if hasattr(self.perception, "invalidate_order_cache"):
                        self.perception.invalidate_order_cache()
                    continue
                recoveries += 1
                self._trace_failure(
                    cycle_index,
                    reason="action_execution_failed",
                    phase="execute",
                    expected=f"successful {kind} action batch",
                    actual=f"{type(exc).__name__}: {exc}",
                    next_action="re-observe resulting board and re-plan without replay",
                )
                cycles.append({
                    "cycle": cycle_index,
                    "decision_kind": kind,
                    "action_count": len(actions),
                    "failed": True,
                    "error_type": type(exc).__name__,
                })
                if recoveries > max_recoveries:
                    break
                # Low-risk game policy: do not replay an uncertain partial
                # batch. Re-perceive the resulting board and plan from truth.
                continue
            if kind == "merge":
                total_merges += len(decision["merges"])
                items_by_cell = self._items_by_cell(snapshot)
                pending_merge = {
                    "free_cells": snapshot.free_cells,
                    "occupied_cells": self._item_cells(snapshot),
                    "item_states": {
                        cell_id: (
                            getattr(item, "identity", None),
                            getattr(item, "level", None),
                        )
                        for step in decision["merges"]
                        for cell_id in (step.source_cell, step.target_cell)
                        for item in [items_by_cell.get(cell_id)]
                        if item is not None
                    },
                    "steps": tuple(
                        (step.source_cell, step.target_cell)
                        for step in decision["merges"]
                    ),
                }
            elif kind == "produce":
                attempts = int(decision["emissions"])
                total_emission_attempts += attempts
                sticky_producer_id = decision.get("producer_id")
                sticky_producer_cell = decision.get("producer_cell")
                pending_production = {
                    "cycle": cycle_index,
                    "attempts": attempts,
                    "occupied_cells": self._item_cells(snapshot),
                    "free_cells": snapshot.free_cells,
                }
            elif kind == "deliver":
                deliveries += len(actions)
                if hasattr(self.perception, "invalidate_order_cache"):
                    self.perception.invalidate_order_cache()
            elif kind == "refresh":
                order_refreshes += 1
                sticky_producer_id = None
                sticky_producer_cell = None
                if hasattr(self.perception, "invalidate_order_cache"):
                    self.perception.invalidate_order_cache()
            cycles.append({
                "cycle": cycle_index,
                "decision_kind": kind,
                "action_count": len(actions),
                "duration_ms": result.get("duration_ms"),
                **(
                    {"emission_attempts": int(decision["emissions"])}
                    if kind == "produce"
                    else {}
                ),
            })
            cycle_index += 1

        return {
            "cycles": cycles,
            "merge_count": total_merges,
            "emission_count": total_emissions,
            "emission_attempt_count": total_emission_attempts,
            "unconfirmed_emission_attempt_count": unconfirmed_emission_attempts,
            "pending_emission_attempt_count": (
                pending_production["attempts"] if pending_production is not None else 0
            ),
            "delivery_count": deliveries,
            "order_refresh_count": order_refreshes,
            "order_refresh_review": order_refresh_review,
            "recovery_count": recoveries,
            "bubble_preemption_count": bubble_preemptions,
            "merge_outcome_question_count": merge_outcome_questions,
            "pause_replan_count": pause_replans,
            "pause_wait_ms": round(pause_wait_ms, 3),
            "paused_for_human_teaching": paused_teaching is not None,
            "human_teaching": paused_teaching,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

