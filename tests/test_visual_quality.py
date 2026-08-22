import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from phone_harness.frame import ScreenFrame
from phone_harness.visual_quality import (
    VisualProfile,
    benchmark_grid_candidate_recall,
    benchmark_grid_identity,
    benchmark_visual_profiles,
)


class VisualQualityTests(unittest.TestCase):
    def test_visual_profiles_report_smaller_candidates_and_structure_metrics(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            image = Image.new("RGB", (800, 1200), "white")
            draw = ImageDraw.Draw(image)
            for index in range(20):
                draw.rectangle((20 + index * 25, 50, 35 + index * 25, 1100), fill=(index * 11 % 255, 80, 160))
            image.save(source)
            frame = ScreenFrame.from_path(source)
            try:
                result = benchmark_visual_profiles(
                    frame,
                    profiles=(VisualProfile("test", 256, "JPEG", 30),),
                )
            finally:
                frame.close()

            profile = result["profiles"][0]
            self.assertLess(profile["byte_ratio"], 1.0)
            self.assertGreater(profile["low_frequency_similarity"], 0.8)
            self.assertGreater(profile["edge_similarity"], 0.6)

    def test_grid_identity_benchmark_detects_group_preservation(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "grid.png"
            image = Image.new("RGB", (600, 600), "white")
            draw = ImageDraw.Draw(image)
            # Two identical high-contrast icons in the first row, plus distinct
            # content elsewhere.  Use large shapes so coarse profiles should
            # preserve the same-looking grouping.
            for left in (20, 220):
                draw.ellipse((left + 30, 30, left + 150, 150), fill="black")
                draw.rectangle((left + 70, 65, left + 110, 115), fill="white")
            draw.rectangle((430, 40, 560, 160), fill="gray")
            draw.polygon([(50, 350), (150, 230), (180, 380)], fill="navy")
            draw.rectangle((250, 250, 360, 360), fill="green")
            draw.ellipse((430, 240, 560, 370), fill="orange")
            image.save(source)
            frame = ScreenFrame.from_path(source)
            try:
                result = benchmark_grid_identity(
                    frame,
                    rows=2,
                    columns=3,
                    bounds={"x": 0, "y": 0, "w": 600, "h": 600},
                    inset=0.12,
                    exact_threshold=0.93,
                    profiles=(VisualProfile("test", 384, "JPEG", 35),),
                )
            finally:
                frame.close()

            self.assertIn(["r0c0", "r0c1"], result["reference_groups"])
            self.assertTrue(result["profiles"][0]["groups_preserved"])

    def test_grid_candidate_recall_can_preserve_reference_pair_with_small_shortlist(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "grid.png"
            image = Image.new("RGB", (600, 600), "white")
            draw = ImageDraw.Draw(image)
            for left in (20, 220):
                draw.ellipse((left + 30, 30, left + 150, 150), fill="black")
                draw.rectangle((left + 70, 65, left + 110, 115), fill="white")
            draw.rectangle((430, 40, 560, 160), fill="gray")
            draw.polygon([(50, 350), (150, 230), (180, 380)], fill="navy")
            draw.rectangle((250, 250, 360, 360), fill="green")
            draw.ellipse((430, 240, 560, 370), fill="orange")
            image.save(source)
            frame = ScreenFrame.from_path(source)
            try:
                result = benchmark_grid_candidate_recall(
                    frame,
                    rows=2,
                    columns=3,
                    bounds={"x": 0, "y": 0, "w": 600, "h": 600},
                    inset=0.12,
                    exact_threshold=0.93,
                    neighbor_counts=(1, 2),
                    profiles=(VisualProfile("test", 256, "JPEG", 28),),
                )
            finally:
                frame.close()

            first = result["profiles"][0]["neighbor_results"][0]
            self.assertEqual(first["reference_pair_recall"], 1.0)
            self.assertLess(first["candidate_fraction"], 1.0)
