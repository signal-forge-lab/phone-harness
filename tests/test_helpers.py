import unittest
import sys
from unittest.mock import patch

from phone_harness import helpers


class TextTargetingTests(unittest.TestCase):
    def test_elements_prefers_windows_accessibility(self):
        boxes = [{"text": "8", "confidence": 1.0, "source": "accessibility"}]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=boxes, create=True), \
                patch.object(helpers.mirror, "capture") as capture:
            self.assertEqual(helpers.elements(), boxes)
        capture.assert_not_called()

    def test_elements_falls_back_to_ocr_when_accessibility_unavailable(self):
        boxes = [{"text": "8", "confidence": 0.9}]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", side_effect=RuntimeError("no WDA"), create=True), \
                patch.object(helpers.mirror, "capture", return_value=("screen.png", {})), \
                patch.object(helpers._ocr, "recognize", return_value=boxes):
            self.assertEqual(helpers.elements(), boxes)

    def test_find_text_rejects_empty_query_without_ocr(self):
        with patch.object(helpers, "ocr") as ocr:
            with self.assertRaises(ValueError):
                helpers.find_text("")
        ocr.assert_not_called()

    def test_tap_text_rejects_empty_query_without_observing(self):
        with patch.object(helpers, "ocr") as ocr:
            with self.assertRaises(ValueError):
                helpers.tap_text("")
        ocr.assert_not_called()

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
        with patch.object(helpers, "ocr", return_value=boxes), patch.object(helpers, "tap") as tap:
            hit = helpers.tap_text("Settings", index=1)
        self.assertIs(hit, boxes[1])
        tap.assert_called_once_with(30, 40)

    def test_tap_text_uses_accessibility_selector_for_unique_windows_hit(self):
        box = {
            "text": "8", "x": 10, "y": 20, "source": "accessibility",
            "name": "eight", "label": "8",
        }
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers, "ocr", return_value=[box]), \
                patch.object(helpers.mirror, "tap_accessibility", create=True) as tap_element, \
                patch.object(helpers, "tap") as tap:
            hit = helpers.tap_text("8", exact=True)
        self.assertIs(hit, box)
        tap_element.assert_called_once_with(box)
        tap.assert_not_called()

class ScrollDetectionTests(unittest.TestCase):
    def test_empty_screens_are_identical(self):
        self.assertEqual(helpers._overlap(frozenset(), frozenset()), 1.0)

    def test_one_empty_screen_means_no_overlap(self):
        self.assertEqual(helpers._overlap(frozenset({"Settings"}), frozenset()), 0.0)


class AppLaunchTests(unittest.TestCase):
    def test_windows_open_app_defers_readiness_to_next_observation(self):
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "open_app", create=True) as open_app, \
                patch.object(helpers, "wait_stable") as wait_stable:
            helpers.open_app("Calculator")

        open_app.assert_called_once_with("Calculator")
        wait_stable.assert_not_called()


class AgentHelperCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows coordinate safety")
    def test_tap_icon_is_disabled_until_windows_offset_is_calibrated(self):
        with self.assertRaisesRegex(RuntimeError, "open_app"):
            helpers.tap_icon("Weather")


if __name__ == "__main__":
    unittest.main()
