import unittest
from unittest.mock import patch

from phone_harness import helpers


class TextTargetingTests(unittest.TestCase):
    def test_find_text_can_require_exact_match(self):
        boxes = [
            {"text": "Settings", "confidence": 0.9},
            {"text": "Settings & Privacy", "confidence": 0.9},
        ]
        with patch.object(helpers, "ocr", return_value=boxes):
            self.assertEqual(helpers.find_text("Settings", exact=True), [boxes[0]])

    def test_tap_text_rejects_ambiguous_target_by_default(self):
        boxes = [
            {"text": "Settings", "x": 10, "y": 20},
            {"text": "Settings", "x": 30, "y": 40},
        ]
        with patch.object(helpers, "find_text", return_value=boxes), patch.object(helpers, "tap") as tap:
            with self.assertRaises(RuntimeError):
                helpers.tap_text("Settings")
        tap.assert_not_called()

    def test_tap_text_allows_explicit_index_for_ambiguity(self):
        boxes = [
            {"text": "Settings", "x": 10, "y": 20},
            {"text": "Settings", "x": 30, "y": 40},
        ]
        with patch.object(helpers, "find_text", return_value=boxes), patch.object(helpers, "tap") as tap:
            hit = helpers.tap_text("Settings", index=1)
        self.assertIs(hit, boxes[1])
        tap.assert_called_once_with(30, 40)


if __name__ == "__main__":
    unittest.main()
