import unittest

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


if __name__ == "__main__":
    unittest.main()
