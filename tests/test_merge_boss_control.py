import json
import unittest

from phone_harness.workflows.merge_boss_control import (
    MergeBossBindings,
    MergeBossSnapshot,
    MergeBossTurnController,
)
from phone_harness.workflows.merge_boss_strategy import (
    MergeBossBoardItem,
    MergeBossOrder,
    MergeBossProducer,
)


class FakeRuntime:
    def __init__(self, fail_first=0, teaching=None, pause_waits=None, pause_checkpoints=None, operator_response=None):
        self.calls = []
        self.fail_first = fail_first
        self.teaching = teaching
        self.pause_waits = list(pause_waits or [])
        self.pause_checkpoints = list(pause_checkpoints or [])
        self.operator_response = operator_response
        self.operator_questions = []

    def wait_if_paused(self, **_kwargs):
        return self.pause_waits.pop(0) if self.pause_waits else 0.0

    def pause_checkpoint(self, **_kwargs):
        if self.pause_checkpoints:
            return self.pause_checkpoints.pop(0)
        waited = self.wait_if_paused(**_kwargs)
        return {"wait_ms": waited, "replan_required": waited > 0}

    def pending_human_teaching(self):
        return self.teaching

    def ask_operator(self, question, **kwargs):
        self.operator_questions.append((question, kwargs))
        if self.operator_response is not None:
            return dict(self.operator_response)
        return {"answered": False, "answer": None, "choice": None, "skipped": True, "reason": "operator_absent"}

    def act(self, actions, observation_id=None):
        self.calls.append((actions, observation_id))
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("simulated partial/unknown game action failure")
        return {"duration_ms": 1.0, "action_count": len(actions)}


class FakePerception:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.index = 0

    def snapshot(self, **_kwargs):
        value = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return value


class RecordingPerception(FakePerception):
    def __init__(self, snapshots):
        super().__init__(snapshots)
        self.snapshot_options = []

    def snapshot(self, **kwargs):
        self.snapshot_options.append(dict(kwargs))
        return super().snapshot(**kwargs)


def bindings(order_buttons=None, selected=None, refresh_buttons=None):
    centers = {
        "a": (10, 10), "b": (20, 10), "c": (30, 10), "d": (40, 10),
        "r8c0": (50, 90), "r8c3": (80, 90), "r8c4": (90, 90),
    }
    return MergeBossBindings(
        observation_id=1,
        cell_centers=centers,
        complete_centers=order_buttons or {},
        refresh_centers=refresh_buttons or {},
        selected_producer_cell=selected,
    )


