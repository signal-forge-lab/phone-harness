import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from PIL import Image

from phone_harness import helpers


class TextTargetingTests(unittest.TestCase):
    def test_region_screenshot_crops_pixels_but_keeps_screen_coordinate_contract(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            output = Path(directory) / "crop.png"
            image = Image.new("RGB", (200, 100), "red")
            for x in range(100, 200):
                for y in range(100):
                    image.putpixel((x, y), (0, 0, 255))
            image.save(source)
            window = {"x": 0, "y": 0, "w": 200, "h": 100}
            with patch.object(helpers.mirror, "ensure_window", return_value=window, create=True), \
                    patch.object(helpers.mirror, "capture", return_value=(str(source), window)):
                result = helpers.screenshot(output, region={"x": 100, "y": 0, "w": 100, "h": 100})

            self.assertEqual(Path(result), output)
            with Image.open(output) as cropped:
                self.assertEqual(cropped.size, (100, 100))
                self.assertEqual(cropped.getpixel((50, 50)), (0, 0, 255))

    def test_elements_region_filters_dense_accessibility_without_ocr(self):
        window = {"x": 0, "y": 0, "w": 400, "h": 800}
        boxes = [
            {"text": "A", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton", "x": 120, "y": 120},
            {"text": "B", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton", "x": 150, "y": 150},
            {"text": "C", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton", "x": 180, "y": 180},
            {"text": "Outside", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton", "x": 350, "y": 700},
        ]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "ensure_window", return_value=window, create=True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=boxes, create=True), \
                patch.object(helpers.mirror, "capture") as capture:
            result = helpers.elements(region={"x": 100, "y": 100, "w": 100, "h": 100})

        self.assertEqual([item["text"] for item in result], ["A", "B", "C"])
        capture.assert_not_called()

    def test_elements_region_augments_sparse_accessibility_with_region_ocr(self):
        window = {"x": 0, "y": 0, "w": 400, "h": 800}
        accessible = [
            {"text": "Canvas", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeOther", "x": 150, "y": 150},
        ]
        ocr_boxes = [{"text": "Target", "confidence": 0.99, "x": 175, "y": 175, "w": 50, "h": 20}]
        region = {"x": 100.0, "y": 100.0, "w": 100.0, "h": 100.0}
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "ensure_window", return_value=window, create=True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=accessible, create=True), \
                patch.object(helpers, "screenshot", return_value="region.png") as screenshot, \
                patch.object(helpers._ocr, "recognize", return_value=ocr_boxes) as recognize:
            result = helpers.elements(region=region)

        screenshot.assert_called_once_with(region=region)
        recognize.assert_called_once_with("region.png", region)
        self.assertEqual([item["text"] for item in result], ["Canvas", "Target"])
        self.assertEqual(result[1]["source"], "ocr")

    def test_region_rejects_bounds_outside_screen(self):
        window = {"x": 0, "y": 0, "w": 400, "h": 800}
        with patch.object(helpers.mirror, "ensure_window", return_value=window, create=True):
            with self.assertRaisesRegex(ValueError, "region must fit"):
                helpers.normalize_region({"x": 350, "y": 0, "w": 100, "h": 100})

    def test_elements_prefers_windows_accessibility(self):
        boxes = [
            {"text": "1", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton"},
            {"text": "2", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton"},
            {"text": "3", "confidence": 1.0, "source": "accessibility", "role": "XCUIElementTypeButton"},
        ]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=boxes, create=True), \
                patch.object(helpers.mirror, "capture") as capture:
            self.assertEqual(helpers.elements(), boxes)
        capture.assert_not_called()

    def test_elements_augments_sparse_windows_accessibility_with_ocr(self):
        accessible = [
            {
                "text": "AliExpress",
                "confidence": 1.0,
                "source": "accessibility",
                "role": "XCUIElementTypeApplication",
                "x": 600,
                "y": 1300,
                "w": 1200,
                "h": 2600,
            },
            {
                "text": "Mission",
                "confidence": 1.0,
                "source": "accessibility",
                "role": "XCUIElementTypeStaticText",
                "x": 1000,
                "y": 1900,
                "w": 100,
                "h": 40,
            },
        ]
        ocr_boxes = [{"text": "Play", "confidence": 0.98, "x": 100, "y": 2400, "w": 120, "h": 50}]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=accessible, create=True), \
                patch.object(helpers.mirror, "capture", return_value=("screen.png", {})), \
                patch.object(helpers._ocr, "recognize", return_value=ocr_boxes):
            result = helpers.elements()

        self.assertEqual(result[:2], accessible)
        self.assertEqual(result[2]["text"], "Play")
        self.assertEqual(result[2]["source"], "ocr")

    def test_elements_reuses_supplied_frame_for_sparse_ocr(self):
        accessible = [{
            "text": "Canvas",
            "confidence": 1.0,
            "source": "accessibility",
            "role": "XCUIElementTypeOther",
            "x": 150,
            "y": 150,
        }]
        ocr_boxes = [{"text": "Target", "confidence": 0.99, "x": 175, "y": 175, "w": 50, "h": 20}]
        frame = MagicMock()
        frame.window = {"x": 0, "y": 0, "w": 400, "h": 800}
        frame.variant.return_value = Path("prepared.png")
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=accessible, create=True), \
                patch.object(helpers.mirror, "capture") as capture, \
                patch.object(helpers._ocr, "recognize", return_value=ocr_boxes) as recognize:
            result = helpers.elements(frame=frame)

        capture.assert_not_called()
        frame.variant.assert_called_once_with(max_long_edge=1600, image_format="PNG")
        recognize.assert_called_once_with(Path("prepared.png"), frame.window)
        self.assertEqual([item["text"] for item in result], ["Canvas", "Target"])

    def test_elements_dedupes_nearby_ocr_text_from_sparse_accessibility(self):
        accessible = [{
            "text": "Play",
            "confidence": 1.0,
            "source": "accessibility",
            "role": "XCUIElementTypeButton",
            "x": 100,
            "y": 200,
            "w": 120,
            "h": 60,
        }]
        ocr_boxes = [{"text": "Play", "confidence": 0.99, "x": 102, "y": 201, "w": 100, "h": 40}]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "accessibility_elements", return_value=accessible, create=True), \
                patch.object(helpers.mirror, "capture", return_value=("screen.png", {})), \
                patch.object(helpers._ocr, "recognize", return_value=ocr_boxes):
            result = helpers.elements()

        self.assertEqual(result, accessible)

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


class StabilityTests(unittest.TestCase):
    def test_wait_stable_accepts_bounded_windows_ambient_motion(self):
        captures = [(f"frame-{index}.png", {}) for index in range(4)]
        signatures = [bytes([index]) for index in range(4)]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "capture", side_effect=captures), \
                patch.object(helpers.mirror, "image_signature", side_effect=signatures), \
                patch.object(helpers.mirror, "image_signatures_close", return_value=False), \
                patch.object(
                    helpers.mirror,
                    "image_signature_distance",
                    side_effect=[0.038, 0.031, 0.034],
                    create=True,
                ), \
                patch.object(helpers.time, "monotonic", side_effect=[0.0, 0.0, 0.1, 0.2, 0.3]), \
                patch.object(helpers.time, "sleep"):
            self.assertTrue(helpers.wait_stable(timeout=2, interval=0.1))

    def test_wait_stable_rejects_large_or_unsettled_windows_motion(self):
        captures = [(f"frame-{index}.png", {}) for index in range(4)]
        signatures = [bytes([index]) for index in range(4)]
        with patch.object(helpers, "_WINDOWS", True), \
                patch.object(helpers.mirror, "capture", side_effect=captures), \
                patch.object(helpers.mirror, "image_signature", side_effect=signatures), \
                patch.object(helpers.mirror, "image_signatures_close", return_value=False), \
                patch.object(
                    helpers.mirror,
                    "image_signature_distance",
                    side_effect=[0.12, 0.04, 0.11],
                    create=True,
                ), \
                patch.object(helpers.time, "monotonic", side_effect=[0.0, 0.0, 0.1, 0.2, 2.1]), \
                patch.object(helpers.time, "sleep"):
            self.assertFalse(helpers.wait_stable(timeout=2, interval=0.1))


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
