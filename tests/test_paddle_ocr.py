import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from phone_harness import paddle_ocr


class PaddleResultTests(unittest.TestCase):
    def test_normalize_result_returns_tap_ready_boxes(self):
        result = {
            "rec_texts": ["Settings"],
            "rec_scores": [0.96],
            "rec_polys": [[[100, 50], [300, 50], [300, 100], [100, 100]]],
        }
        window = {"x": 0, "y": 0, "w": 900, "h": 300}
        boxes = paddle_ocr.normalize_result(result, (900, 300), window)

        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertEqual(box["text"], "Settings")
        self.assertAlmostEqual(box["confidence"], 0.96)
        self.assertEqual(box["center"], {"x": 200.0, "y": 75.0})
        self.assertEqual(box["bbox"], {"x": 100.0, "y": 50.0, "w": 200.0, "h": 50.0})
        self.assertEqual(box["x"], 200.0)
        self.assertEqual(box["y"], 75.0)
        self.assertEqual(box["w"], 200.0)
        self.assertEqual(box["h"], 50.0)

    def test_normalize_result_skips_empty_text(self):
        result = {
            "rec_texts": [""],
            "rec_scores": [0.99],
            "rec_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]]],
        }
        self.assertEqual(
            paddle_ocr.normalize_result(result, (100, 100), {"x": 0, "y": 0, "w": 100, "h": 100}),
            [],
        )

    def test_inference_image_downscales_large_screenshot_to_safe_long_edge(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (1206, 2622), "white").save(source)

            with paddle_ocr._inference_image(source) as prepared:
                self.assertNotEqual(prepared, source)
                with Image.open(prepared) as image:
                    self.assertEqual(image.size, (736, 1600))
                prepared_path = prepared

            self.assertFalse(prepared_path.exists())
            self.assertTrue(source.exists())

    def test_inference_image_keeps_small_screenshot_without_temp_copy(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (800, 1200), "white").save(source)

            with paddle_ocr._inference_image(source) as prepared:
                self.assertEqual(prepared, source)

            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