class MergeBossControlTests(unittest.TestCase):
    def test_merge_created_nearby_duplicate_preempts_as_probable_bubble(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "same-lv1", 1),
                    MergeBossBoardItem("b", "same-lv1", 1),
                ),
                orders=(), producers=(), free_cells=10, energy=10,
                bindings=bindings(),
                visual_merge_pairs=(("a", "b"),),
            ),
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("b", "same-lv2", 2),
                    MergeBossBoardItem("c", "same-lv2", 2),
                ),
                orders=(), producers=(), free_cells=10, energy=10,
                bindings=bindings(),
                visual_merge_pairs=(("b", "c"),),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=2, max_merges=2, max_emissions=0
        )

        self.assertEqual(result["merge_count"], 1)
        self.assertEqual(result["bubble_preemption_count"], 1)
        self.assertEqual(len(runtime.calls), 2)
        self.assertEqual(runtime.calls[1][0], [
            {"op": "tap", "x": 30, "y": 10},
            {"op": "tap", "x": 30, "y": 10},
        ])
        self.assertEqual(result["cycles"][-1]["decision_kind"], "bubble_preempt")

    def test_merge_created_extra_object_explains_missing_free_cell_gain(self):
        pending = {
            "free_cells": 10,
            "occupied_cells": {"a", "b"},
            "steps": (("a", "b"),),
        }
        snapshot = MergeBossSnapshot(
            items=(
                MergeBossBoardItem("b", "same-lv2", 2),
                MergeBossBoardItem("c", "visual:green-cash", 0),
            ),
            orders=(), producers=(), free_cells=10, energy=10,
            bindings=bindings(), visual_merge_pairs=(),
        )
        spawn = MergeBossTurnController._merge_spawn_evidence(pending, snapshot)
        self.assertEqual(spawn["new_cells"], ["c"])
        self.assertIsNone(
            MergeBossTurnController._merge_outcome_anomaly(
                pending, snapshot, spawn_evidence=spawn
            )
        )

    def test_same_source_target_pair_after_merge_is_not_assumed_to_be_bubble(self):
        pending = {"free_cells": 10, "steps": (("a", "b"),)}
        snapshot = MergeBossSnapshot(
            items=(), orders=(), producers=(), free_cells=10, energy=10,
            bindings=bindings(), visual_merge_pairs=(("a", "b"),),
        )
        self.assertIsNone(
            MergeBossTurnController._bubble_candidate_from_pending_merge(pending, snapshot)
        )

    def test_known_before_occupancy_requires_new_cell_for_bubble_preemption(self):
        pending = {
            "free_cells": 10,
            "occupied_cells": {"a", "b", "c"},
            "steps": (("a", "b"),),
        }
        snapshot = MergeBossSnapshot(
            items=(
                MergeBossBoardItem("b", "same-lv2", 2),
                MergeBossBoardItem("c", "same-lv2", 2),
            ),
            orders=(), producers=(), free_cells=10, energy=10,
            bindings=bindings(), visual_merge_pairs=(("b", "c"),),
        )
        self.assertIsNone(
            MergeBossTurnController._bubble_candidate_from_pending_merge(pending, snapshot)
        )

    def test_same_source_and_target_item_states_are_strong_noop_evidence(self):
        pending = {
            "free_cells": 10,
            "occupied_cells": {"a", "b"},
            "item_states": {
                "a": ("same-lv1", 1),
                "b": ("same-lv1", 1),
            },
            "steps": (("a", "b"),),
        }
        snapshot = MergeBossSnapshot(
            items=(
                MergeBossBoardItem("a", "same-lv1", 1),
                MergeBossBoardItem("b", "same-lv1", 1),
            ),
            orders=(), producers=(), free_cells=10, energy=10,
            bindings=bindings(), visual_merge_pairs=(("a", "b"),),
        )
        evidence = MergeBossTurnController._merge_noop_evidence(pending, snapshot)
        self.assertEqual(evidence["pairs"], (("a", "b"),))

    def test_ambiguous_merge_outcome_asks_human_and_promotes_answer(self):
        runtime = FakeRuntime(operator_response={
            "answered": True,
            "answer": "マージ後に追加で出たバブルです",
            "choice": "バブルがある",
            "skipped": False,
            "teaching_message_id": "teach-merge-anomaly",
        })
        pending_teaching = {"value": None}
        runtime.pending_human_teaching = lambda: pending_teaching["value"]
        original_ask_operator = runtime.ask_operator

        def ask_operator_and_promote(question, **kwargs):
            response = original_ask_operator(question, **kwargs)
            pending_teaching["value"] = {
                "message_id": "teach-merge-anomaly",
                "text": "マージ後に追加で出たバブルです",
                "has_image": False,
            }
            return response

        runtime.ask_operator = ask_operator_and_promote
        perception = FakePerception([
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "same-lv1", 1), MergeBossBoardItem("b", "same-lv1", 1)),
                orders=(), producers=(), free_cells=10, energy=10,
                bindings=bindings(), visual_merge_pairs=(("a", "b"),),
            ),
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "same-lv1", 1), MergeBossBoardItem("b", "same-lv1", 1)),
                orders=(), producers=(), free_cells=10, energy=10,
                bindings=MergeBossBindings(
                    observation_id=2,
                    cell_centers=bindings().cell_centers,
                    preview_path="board-after-merge.png",
                ),
                visual_merge_pairs=(("a", "b"),),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=2, max_merges=2, max_emissions=0
        )

        self.assertEqual(result["merge_outcome_question_count"], 1)
        self.assertTrue(result["paused_for_human_teaching"])
        self.assertEqual(len(runtime.operator_questions), 1)
        kwargs = runtime.operator_questions[0][1]
        self.assertEqual(kwargs["preview_path"], "board-after-merge.png")
        self.assertTrue(kwargs["promote_answer_to_teaching"])
        self.assertEqual(result["cycles"][-1]["decision_kind"], "merge_outcome_teaching")

    def test_idle_workflow_result_is_json_serializable(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(MergeBossProducer("r8c0", "unknown", 1),),
                # This test covers the serializable generic idle result, not
                # the full-board jam policy. Full boards now escalate to
                # refresh/question recovery instead of idling.
                free_cells=1,
                energy=20,
                bindings=bindings(),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(max_cycles=1)

        encoded = json.dumps(result, ensure_ascii=False)
        self.assertIn("no_known_producer_can_advance_orders", encoded)
        self.assertNotIn("order_analysis", encoded)

    def test_high_level_order_refresh_uses_recognized_green_button_after_human_approval(self):
        runtime = FakeRuntime(operator_response={
            "answered": True,
            "answer": None,
            "choice": "更新する",
            "skipped": False,
        })
        perception = FakePerception([
            MergeBossSnapshot(
                items=(),
                orders=(
                    MergeBossOrder("high-a", (("romeo-juliet-props-lv10", 10),)),
                    MergeBossOrder("high-b", (("viewing-equipment-lv9", 9),)),
                ),
                producers=(),
                free_cells=12,
                energy=20,
                bindings=bindings(refresh_buttons={"high-b": (430, 550), "high-a": (820, 550)}),
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=12, energy=20,
                bindings=bindings(),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(max_cycles=2, max_emissions=0)

        self.assertEqual(result["order_refresh_count"], 1)
        self.assertEqual(len(runtime.operator_questions), 1)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0][0], [{"op": "tap", "x": 430, "y": 550}])

    def test_high_level_order_refresh_review_returns_without_action_when_operator_absent(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(),
                orders=(
                    MergeBossOrder("high-a", (("romeo-juliet-props-lv10", 10),)),
                    MergeBossOrder("high-b", (("viewing-equipment-lv9", 9),)),
                ),
                producers=(), free_cells=12, energy=20,
                bindings=bindings(refresh_buttons={"high-a": (430, 550), "high-b": (820, 550)}),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(max_cycles=2, max_emissions=0)

        self.assertEqual(result["order_refresh_count"], 0)
        self.assertEqual(runtime.calls, [])
        self.assertIsNotNone(result["order_refresh_review"])
        self.assertEqual(result["order_refresh_review"]["reason"], "operator_absent")

    def test_quick_pause_resume_before_action_replans_from_fresh_snapshot(self):
        runtime = FakeRuntime(pause_checkpoints=[
            {"wait_ms": 0.0, "replan_required": False},
            {"wait_ms": 0.0, "replan_required": True},
            {"wait_ms": 0.0, "replan_required": False},
            {"wait_ms": 0.0, "replan_required": False},
        ])
        perception = RecordingPerception([
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "エビ", 1), MergeBossBoardItem("b", "エビ", 1)),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "エビ", 1), MergeBossBoardItem("b", "エビ", 1)),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
        ])

        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=1, max_merges=1, max_emissions=0
        )

        self.assertEqual(result["pause_replan_count"], 1)
        self.assertEqual(len(perception.snapshot_options), 2)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(result["merge_count"], 1)

    def test_operator_pause_waits_then_replans_without_consuming_cycle_budget(self):
        runtime = FakeRuntime(pause_waits=[0.0, 25.0, 0.0, 0.0])
        perception = RecordingPerception([
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "エビ", 1), MergeBossBoardItem("b", "エビ", 1)),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(MergeBossBoardItem("a", "エビ", 1), MergeBossBoardItem("b", "エビ", 1)),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=1, max_merges=1, max_emissions=0
        )
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(result["merge_count"], 1)
        self.assertEqual(result["pause_replan_count"], 1)
        self.assertGreaterEqual(result["pause_wait_ms"], 25.0)
        self.assertEqual(len(perception.snapshot_options), 2)

    def test_pending_human_teaching_pauses_before_perception_or_phone_action(self):
        runtime = FakeRuntime(teaching={
            "message_id": "teach-1",
            "text": "Do not merge the pink item yet.",
            "has_image": True,
        })
        perception = RecordingPerception([
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=20, energy=20,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(max_cycles=2)
        self.assertTrue(result["paused_for_human_teaching"])
        self.assertEqual(result["human_teaching"]["message_id"], "teach-1")
        self.assertEqual(perception.snapshot_options, [])
        self.assertEqual(runtime.calls, [])

    def test_perception_failure_returns_structured_failure_instead_of_escaping(self):
        class FailingPerception:
            def snapshot(self, **_kwargs):
                raise RuntimeError("unknown Merge Boss order item visual")

        result = MergeBossTurnController(FakeRuntime(), FailingPerception()).run(max_cycles=1)
        self.assertFalse(result["paused_for_human_teaching"])
        self.assertTrue(result["cycles"][0]["failed"])
        self.assertEqual(result["cycles"][0]["phase"], "perception")
        self.assertIn("unknown Merge Boss order item visual", result["cycles"][0]["error"])

    def test_cycle_zero_does_not_force_order_rescan(self):
        runtime = FakeRuntime()
        perception = RecordingPerception([
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=20, energy=20,
                bindings=bindings(),
            ),
        ])
        MergeBossTurnController(runtime, perception).run(max_cycles=1)
        self.assertEqual(perception.snapshot_options, [{"rescan_orders": False}])

    def test_chain_merge_rounds_are_flattened_into_one_runtime_batch(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "エビ", 1),
                    MergeBossBoardItem("b", "エビ", 1),
                    MergeBossBoardItem("c", "エビ", 1),
                    MergeBossBoardItem("d", "エビ", 1),
                ),
                orders=(MergeBossOrder("o", (("チョウザメ", 3),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(MergeBossBoardItem("d", "チョウザメ", 3),),
                orders=(MergeBossOrder("o", (("チョウザメ", 3),)),),
                producers=(), free_cells=13, energy=10,
                bindings=bindings({"o": (100, 100)}),
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=14, energy=10,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(max_cycles=3)

        self.assertEqual(len(runtime.calls), 2)
        merge_actions = runtime.calls[0][0]
        self.assertEqual([action["op"] for action in merge_actions], ["drag", "drag", "drag"])
        self.assertEqual(result["merge_count"], 3)
        self.assertEqual(result["delivery_count"], 1)

    def test_production_burst_is_one_runtime_batch_then_replans_locally(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8),),
                free_cells=20, energy=20, bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "エビ", 1),
                    MergeBossBoardItem("b", "エビ", 1),
                    MergeBossBoardItem("c", "エビ", 1),
                    MergeBossBoardItem("d", "エビ", 1),
                ),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8),),
                free_cells=16, energy=16, bindings=bindings(selected="r8c0"),
            ),
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("b", "カニ", 2),
                    MergeBossBoardItem("c", "エビ", 1),
                    MergeBossBoardItem("d", "エビ", 1),
                ),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8),),
                free_cells=17, energy=16,
                bindings=bindings({"o": (100, 100)}, selected="r8c0"),
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=20, energy=16,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=4, max_emissions=20, uncertain_burst_size=4
        )

        # Unknown selection state never adds an optimistic extra tap; this can
        # under-emit by one but cannot overflow the planner's free-cell budget.
        self.assertEqual(len(runtime.calls[0][0]), 4)
        self.assertTrue(all(action["op"] == "tap" for action in runtime.calls[0][0]))
        self.assertEqual(runtime.calls[1][0][0]["op"], "drag")
        self.assertEqual(runtime.calls[2][0][0]["op"], "tap")
        self.assertEqual(result["emission_count"], 4)
        self.assertEqual(result["merge_count"], 1)
        self.assertEqual(result["delivery_count"], 1)

    def test_production_taps_are_not_counted_as_emissions_until_board_changes(self):
        runtime = FakeRuntime()
        unchanged = MergeBossSnapshot(
            items=(),
            orders=(MergeBossOrder("o", (("カニ", 2),)),),
            producers=(MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8),),
            free_cells=20,
            energy=20,
            bindings=bindings(),
        )
        perception = FakePerception([unchanged, unchanged])

        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=2,
            max_emissions=4,
            uncertain_burst_size=4,
        )

        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(result["emission_count"], 0)

    def test_consecutive_same_producer_keeps_physical_cell_when_identity_flickers(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(),
                orders=(MergeBossOrder("o", (("romeo-juliet-props-lv8", 8),)),),
                producers=(
                    MergeBossProducer("r8c3", "縦型フィルムカメラ", 8),
                    MergeBossProducer("r8c4", "縦型フィルムカメラ", 8),
                ),
                free_cells=20,
                energy=20,
                bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(),
                orders=(MergeBossOrder("o", (("romeo-juliet-props-lv8", 8),)),),
                producers=(
                    MergeBossProducer("r8c3", "visual-producer:animated", 0),
                    MergeBossProducer("r8c4", "縦型フィルムカメラ", 8),
                ),
                free_cells=20,
                energy=20,
                bindings=bindings(),
            ),
        ])

        MergeBossTurnController(runtime, perception).run(
            max_cycles=2,
            max_emissions=4,
            uncertain_burst_size=2,
        )

        first_taps = runtime.calls[0][0]
        second_taps = runtime.calls[1][0]
        self.assertEqual({(tap["x"], tap["y"]) for tap in first_taps}, {(80, 90)})
        self.assertEqual({(tap["x"], tap["y"]) for tap in second_taps}, {(80, 90)})

    def test_delivery_still_runs_after_merge_budget_is_reached(self):
        runtime = FakeRuntime()
        perception = FakePerception([
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "エビ", 1),
                    MergeBossBoardItem("b", "エビ", 1),
                ),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
            MergeBossSnapshot(
                items=(MergeBossBoardItem("b", "カニ", 2),),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=11, energy=10,
                bindings=bindings({"o": (100, 100)}),
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=12, energy=10,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=3, max_merges=1, max_emissions=0
        )
        self.assertEqual(result["merge_count"], 1)
        self.assertEqual(result["delivery_count"], 1)
        self.assertEqual(len(runtime.calls), 2)

    def test_failed_batch_reobserves_and_replans_locally(self):
        runtime = FakeRuntime(fail_first=1)
        perception = FakePerception([
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "エビ", 1),
                    MergeBossBoardItem("b", "エビ", 1),
                ),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=10, energy=10, bindings=bindings(),
            ),
            # After the failed/unknown batch, perception is authoritative. In
            # this simulated case the merge actually happened despite the error.
            MergeBossSnapshot(
                items=(MergeBossBoardItem("b", "カニ", 2),),
                orders=(MergeBossOrder("o", (("カニ", 2),)),),
                producers=(), free_cells=11, energy=10,
                bindings=bindings({"o": (100, 100)}),
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=12, energy=10,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(
            max_cycles=3, max_recoveries=2
        )
        self.assertEqual(result["recovery_count"], 1)
        self.assertEqual(result["delivery_count"], 1)
        self.assertEqual(len(runtime.calls), 2)

    def test_delivery_can_scroll_back_to_offscreen_completed_orders(self):
        runtime = FakeRuntime()
        scroller = {
            "op": "drag", "x1": 100, "y1": 100,
            "x2": 300, "y2": 100, "duration": 0.2,
        }
        bound = MergeBossBindings(
            observation_id=9,
            cell_centers={},
            complete_centers={"left": (20, 20), "right": (200, 20)},
            order_page_indices={"left": 0, "right": 2},
            order_scan_final_page=2,
            order_scroll_backward_action=scroller,
        )
        perception = FakePerception([
            MergeBossSnapshot(
                items=(
                    MergeBossBoardItem("a", "A", 1),
                    MergeBossBoardItem("b", "B", 1),
                ),
                orders=(
                    MergeBossOrder("left", (("A", 1),)),
                    MergeBossOrder("right", (("B", 1),)),
                ),
                producers=(), free_cells=10, energy=10, bindings=bound,
            ),
            MergeBossSnapshot(
                items=(), orders=(), producers=(), free_cells=12, energy=10,
                bindings=bindings(),
            ),
        ])
        result = MergeBossTurnController(runtime, perception).run(max_cycles=2)
        actions = runtime.calls[0][0]
        self.assertEqual(
            [action["op"] for action in actions],
            ["tap", "drag", "drag", "tap"],
        )
        self.assertEqual(result["delivery_count"], 4)


if __name__ == "__main__":
    unittest.main()
