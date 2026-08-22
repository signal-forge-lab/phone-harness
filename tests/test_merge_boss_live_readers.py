import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from phone_harness.visual_grid import describe_grid_cells
from phone_harness.workflows.merge_boss_live_readers import (
    MergeBossBoardImageReader,
    _filter_known_semantic_mismatches,
    _order_customer_base,
    MergeBossOrderImageReader,
    _order_anchor_centers,
    _order_marker_state,
    order_item_descriptor,
)
from phone_harness.workflows.merge_boss_strategy import MergeBossBoardItem


class MergeBossOrderPagingTests(unittest.TestCase):
    def test_partial_final_page_reuses_previous_last_customer_slot(self):
        geometry = {
            "customers_per_full_page": 2,
            "full_page_anchor_reference": [360, 762],
            "partial_end_shift_min": 40,
            "partial_end_overlap": 1,
        }
        self.assertEqual(_order_customer_base(0, (360, 762), geometry), 0)
        self.assertEqual(_order_customer_base(1, (363, 765), geometry), 2)
        self.assertEqual(_order_customer_base(2, (424, 826), geometry), 3)
        self.assertEqual(_order_customer_base(2, (361, 763), geometry), 4)


class MergeBossSemanticMergeFilterTests(unittest.TestCase):
    def test_known_different_levels_are_not_visual_merge_candidates(self):
        pairs = (("r0c0", "r0c1"), ("r1c0", "r1c1"))
        items = (
            MergeBossBoardItem("r0c0", "fishing-line-lv1", 1),
            MergeBossBoardItem("r0c1", "fishing-line-lv2", 2),
            MergeBossBoardItem("r1c0", "visual:aaa", 0),
            MergeBossBoardItem("r1c1", "visual:bbb", 0),
        )

        kept, rejected = _filter_known_semantic_mismatches(pairs, items)

        self.assertEqual(kept, (("r1c0", "r1c1"),))
        self.assertEqual(rejected, (("r0c0", "r0c1"),))
from phone_harness.visual_descriptor import descriptor_similarity
from phone_harness.workflows.merge_boss_perception import MergeBossPerceptionCalibration


class FakeRuntime:
    def __init__(self, image_path):
        self.image_path = str(image_path)
        self.calls = []

    def observe(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "observation_id": 7,
            "_image_path": self.image_path,
            "capture_ms": 10.0,
            "duration_ms": 12.0,
        }


class SequencedRuntime:
    def __init__(self, paths):
        self.paths = [str(path) for path in paths]
        self.observe_index = 0
        self.actions = []

    def observe(self, **kwargs):
        path = self.paths[min(self.observe_index, len(self.paths) - 1)]
        self.observe_index += 1
        return {
            "observation_id": self.observe_index,
            "_image_path": path,
            "capture_ms": 1.0,
            "duration_ms": 1.0,
        }

    def act(self, actions, observation_id=None):
        self.actions.append((list(actions), observation_id))
        return {"duration_ms": 1.0}


