import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from phone_harness.workflows.merge_boss import (
    MergeBossFastWorkflow,
    MergeBossLayout,
    _panel_similarity,
    producer_badge_scores,
)


class MergeBossWorkflowTests(unittest.TestCase):
    def test_panel_similarity_accepts_same_low_resolution_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            Image.new("RGB", (400, 100), "white").save(first)
            image = Image.new("RGB", (400, 100), "white")
            ImageDraw.Draw(image).rectangle((10, 10, 20, 20), fill="#fefefe")
            image.save(second)
            self.assertGreater(_panel_similarity(first, second), 0.99)

    def test_panel_confirmation_keeps_first_panel_pixels_across_action_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.jpg"
            second = Path(directory) / "second.jpg"
            Image.new("RGB", (200, 80), "white").save(first)
            Image.new("RGB", (200, 80), "white").save(second)

            class InvalidatingRuntime:
                def __init__(self):
                    self.observe_count = 0
                    self.act_count = 0

                def act(self, _actions, observation_id=None):
                    self.act_count += 1
                    # The second tap consumes the first panel observation and
                    # reproduces ScreenFrame.close() deleting its temp JPEG.
                    if self.observe_count == 1:
                        first.unlink(missing_ok=True)
                    return {"duration_ms": 1.0, "observation_id": observation_id}

                def observe(self, **_kwargs):
                    self.observe_count += 1
                    path = first if self.observe_count == 1 else second
                    return {
                        "observation_id": 10 + self.observe_count,
                        "_image_path": str(path),
                        "duration_ms": 1.0,
                    }

            runtime = InvalidatingRuntime()
            workflow = MergeBossFastWorkflow(
                runtime=runtime,
                layout=MergeBossLayout(rows=1, columns=2, board_bounds={"x": 0, "y": 0, "w": 200, "h": 100}),
            )
            ranked = {"cells": [{"center": (50, 50)}, {"center": (150, 50)}]}
            confirmed, evidence = workflow._confirm_pair_by_panel(
                {"observation_id": 1},
                ranked,
                {"cells": ["r0c0", "r0c1"]},
            )

            self.assertTrue(confirmed)
            self.assertGreater(evidence["panel_similarity"], 0.99)
            self.assertEqual(runtime.act_count, 2)
            self.assertEqual(runtime.observe_count, 2)

    def test_producer_badge_score_prefers_yellow_upper_right_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            image = Image.new("RGB", (200, 100), "#eeeeee")
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 20, 80, 80), fill="#3366cc")
            draw.ellipse((120, 20, 180, 80), fill="#3366cc")
            draw.polygon([(170, 5), (185, 5), (177, 25), (190, 25), (165, 50), (171, 29), (160, 29)], fill="#ffd21f")
            image.save(path)
            layout = MergeBossLayout(
                rows=1,
                columns=2,
                board_bounds={"x": 0.0, "y": 0.0, "w": 200.0, "h": 100.0},
            )
            scores = producer_badge_scores(path, layout=layout)

        self.assertGreater(scores["r0c1"], scores["r0c0"])
        self.assertGreater(scores["r0c1"], 0.025)

    def test_candidate_pairs_exclude_known_producer_cells(self):
        workflow = MergeBossFastWorkflow(runtime=object())
        ranked = {
            "pairs": [
                {"cells": ["r0c0", "r0c1"], "score": 0.99, "color_similarity": 0.99, "edge_similarity": 0.99},
                {"cells": ["r1c0", "r1c1"], "score": 0.94, "color_similarity": 0.96, "edge_similarity": 0.88},
            ]
        }
        strict, relaxed = workflow._candidate_pairs(ranked, {"r0c1"})
        self.assertEqual(strict, [])
        self.assertEqual(relaxed[0]["cells"], ["r1c0", "r1c1"])

    def test_candidate_pairs_exclude_empty_visual_twins(self):
        workflow = MergeBossFastWorkflow(runtime=object())
        ranked = {
            "cells": [
                {"id": "r0c0", "content_score": 0.8},
                {"id": "r0c1", "content_score": 1.1},
                {"id": "r1c0", "content_score": 12.0},
                {"id": "r1c1", "content_score": 13.0},
            ],
            "pairs": [
                {"cells": ["r0c0", "r0c1"], "score": 0.999, "color_similarity": 0.999, "edge_similarity": 0.999},
                {"cells": ["r1c0", "r1c1"], "score": 0.98, "color_similarity": 0.98, "edge_similarity": 0.98},
            ],
        }
        strict, relaxed = workflow._candidate_pairs(ranked, set(), content_threshold=6.0)
        self.assertEqual([pair["cells"] for pair in strict], [["r1c0", "r1c1"]])
        self.assertEqual(relaxed, [])

    def test_candidate_pairs_use_deep_occupancy_scores_over_glow_contaminated_cell_score(self):
        workflow = MergeBossFastWorkflow(runtime=object())
        ranked = {
            "cells": [
                {"id": "r0c0", "content_score": 18.0},
                {"id": "r0c1", "content_score": 22.0},
            ],
            "occupancy_scores": {"r0c0": 0.9, "r0c1": 1.2},
            "pairs": [
                {"cells": ["r0c0", "r0c1"], "score": 0.99, "color_similarity": 0.99, "edge_similarity": 0.99},
            ],
        }
        strict, relaxed = workflow._candidate_pairs(ranked, set(), content_threshold=6.0)
        self.assertEqual(strict, [])
        self.assertEqual(relaxed, [])

    def test_explicit_empty_cells_override_flat_item_content_scores(self):
        workflow = MergeBossFastWorkflow(runtime=object())
        ranked = {
            "cells": [
                {"id": "empty-a", "content_score": 0.8},
                {"id": "empty-b", "content_score": 0.7},
                {"id": "flat-item-a", "content_score": 0.5},
                {"id": "flat-item-b", "content_score": 0.4},
            ],
            "pairs": [
                {"cells": ["empty-a", "empty-b"], "score": 0.99, "color_similarity": 0.99, "edge_similarity": 0.99},
                {"cells": ["flat-item-a", "flat-item-b"], "score": 0.98, "color_similarity": 0.98, "edge_similarity": 0.98},
            ],
        }
        strict, relaxed = workflow._candidate_pairs(
            ranked,
            set(),
            content_threshold=6.0,
            empty_cells={"empty-a", "empty-b"},
        )
        self.assertEqual([pair["cells"] for pair in strict], [["flat-item-a", "flat-item-b"]])
        self.assertEqual(relaxed, [])

    def test_relaxed_candidates_keep_real_dense_board_boundary_for_panel_confirmation(self):
        workflow = MergeBossFastWorkflow(runtime=object())
        ranked = {
            "pairs": [
                {
                    "cells": ["r5c1", "r6c0"],
                    "score": 0.9090,
                    "color_similarity": 0.9402,
                    "edge_similarity": 0.8155,
                },
                {
                    "cells": ["r3c2", "r5c0"],
                    "score": 0.9077,
                    "color_similarity": 0.9225,
                    "edge_similarity": 0.8635,
                },
            ]
        }
        strict, relaxed = workflow._candidate_pairs(ranked, set())
        self.assertEqual(strict, [])
        self.assertEqual(
            [pair["cells"] for pair in relaxed],
            [["r5c1", "r6c0"], ["r3c2", "r5c0"]],
        )

    def test_preflight_requires_aliexpress_application_root(self):
        with patch(
            "phone_harness.workflows.merge_boss.windows.accessibility_elements",
            return_value=[{"role": "XCUIElementTypeApplication", "text": "OtherApp"}],
        ):
            self.assertFalse(MergeBossFastWorkflow._aliexpress_foreground())
        with patch(
            "phone_harness.workflows.merge_boss.windows.accessibility_elements",
            return_value=[{"role": "XCUIElementTypeApplication", "text": "AliExpress"}],
        ):
            self.assertTrue(MergeBossFastWorkflow._aliexpress_foreground())
