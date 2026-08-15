import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from phone_harness.visual_grid import analyze_grid


class VisualGridTests(unittest.TestCase):
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
