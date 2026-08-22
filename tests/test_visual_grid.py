import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from phone_harness.visual_grid import (
    analyze_grid,
    analyze_grid_coarse_to_fine,
    compare_grid_frames,
    compare_grid_temporal,
    rank_grid_pairs,
    rank_grid_pairs_coarse_to_fine,
    describe_grid_cells,
    select_non_overlapping_pairs,
)


class VisualGridTests(unittest.TestCase):
    def test_select_non_overlapping_pairs_respects_rank_and_exclusions(self):
        pairs = [
            {"cells": ["a", "b"], "score": 0.99},
            {"cells": ["a", "c"], "score": 0.98},
            {"cells": ["d", "e"], "score": 0.97},
            {"cells": ["f", "g"], "score": 0.96},
        ]
        selected = select_non_overlapping_pairs(
            pairs, excluded_cells={"f"}, limit=2
        )
        self.assertEqual([pair["cells"] for pair in selected], [["a", "b"], ["d", "e"]])

    def test_describe_grid_cells_returns_compact_descriptors_in_one_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.png"
            image = Image.new("RGB", (200, 100))
            image.paste((255, 0, 0), (0, 0, 100, 100))
            image.paste((0, 0, 255), (100, 0, 200, 100))
            image.save(path)
            image.close()
            result = describe_grid_cells(
                path,
                rows=1,
                columns=2,
                bounds={"x": 0, "y": 0, "w": 200, "h": 100},
                inset=0.1,
            )
        self.assertEqual([cell["id"] for cell in result["cells"]], ["r0c0", "r0c1"])
        self.assertEqual(result["cells"][0]["visual_descriptor"]["size"], [8, 8])
        self.assertNotEqual(
            result["cells"][0]["visual_descriptor"]["rgb4_b64"],
            result["cells"][1]["visual_descriptor"]["rgb4_b64"],
        )
    def test_rank_grid_pairs_keeps_color_and_edge_as_candidate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.png"
            image = Image.new("RGB", (300, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 20, 80, 80), fill="#ee8844")
            draw.ellipse((120, 20, 180, 80), fill="#ee8844")
            draw.rectangle((220, 20, 280, 80), fill="#3366cc")
            image.save(path)

            result = rank_grid_pairs(
                path,
                rows=1,
                columns=3,
                bounds={"x": 0, "y": 0, "w": 300, "h": 100},
                inset=0.1,
            )

        self.assertEqual(result["pairs"][0]["cells"], ["r0c0", "r0c1"])
        self.assertGreater(result["pairs"][0]["color_similarity"], 0.99)
        self.assertGreater(result["pairs"][0]["edge_similarity"], 0.99)
    def test_coarse_to_fine_preserves_detail_groups_with_fewer_detail_comparisons(self):
        with TemporaryDirectory() as directory:
            detail = Path(directory) / "detail.png"
            coarse = Path(directory) / "coarse.jpg"
            image = Image.new("RGB", (600, 600), "white")
            draw = ImageDraw.Draw(image)
            for left in (20, 220):
                draw.ellipse((left + 30, 30, left + 150, 150), fill="black")
                draw.rectangle((left + 70, 65, left + 110, 115), fill="white")
            draw.rectangle((430, 40, 560, 160), fill="gray")
            draw.polygon([(50, 350), (150, 230), (180, 380)], fill="navy")
            draw.rectangle((250, 250, 360, 360), fill="green")
            draw.ellipse((430, 240, 560, 370), fill="orange")
            image.save(detail)
            image.resize((256, 256), Image.Resampling.LANCZOS).save(
                coarse, format="JPEG", quality=28
            )

            reference = analyze_grid(
                detail,
                rows=2,
                columns=3,
                bounds={"x": 0, "y": 0, "w": 600, "h": 600},
                inset=0.12,
                exact_threshold=0.93,
            )
            result = analyze_grid_coarse_to_fine(
                detail,
                coarse,
                rows=2,
                columns=3,
                detail_bounds={"x": 0, "y": 0, "w": 600, "h": 600},
                coarse_bounds={"x": 0, "y": 0, "w": 256, "h": 256},
                inset=0.12,
                exact_threshold=0.93,
                candidate_neighbors=2,
            )

            self.assertEqual(result["exact_groups"], reference["exact_groups"])
            self.assertLess(result["detail_comparison_count"], result["full_pair_count"])
            self.assertLess(result["detail_comparison_fraction"], 1.0)

    def test_temporal_compare_ignores_preexisting_animation_but_keeps_material_change(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            before_a = directory / "before-a.png"
            before_b = directory / "before-b.png"
            after = directory / "after.png"

            first = Image.new("RGB", (200, 100), "#dddddd")
            draw = ImageDraw.Draw(first)
            draw.rectangle((20, 20, 80, 80), fill="#33aa66")
            draw.rectangle((120, 20, 180, 80), fill="#ee7744")
            first.save(before_a)

            second = first.copy()
            ImageDraw.Draw(second).rectangle((120, 20, 180, 80), fill="#dd6633")
            second.save(before_b)

            final = first.copy()
            draw = ImageDraw.Draw(final)
            draw.rectangle((20, 20, 80, 80), fill="#4455cc")
            draw.rectangle((120, 20, 180, 80), fill="#ee7744")
            final.save(after)

            result = compare_grid_temporal(
                [before_a, before_b],
                [after],
                rows=1,
                columns=2,
                bounds={"x": 0, "y": 0, "w": 200, "h": 100},
                material_margin=0.03,
            )

        self.assertEqual(result["material_changed_cells"], ["r0c0"])
        self.assertEqual(result["ambient_only_cells"], ["r0c1"])
        static = result["cells"][0]
        animated = result["cells"][1]
        self.assertTrue(static["material_changed"])
        self.assertFalse(animated["material_changed"])
        self.assertLess(static["post_best_similarity"], static["material_threshold"])
        self.assertGreaterEqual(animated["post_best_similarity"], animated["material_threshold"])

    def test_temporal_compare_requires_at_least_two_baseline_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            Image.new("RGB", (100, 100), "white").save(path)
            with self.assertRaisesRegex(ValueError, "at least two baseline"):
                compare_grid_temporal(
                    [path],
                    [path],
                    rows=1,
                    columns=1,
                    bounds={"x": 0, "y": 0, "w": 100, "h": 100},
                )

    def test_compare_frames_reports_only_changed_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            before_path = Path(directory) / "before.png"
            after_path = Path(directory) / "after.png"
            before = Image.new("RGB", (200, 100), "#dddddd")
            draw = ImageDraw.Draw(before)
            draw.rectangle((20, 20, 80, 80), fill="#33aa66")
            draw.rectangle((120, 20, 180, 80), fill="#4477cc")
            before.save(before_path)

            after = before.copy()
            ImageDraw.Draw(after).rectangle((120, 20, 180, 80), fill="#cc7744")
            after.save(after_path)

            result = compare_grid_frames(
                before_path,
                after_path,
                rows=1,
                columns=2,
                bounds={"x": 0, "y": 0, "w": 200, "h": 100},
            )

        self.assertEqual(result["changed_cells"], ["r0c1"])
        self.assertEqual(result["unchanged_cells"], ["r0c0"])
        self.assertLess(result["cells"][1]["color_similarity"], result["stable_threshold"])

    def test_groups_only_visually_identical_nonempty_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            image = Image.new("RGB", (280, 180), "#e8dfc2")
            draw = ImageDraw.Draw(image)
            # 2 rows x 4 columns. The first pair is identical, the third is
            # deliberately similar but not identical, and the rest are empty.
            draw.rectangle((15, 15, 55, 75), fill="#e94f65")
            draw.ellipse((24, 28, 46, 58), fill="#ffffff")
            draw.rectangle((85, 15, 125, 75), fill="#e94f65")
            draw.ellipse((94, 28, 116, 58), fill="#ffffff")
            draw.rectangle((155, 15, 195, 75), fill="#e94f65")
            draw.ellipse((164, 28, 186, 58), fill="#f7e8e8")
            image.save(path)

            result = analyze_grid(
                path,
                rows=2,
                columns=4,
                bounds={"x": 0, "y": 0, "w": 280, "h": 180},
            )

        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["columns"], 4)
        self.assertEqual(result["cells"][0]["center"], [35.0, 45.0])
        self.assertEqual(result["exact_groups"], [["r0c0", "r0c1"]])
        self.assertEqual(len(result["exact_pairs"]), 1)
        self.assertGreaterEqual(result["exact_pairs"][0]["color_similarity"], 0.995)
        self.assertIn("perceptual_hash", result["cells"][0])
        self.assertNotEqual(result["cells"][0]["visual_signature"], result["cells"][2]["visual_signature"])

    def test_inset_ignores_cell_border_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            image = Image.new("RGB", (200, 100), "#dddddd")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 80, 80), fill="#33aa66")
            draw.rectangle((120, 20, 180, 80), fill="#33aa66")
            draw.rectangle((0, 0, 5, 99), fill="#000000")
            draw.rectangle((194, 0, 199, 99), fill="#ffffff")
            image.save(path)

            result = analyze_grid(
                path,
                rows=1,
                columns=2,
                bounds={"x": 0, "y": 0, "w": 200, "h": 100},
                inset=0.18,
            )

        self.assertEqual(result["exact_groups"], [["r0c0", "r0c1"]])

    def test_rejects_invalid_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            Image.new("RGB", (100, 100), "white").save(path)
            with self.assertRaises(ValueError):
                analyze_grid(path, rows=9, columns=7, bounds={"x": 90, "y": 0, "w": 20, "h": 100})

    def test_rejects_unbounded_grid_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            Image.new("RGB", (100, 100), "white").save(path)
            with self.assertRaises(ValueError):
                analyze_grid(path, rows=21, columns=20, bounds={"x": 0, "y": 0, "w": 100, "h": 100})

    def test_exact_groups_require_every_pair_to_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.png"
            image = Image.new("RGB", (300, 100), "#dddddd")
            draw = ImageDraw.Draw(image)
            for index, color in enumerate(("#ff0000", "#fd0000", "#fb0000")):
                left = index * 100 + 20
                draw.rectangle((left, 20, left + 60, 80), fill=color)
            image.save(path)

            result = analyze_grid(
                path,
                rows=1,
                columns=3,
                bounds={"x": 0, "y": 0, "w": 300, "h": 100},
                exact_threshold=0.997,
                min_content_score=0,
            )

        pairs = {tuple(pair["cells"]) for pair in result["exact_pairs"]}
        self.assertIn(("r0c0", "r0c1"), pairs)
        self.assertIn(("r0c1", "r0c2"), pairs)
        self.assertNotIn(("r0c0", "r0c2"), pairs)
        self.assertEqual(result["exact_groups"], [])


if __name__ == "__main__":
    unittest.main()
