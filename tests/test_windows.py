import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from phone_harness import windows


class CoordinateTests(unittest.TestCase):
    def test_pixel_to_hid_maps_edges_and_center(self):
        self.assertEqual(windows.pixel_to_hid(0, 0, 1179, 2556), (0, 0))
        self.assertEqual(windows.pixel_to_hid(1179, 2556, 1179, 2556), (65535, 65535))
        x, y = windows.pixel_to_hid(1179 / 2, 2556 / 2, 1179, 2556)
        self.assertIn(x, (32767, 32768))
        self.assertIn(y, (32767, 32768))

    def test_pixel_to_hid_rejects_invalid_display_size(self):
        with self.assertRaises(ValueError):
            windows.pixel_to_hid(1, 1, 0, 2556)

    def test_display_size_reads_coredevice_shape(self):
        data = {"displays": [{"currentMode": {"size": {"width": 1179, "height": 2556}}}]}
        self.assertEqual(windows._display_size(data), (1179, 2556))


class DeviceSelectionTests(unittest.TestCase):
    def test_select_device_requires_exactly_one_device(self):
        self.assertEqual(windows._select_device(["device-1"]), "device-1")
        with self.assertRaises(RuntimeError):
            windows._select_device([])
        with self.assertRaises(RuntimeError):
            windows._select_device(["device-1", "device-2"])


class AppResolutionTests(unittest.TestCase):
    def test_resolve_app_bundle_matches_display_name(self):
        apps = [
            {"bundleIdentifier": "com.apple.Preferences", "displayName": "Settings"},
            {"bundleIdentifier": "com.apple.mobilesafari", "displayName": "Safari"},
        ]
        self.assertEqual(windows._resolve_app_bundle("settings", apps), "com.apple.Preferences")

    def test_resolve_app_bundle_accepts_bundle_identifier(self):
        apps = [{"bundleIdentifier": "com.example.app", "displayName": "Example"}]
        self.assertEqual(windows._resolve_app_bundle("com.example.app", apps), "com.example.app")

    def test_resolve_app_bundle_rejects_ambiguous_names(self):
        apps = [
            {"bundleIdentifier": "com.example.one", "displayName": "Example"},
            {"bundleIdentifier": "com.example.two", "displayName": "Example"},
        ]
        with self.assertRaises(RuntimeError):
            windows._resolve_app_bundle("Example", apps)


class TypingTests(unittest.TestCase):
    def test_printable_ascii(self):
        self.assertTrue(windows._is_printable_ascii("hello 123!?"))
        self.assertFalse(windows._is_printable_ascii("hello\n"))
        self.assertFalse(windows._is_printable_ascii("日本語"))


class StabilityTests(unittest.TestCase):
    def test_image_signature_is_stable_for_same_pixels(self):
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            Image.new("RGB", (200, 100), "white").save(first)
            Image.new("RGB", (200, 100), "white").save(second)
            self.assertEqual(windows.image_signature(first), windows.image_signature(second))


if __name__ == "__main__":
    unittest.main()
