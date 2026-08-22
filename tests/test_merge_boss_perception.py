import unittest

from phone_harness.workflows.merge_boss_control import MergeBossSnapshot
from phone_harness.workflows.merge_boss_perception import (
    MergeBossBoardReadout,
    MergeBossItemFamilyHintReadout,
    MergeBossLivePerception,
    MergeBossOrderCardReadout,
    MergeBossOrderPageReadout,
    MergeBossPerceptionCalibration,
    MergeBossPerceptionReaders,
    MergeBossProducerHintReadout,
    parse_item_family_hint_elements,
    parse_producer_hint_elements,
    stable_order_id,
)
from phone_harness.semantic_slots import SemanticSlot
import json
import tempfile
from pathlib import Path
from phone_harness.workflows.merge_boss_strategy import (
    MergeBossBoardItem,
    MergeBossProducer,
)


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def act(self, actions, observation_id=None):
        self.calls.append((actions, observation_id))
        return {"duration_ms": 1.0}


class MergeBossPerceptionTests(unittest.TestCase):
    def test_repeated_single_pair_anomaly_gets_short_cooldown(self):
        perception = MergeBossLivePerception.__new__(MergeBossLivePerception)
        perception.runtime = type("Runtime", (), {"trace": None})()
        perception._merge_anomaly_counts = {}
        perception._merge_pair_cooldowns = {}
        snapshot = type("Snapshot", (), {
            "bindings": type("Bindings", (), {"preview_path": None})(),
        })()
        anomaly = {
            "steps": (("r1c1", "r3c6"),),
            "actual_free_gain": 0,
            "new_cells": [],
        }

        self.assertEqual(perception.record_merge_anomaly({}, snapshot, anomaly), ())
        self.assertEqual(
            perception.record_merge_anomaly({}, snapshot, anomaly),
            (("r1c1", "r3c6"),),
        )
        self.assertEqual(
            perception._apply_merge_pair_cooldowns((("r1c1", "r3c6"), ("r0c0", "r0c1"))),
            (("r0c0", "r0c1"),),
        )
    def test_uncalibrated_adapter_stays_fail_closed(self):
        perception = MergeBossLivePerception(
            FakeRuntime(),
            calibration=MergeBossPerceptionCalibration(calibrated=False),
        )
        self.assertFalse(perception.status()["ready"])
        with self.assertRaisesRegex(RuntimeError, "not calibrated"):
            perception.snapshot()

    def test_snapshot_enumerates_overlapping_orders_then_reads_board_last(self):
        runtime = FakeRuntime()
        pages = {
            0: MergeBossOrderPageReadout(1, (
                MergeBossOrderCardReadout("o1", (("A", 1),), (10, 10), refresh_center=(11, 12)),
                MergeBossOrderCardReadout("o2", (("B", 2),), None),
            )),
            1: MergeBossOrderPageReadout(2, (
                MergeBossOrderCardReadout("o2", (("B", 2),), None),
                MergeBossOrderCardReadout("o3", (("C", 3),), (30, 10), refresh_center=(31, 12)),
            )),
            2: MergeBossOrderPageReadout(3, (
                MergeBossOrderCardReadout("o3", (("C", 3),), (30, 10)),
            )),
        }
        events = []

        def read_orders(_runtime, _calibration, page_index):
            events.append(("orders", page_index))
            return pages[min(page_index, 2)]

        def scroll_orders(_runtime, _calibration):
            return len([event for event in events if event[0] == "orders"]) < 3

        def read_board(_runtime, _calibration):
            events.append(("board", None))
            return MergeBossBoardReadout(
                observation_id=9,
                items=(MergeBossBoardItem("r0c0", "A", 1),),
                producers=(MergeBossProducer("r8c0", "P", 1),),
                free_cells=10,
                energy=7,
                cell_centers={"r0c0": (1, 1), "r8c0": (2, 2)},
                selected_producer_cell="r8c0",
            )

        readers = MergeBossPerceptionReaders(
            read_board=read_board,
            read_visible_orders=read_orders,
            scroll_orders_forward=scroll_orders,
            read_item_family_hint=lambda *_args: None,
            read_producer_output_hint=lambda *_args: None,
        )
        calibration = MergeBossPerceptionCalibration(
            calibrated=True,
            screen_size=(1206, 2622),
            board_bounds={"x": 35, "y": 910, "w": 1135, "h": 1485},
            order_strip_bounds={"x": 0, "y": 0, "w": 1206, "h": 500},
            order_scroll_forward_action={
                "op": "drag", "x1": 900, "y1": 300,
                "x2": 300, "y2": 300, "duration": 0.2,
            },
            order_scroll_backward_action={
                "op": "drag", "x1": 10, "y1": 10,
                "x2": 30, "y2": 10, "duration": 0.2,
            },
            max_order_pages=4,
            stagnant_order_pages=1,
        )
        snapshot = MergeBossLivePerception(
            runtime, calibration=calibration, readers=readers
        ).snapshot()

        self.assertIsInstance(snapshot, MergeBossSnapshot)
        self.assertEqual([order.order_id for order in snapshot.orders], ["o1", "o2", "o3"])
        self.assertEqual(snapshot.energy, 7)
        self.assertEqual(snapshot.bindings.observation_id, 9)
        self.assertEqual(snapshot.bindings.order_page_indices, {"o1": 0, "o2": 0, "o3": 1})
        self.assertEqual(snapshot.bindings.refresh_centers, {"o1": (11, 12), "o3": (31, 12)})
        self.assertEqual(snapshot.bindings.order_scan_final_page, 1)
        self.assertEqual(events[-1], ("board", None))

    def test_empty_order_scan_cache_is_not_reused_after_transient_overlay(self):
        runtime = FakeRuntime()
        order_reads = []

        def read_orders(_runtime, _calibration, _page_index):
            order_reads.append(len(order_reads))
            if len(order_reads) == 1:
                return MergeBossOrderPageReadout(1, ())
            return MergeBossOrderPageReadout(2, (
                MergeBossOrderCardReadout("o1", (("A", 1),), None),
            ))

        readers = MergeBossPerceptionReaders(
            read_board=lambda *_args: MergeBossBoardReadout(
                observation_id=9,
                items=(),
                producers=(),
                free_cells=10,
                energy=7,
                cell_centers={},
            ),
            read_visible_orders=read_orders,
            scroll_orders_forward=lambda *_args: False,
            read_item_family_hint=lambda *_args: None,
            read_producer_output_hint=lambda *_args: None,
        )
        calibration = MergeBossPerceptionCalibration(
            calibrated=True,
            screen_size=(1206, 2622),
            board_bounds={"x": 35, "y": 910, "w": 1135, "h": 1485},
            order_strip_bounds={"x": 0, "y": 190, "w": 1206, "h": 410},
            order_scroll_forward_action={"op": "drag", "x1": 1000, "y1": 350, "x2": 300, "y2": 350},
            order_scroll_backward_action={"op": "drag", "x1": 300, "y1": 350, "x2": 1000, "y2": 350},
        )
        perception = MergeBossLivePerception(runtime, calibration=calibration, readers=readers)

        first = perception.snapshot(rescan_orders=True)
        second = perception.snapshot(rescan_orders=False)

        self.assertEqual(first.orders, ())
        self.assertEqual([order.order_id for order in second.orders], ["o1"])
        self.assertEqual(len(order_reads), 2)

    def test_order_card_complete_or_green_checks_mark_order_ready(self):
        runtime = FakeRuntime()

        def read_orders(_runtime, _calibration, _page_index):
            return MergeBossOrderPageReadout(1, (
                MergeBossOrderCardReadout(
                    "checked",
                    (("A", 1), ("B", 2)),
                    None,
                    all_items_present=True,
                ),
                MergeBossOrderCardReadout(
                    "button",
                    (("C", 3),),
                    (30, 10),
                ),
            ))

        readers = MergeBossPerceptionReaders(
            read_board=lambda *_args: MergeBossBoardReadout(
                observation_id=9,
                items=(),
                producers=(),
                free_cells=10,
                energy=7,
                cell_centers={},
            ),
            read_visible_orders=read_orders,
            scroll_orders_forward=lambda *_args: False,
            read_item_family_hint=lambda *_args: None,
            read_producer_output_hint=lambda *_args: None,
        )
        calibration = MergeBossPerceptionCalibration(
            calibrated=True,
            screen_size=(1206, 2622),
            board_bounds={"x": 35, "y": 910, "w": 1135, "h": 1485},
            order_strip_bounds={"x": 0, "y": 190, "w": 1206, "h": 410},
            order_scroll_forward_action={"op": "drag", "x1": 1000, "y1": 350, "x2": 300, "y2": 350},
            order_scroll_backward_action={"op": "drag", "x1": 300, "y1": 350, "x2": 1000, "y2": 350},
        )
        snapshot = MergeBossLivePerception(runtime, calibration=calibration, readers=readers).snapshot()
        ready = {order.order_id: order.ready for order in snapshot.orders}
        self.assertEqual(ready, {"checked": True, "button": True})

    def test_ready_flag_alone_does_not_bypass_missing_geometry_but_hint_learning_is_separate(self):
        readers = MergeBossPerceptionReaders(
            read_board=lambda *_args: None,
            read_visible_orders=lambda *_args: None,
        )
        perception = MergeBossLivePerception(
            FakeRuntime(),
            calibration=MergeBossPerceptionCalibration(calibrated=True),
            readers=readers,
        )
        status = perception.status()
        self.assertFalse(status["ready"])
        self.assertIn("order_strip_bounds_missing", status["missing"])

    def test_cached_knowledge_path_can_be_ready_while_hint_learning_is_not(self):
        readers = MergeBossPerceptionReaders(
            read_board=lambda *_args: None,
            read_visible_orders=lambda *_args: None,
        )
        perception = MergeBossLivePerception(
            FakeRuntime(),
            calibration=MergeBossPerceptionCalibration(
                calibrated=True,
                screen_size=(1206, 2622),
                board_bounds={"x": 35, "y": 910, "w": 1135, "h": 1485},
                order_strip_bounds={"x": 0, "y": 190, "w": 1206, "h": 410},
                order_scroll_forward_action={"op": "drag", "x1": 1040, "y1": 390, "x2": 240, "y2": 390},
                order_scroll_backward_action={"op": "drag", "x1": 450, "y1": 390, "x2": 1100, "y2": 390},
            ),
            readers=readers,
        )
        status = perception.status()
        self.assertTrue(status["ready"])
        self.assertFalse(status["learning_ready"])
        self.assertIn("item_family_hint_reader_not_configured", status["learning_missing"])

    def test_known_item_family_does_not_reopen_hint(self):
        levels = [{"level": level, "identity": f"A{level}"} for level in range(1, 11)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "a",
                    "complete": True,
                    "levels": levels,
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            calls = []
            readers = MergeBossPerceptionReaders(
                read_board=lambda *_args: None,
                read_visible_orders=lambda *_args: None,
                read_item_family_hint=lambda *_args: calls.append(True),
            )
            perception = MergeBossLivePerception(
                FakeRuntime(), readers=readers, catalog_path=path
            )
            result = perception.ensure_item_family("A4", 4, hint_target={"cell": "r0c0"})
        self.assertFalse(result["learned"])
        self.assertEqual(result["family_id"], "a")
        self.assertEqual(calls, [])

    def test_unknown_item_family_opens_hint_once_and_persists(self):
        levels = tuple(
            {"level": level, "identity": f"B{level}"}
            for level in range(1, 11)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            calls = []
            readers = MergeBossPerceptionReaders(
                read_board=lambda *_args: None,
                read_visible_orders=lambda *_args: None,
                read_item_family_hint=lambda *_args: (
                    calls.append(True) or MergeBossItemFamilyHintReadout(levels)
                ),
            )
            perception = MergeBossLivePerception(
                FakeRuntime(), readers=readers, catalog_path=path
            )
            first = perception.ensure_item_family("B7", 7, hint_target={"order": "o1"})
            second = perception.ensure_item_family("B7", 7, hint_target={"order": "o1"})
        self.assertTrue(first["learned"])
        self.assertFalse(second["learned"])
        self.assertEqual(len(calls), 1)

    def test_incomplete_producer_opens_hint_then_becomes_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [{
                    "identity": "P", "level": 8,
                    "outputs_complete": False,
                    "possible_outputs": [{"identity": "A", "level": 1}],
                }],
            }), encoding="utf-8")
            calls = []
            readers = MergeBossPerceptionReaders(
                read_board=lambda *_args: None,
                read_visible_orders=lambda *_args: None,
                read_producer_output_hint=lambda *_args: (
                    calls.append(True)
                    or MergeBossProducerHintReadout(({"identity": "A", "level": 1},), True)
                ),
            )
            perception = MergeBossLivePerception(
                FakeRuntime(), readers=readers, catalog_path=path
            )
            first = perception.ensure_producer_catalog("P", 8, hint_target={"cell": "r8c0"})
            second = perception.ensure_producer_catalog("P", 8, hint_target={"cell": "r8c0"})
        self.assertTrue(first["learned"])
        self.assertFalse(second["learned"])
        self.assertEqual(len(calls), 1)

    def test_item_family_slot_parser_builds_complete_ten_level_hint(self):
        elements = []
        slots = []
        for level in range(1, 11):
            y = (level - 1) * 40
            slots.append(SemanticSlot({"x": 0, "y": y, "w": 200, "h": 35}, expected_level=level))
            elements.append({
                "text": f"Item-{level} Lv.{level}",
                "x": 10, "y": y + 5, "w": 120, "h": 20,
            })
        hint = parse_item_family_hint_elements(elements, slots)
        self.assertEqual(len(hint.levels), 10)
        self.assertEqual(hint.levels[-1], {"identity": "Item-10", "level": 10})

    def test_producer_slot_parser_returns_all_calibrated_outputs(self):
        slots = (
            SemanticSlot({"x": 0, "y": 0, "w": 100, "h": 40}, expected_level=1),
            SemanticSlot({"x": 0, "y": 40, "w": 100, "h": 40}, expected_level=2),
        )
        hint = parse_producer_hint_elements([
            {"text": "A", "x": 10, "y": 5, "w": 20, "h": 20},
            {"text": "B", "x": 10, "y": 45, "w": 20, "h": 20},
        ], slots)
        self.assertEqual(hint.outputs, (
            {"identity": "A", "level": 1},
            {"identity": "B", "level": 2},
        ))

    def test_calibration_round_trips_json_without_marking_unverified_fields_ready(self):
        calibration = MergeBossPerceptionCalibration(
            calibrated=False,
            screen_size=(1206, 2622),
            board_bounds={"x": 35, "y": 910, "w": 1135, "h": 1485},
            empty_cell_reference_rgb=(223, 220, 197),
            empty_cell_color_tolerance=24.0,
            item_family_hint_slots=({"bounds": {"x": 1, "y": 2, "w": 3, "h": 4}, "expected_level": 1},),
            order_marker_geometry={"marker_y": 511},
            producer_badge_roi={"x0": 0.7, "y0": 0.0, "x1": 0.99, "y1": 0.34},
            producer_badge_templates=({"version": 1, "size": [8, 8], "rgb4_b64": "AA=="},),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            calibration.save(path)
            loaded = MergeBossPerceptionCalibration.load(path)
        self.assertFalse(loaded.calibrated)
        self.assertEqual(loaded.screen_size, (1206, 2622))
        self.assertEqual(loaded.board_bounds["w"], 1135)
        self.assertEqual(loaded.empty_cell_reference_rgb, (223.0, 220.0, 197.0))
        self.assertEqual(loaded.empty_cell_color_tolerance, 24.0)
        self.assertEqual(loaded.order_marker_geometry["marker_y"], 511)
        self.assertEqual(len(loaded.item_family_hint_slots), 1)
        self.assertEqual(loaded.item_family_slots()[0].expected_level, 1)
        self.assertEqual(loaded.producer_badge_roi["x0"], 0.7)
        self.assertEqual(len(loaded.producer_badge_templates), 1)

    def test_calibration_scales_reference_pixels_to_live_screen(self):
        calibration = MergeBossPerceptionCalibration(
            calibrated=True,
            screen_size=(1000, 2000),
            board_bounds={"x": 100, "y": 400, "w": 700, "h": 900},
            order_strip_bounds={"x": 0, "y": 100, "w": 1000, "h": 300},
            order_scroll_forward_action={
                "op": "drag", "x1": 800, "y1": 300, "x2": 200, "y2": 300,
            },
            order_marker_geometry={
                "anchor_band": {"y": 300, "h": 40},
                "full_anchor_x": [200, 800],
                "marker_y": 320,
                "one_item_offset": 100,
                "two_item_offsets": [70, 140],
                "state_radius": 20,
            },
            item_family_hint_slots=(
                {"bounds": {"x": 100, "y": 600, "w": 200, "h": 100}, "expected_level": 1},
            ),
        )
        live = calibration.scaled_to_screen((500, 3000))
        self.assertEqual(live.screen_size, (500, 3000))
        self.assertEqual(live.board_bounds, {"x": 50.0, "y": 600.0, "w": 350.0, "h": 1350.0})
        self.assertEqual(live.order_scroll_forward_action["x1"], 400.0)
        self.assertEqual(live.order_scroll_forward_action["y1"], 450.0)
        self.assertEqual(live.order_marker_geometry["marker_y"], 480.0)
        self.assertEqual(live.item_family_hint_slots[0]["bounds"]["y"], 900.0)

    def test_stable_order_id_changes_with_customer_or_requested_level(self):
        first = stable_order_id("customer-hash", (("A", 1), ("B", 2)))
        self.assertEqual(first, stable_order_id("customer-hash", (("A", 1), ("B", 2))))
        self.assertNotEqual(first, stable_order_id("other-customer", (("A", 1), ("B", 2))))
        self.assertNotEqual(first, stable_order_id("customer-hash", (("A", 1), ("B", 3))))


if __name__ == "__main__":
    unittest.main()