class MergeBossBoardImageReaderTests(unittest.TestCase):
    def test_order_reader_skips_unknown_customer_instead_of_blocking_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "orders.png"
            Image.new("RGB", (1206, 700), (240, 240, 230)).save(image_path)
            geometry = {
                "anchor_band": {"y": 525, "h": 50},
                "full_anchor_x": [200, 1000],
                "marker_y": 511,
                "one_item_offset": 206,
                "two_item_offsets": [134, 285],
                "state_radius": 26,
                "blue_min": 0.35,
                "green_min": 0.20,
                "order_match_min_score": 0.99,
                "order_match_min_margin": 0.05,
                "customers_per_full_page": 2,
                "full_page_anchor_reference": [360, 762],
                "partial_end_shift_min": 40,
                "partial_end_overlap": 1,
            }

            class UnknownCatalog:
                def rank_order_items(self, _descriptor, *, limit=2):
                    return []

            calibration = MergeBossPerceptionCalibration(
                calibrated=True,
                screen_size=(1206, 700),
                order_marker_geometry=geometry,
            )
            reader = MergeBossOrderImageReader(catalog_path=root / "unused.json")
            with (
                patch(
                    "phone_harness.workflows.merge_boss_live_readers.MergeBossCatalog.load",
                    return_value=UnknownCatalog(),
                ),
                patch(
                    "phone_harness.workflows.merge_boss_live_readers._order_anchor_centers",
                    return_value=(360.0,),
                ),
                patch(
                    "phone_harness.workflows.merge_boss_live_readers._order_marker_state",
                    return_value="missing",
                ),
            ):
                result = reader(FakeRuntime(image_path), calibration, 0)

        self.assertEqual(result.cards, ())

    def test_producer_confidence_defaults_are_separate_from_item_confidence(self):
        reader = MergeBossBoardImageReader()
        self.assertEqual(reader.item_min_score, 0.93)
        self.assertEqual(reader.min_margin, 0.015)
        self.assertEqual(reader.producer_min_score, 0.89)
        self.assertEqual(reader.producer_min_margin, 0.05)
        self.assertEqual(reader.max_relaxed_checks, 3)

    def test_producer_cells_remain_stable_across_one_frame_template_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "board.png"
            Image.new("RGB", (700, 900), (230, 220, 190)).save(image_path)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            reader = MergeBossBoardImageReader(catalog_path=catalog_path)
            calibration = MergeBossPerceptionCalibration(
                calibrated=True,
                screen_size=(700, 900),
                board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
            )
            with patch.object(
                MergeBossBoardImageReader,
                "_template_producer_cells",
                side_effect=[{"r8c5", "r8c6"}, {"r8c5"}],
            ):
                first = reader(FakeRuntime(image_path), calibration)
                second = reader(FakeRuntime(image_path), calibration)

        self.assertEqual({producer.cell_id for producer in first.producers}, {"r8c5", "r8c6"})
        self.assertEqual({producer.cell_id for producer in second.producers}, {"r8c5", "r8c6"})

    def test_color_fallback_uses_full_cell_producer_visuals_when_catalog_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "board.png"
            image = Image.new("RGB", (700, 900), (230, 220, 190))
            draw = ImageDraw.Draw(image)
            # Two producer cells and one yellow distractor item all satisfy the
            # old color-only fallback.
            for column in (5, 6):
                left = column * 100
                top = 800
                draw.rectangle((left + 18, top + 22, left + 76, top + 80), fill=(50, 110, 210))
                draw.polygon(
                    [(left + 72, top + 4), (left + 92, top + 4), (left + 77, top + 38), (left + 62, top + 38)],
                    fill=(255, 210, 20),
                )
            draw.ellipse((18, 18, 82, 82), fill=(240, 190, 20))
            draw.rectangle((70, 4, 96, 32), fill=(255, 210, 20))
            image.save(image_path)

            grid = describe_grid_cells(
                image_path,
                rows=9,
                columns=7,
                bounds={"x": 0, "y": 0, "w": 700, "h": 900},
                inset=0.14,
            )
            by_id = {cell["id"]: cell for cell in grid["cells"]}
            image.close()

            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [{
                    "identity": "Known Producer",
                    "level": 8,
                    "visual_descriptor": by_id["r8c6"]["visual_descriptor"],
                    "outputs_complete": False,
                    "possible_outputs": [],
                }],
            }), encoding="utf-8")
            calibration = MergeBossPerceptionCalibration(
                calibrated=True,
                screen_size=(700, 900),
                board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
            )
            result = MergeBossBoardImageReader(catalog_path=catalog_path)(
                FakeRuntime(image_path), calibration
            )

        self.assertEqual({producer.cell_id for producer in result.producers}, {"r8c5", "r8c6"})

    def test_relaxed_visual_pair_is_panel_confirmed_when_strict_edge_match_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.png"
            panel_one = root / "panel-one.png"
            panel_two = root / "panel-two.png"
            image = Image.new("RGB", (700, 900), (230, 220, 190))
            draw = ImageDraw.Draw(image)
            # Two same-color items with a tiny animation-like edge displacement.
            draw.ellipse((18, 18, 82, 82), fill=(220, 70, 50))
            draw.line((35, 25, 35, 75), fill=(40, 40, 40), width=3)
            draw.ellipse((118, 18, 182, 82), fill=(220, 70, 50))
            draw.line((139, 25, 139, 75), fill=(40, 40, 40), width=3)
            # Two known producer-badge cells keep board recognition valid.
            for column in (5, 6):
                left = column * 100
                top = 800
                draw.rectangle((left + 18, top + 22, left + 76, top + 80), fill=(50, 110, 210))
                draw.polygon(
                    [(left + 72, top + 4), (left + 92, top + 4), (left + 77, top + 38), (left + 62, top + 38)],
                    fill=(255, 210, 20),
                )
            image.save(board)
            image.close()
            Image.new("RGB", (240, 80), (245, 245, 245)).save(panel_one)
            Image.new("RGB", (240, 80), (245, 245, 245)).save(panel_two)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            runtime = SequencedRuntime([board, panel_one, panel_two])
            result = MergeBossBoardImageReader(
                catalog_path=catalog_path,
                max_relaxed_checks=3,
            )(
                runtime,
                MergeBossPerceptionCalibration(
                    calibrated=True,
                    screen_size=(700, 900),
                    board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
                ),
            )

        self.assertIn(("r0c0", "r0c1"), result.visual_merge_pairs)
        self.assertGreaterEqual(len(runtime.actions), 2)
        self.assertGreater(result.observation_id, 1)

    def test_full_board_escalates_to_exhaustive_pair_search_before_returning_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.png"
            image = Image.new("RGB", (700, 900), (230, 220, 190))
            draw = ImageDraw.Draw(image)
            for row in range(9):
                for column in range(7):
                    left = column * 100
                    top = row * 100
                    draw.ellipse(
                        (left + 18, top + 18, left + 82, top + 82),
                        fill=(80 + row * 5, 80 + column * 5, 140),
                    )
            image.save(board)
            image.close()

            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            reader = MergeBossBoardImageReader(
                catalog_path=catalog_path,
                max_relaxed_checks=3,
            )
            runtime = FakeRuntime(board)
            calibration = MergeBossPerceptionCalibration(
                calibrated=True,
                screen_size=(700, 900),
                board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
            )
            exhaustive = {
                "rows": 9,
                "columns": 7,
                "bounds": calibration.board_bounds,
                "cells": [],
                "pairs": [{
                    "cells": ["r0c0", "r0c1"],
                    "score": 0.89,
                    "color_similarity": 0.92,
                    "edge_similarity": 0.80,
                }],
                "candidate_pair_count": 1,
                "full_pair_count": 1,
                "detail_comparison_fraction": 1.0,
            }
            with (
                patch(
                    "phone_harness.workflows.merge_boss_live_readers.rank_grid_pairs_coarse_to_fine",
                    return_value={
                        "rows": 9,
                        "columns": 7,
                        "bounds": calibration.board_bounds,
                        "cells": [],
                        "pairs": [],
                        "candidate_pair_count": 0,
                        "full_pair_count": 1,
                        "detail_comparison_fraction": 0.0,
                    },
                ),
                patch.object(
                    MergeBossBoardImageReader,
                    "_template_producer_cells",
                    return_value={"r8c5", "r8c6"},
                ),
                patch.object(
                    reader,
                    "_jam_recovery_ranked_grid",
                    return_value=exhaustive,
                ) as jam_search,
                patch(
                    "phone_harness.workflows.merge_boss_live_readers.MergeBossFastWorkflow._confirm_pair_by_panel",
                    return_value=(True, {"observation_id": 7}),
                ),
            ):
                result = reader(runtime, calibration)

        jam_search.assert_called_once()
        self.assertIn(("r0c0", "r0c1"), result.visual_merge_pairs)

    def test_order_item_descriptor_ignores_marker_and_pale_card_background(self):
        first = Image.new("RGB", (180, 180), (245, 245, 240))
        second = Image.new("RGB", (180, 180), (220, 240, 250))
        for image in (first, second):
            draw = ImageDraw.Draw(image)
            draw.ellipse((55, 45, 115, 105), fill=(220, 70, 50))
            # Different marker colors live outside the above-left crop.
        d1 = order_item_descriptor(first, (140, 140))
        d2 = order_item_descriptor(second, (140, 140))
        self.assertGreater(descriptor_similarity(d1, d2)["score"], 0.99)

    def test_order_anchor_and_marker_geometry_reads_one_and_two_item_cards(self):
        cv2 = __import__("cv2")
        np = __import__("numpy")
        image = Image.new("RGB", (1206, 700), (240, 240, 230))
        draw = ImageDraw.Draw(image)
        # Two green halves for each card anchor.
        for anchor in (360, 760):
            draw.rectangle((anchor - 34, 528, anchor - 12, 568), fill=(70, 210, 50))
            draw.rectangle((anchor + 8, 525, anchor + 30, 565), fill=(70, 210, 50))
        # First card: two missing items. Second card: one missing item.
        draw.ellipse((360 + 134 - 20, 491, 360 + 134 + 20, 531), fill=(20, 140, 230))
        draw.ellipse((360 + 285 - 20, 491, 360 + 285 + 20, 531), fill=(20, 140, 230))
        draw.ellipse((760 + 206 - 20, 491, 760 + 206 + 20, 531), fill=(20, 140, 230))
        geometry = {
            "anchor_band": {"y": 525, "h": 50},
            "full_anchor_x": [200, 1000],
            "marker_y": 511,
            "one_item_offset": 206,
            "two_item_offsets": [134, 285],
            "state_radius": 26,
            "blue_min": 0.35,
            "green_min": 0.20,
        }
        anchors = _order_anchor_centers(image, geometry)
        self.assertEqual(len(anchors), 2)
        hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
        self.assertEqual(_order_marker_state(hsv, anchors[0] + 134, 511, geometry), "missing")
        self.assertEqual(_order_marker_state(hsv, anchors[0] + 285, 511, geometry), "missing")
        self.assertEqual(_order_marker_state(hsv, anchors[1] + 206, 511, geometry), "missing")

    def test_reads_known_item_known_producer_and_free_cells_from_one_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "board.png"
            image = Image.new("RGB", (700, 900), (230, 220, 190))
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 20, 80, 80), fill=(210, 50, 50))
            draw.rectangle((520, 820, 580, 880), fill=(80, 140, 190))
            draw.polygon([(570, 805), (590, 805), (575, 840), (560, 840)], fill=(255, 210, 20))
            # r8c6 producer body + upper-right yellow badge evidence.
            draw.rectangle((620, 820, 680, 880), fill=(40, 100, 210))
            draw.polygon([(670, 805), (690, 805), (675, 840), (660, 840)], fill=(255, 210, 20))
            image.save(image_path)
            image.close()

            grid = describe_grid_cells(
                image_path,
                rows=9,
                columns=7,
                bounds={"x": 0, "y": 0, "w": 700, "h": 900},
                inset=0.14,
            )
            by_id = {cell["id"]: cell for cell in grid["cells"]}
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "known",
                    "complete": False,
                    "levels": [{
                        "level": 1,
                        "identity": "known-lv1",
                        "visual_descriptor": by_id["r0c0"]["visual_descriptor"],
                    }],
                }],
                "merge_transitions": [],
                "producers": [{
                    "identity": "Known Producer",
                    "level": 8,
                    "visual_descriptor": by_id["r8c6"]["visual_descriptor"],
                    "outputs_complete": False,
                    "possible_outputs": [],
                }],
            }), encoding="utf-8")

            reader = MergeBossBoardImageReader(
                catalog_path=catalog_path,
                item_min_score=0.99,
                producer_min_score=0.89,
            )
            result = reader(
                FakeRuntime(image_path),
                MergeBossPerceptionCalibration(
                    calibrated=True,
                    screen_size=(700, 900),
                    board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
                ),
            )

        self.assertEqual(result.observation_id, 7)
        self.assertEqual(result.items[0].cell_id, "r0c0")
        self.assertEqual(result.items[0].identity, "known-lv1")
        self.assertEqual(result.items[0].level, 1)
        known_producer = next(item for item in result.producers if item.cell_id == "r8c6")
        self.assertEqual(known_producer.identity, "Known Producer")
        self.assertEqual(known_producer.level, 8)
        self.assertEqual(result.free_cells, 60)
        self.assertIsNone(result.energy)

    def test_unknown_nonempty_item_stays_occupied_but_semantically_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "board.png"
            image = Image.new("RGB", (700, 900), (230, 220, 190))
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 20, 80, 80), fill=(210, 50, 50))
            draw.rectangle((520, 820, 580, 880), fill=(80, 140, 190))
            draw.polygon([(570, 805), (590, 805), (575, 840), (560, 840)], fill=(255, 210, 20))
            draw.rectangle((620, 820, 680, 880), fill=(40, 100, 210))
            draw.polygon([(670, 805), (690, 805), (675, 840), (660, 840)], fill=(255, 210, 20))
            image.save(image_path)
            image.close()
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")

            result = MergeBossBoardImageReader(catalog_path=catalog_path)(
                FakeRuntime(image_path),
                MergeBossPerceptionCalibration(
                    calibrated=True,
                    screen_size=(700, 900),
                    board_bounds={"x": 0, "y": 0, "w": 700, "h": 900},
                ),
            )

        unknown = next(item for item in result.items if item.cell_id == "r0c0")
        self.assertTrue(unknown.identity.startswith("visual:"))
        self.assertEqual(unknown.level, 0)


if __name__ == "__main__":
    unittest.main()
