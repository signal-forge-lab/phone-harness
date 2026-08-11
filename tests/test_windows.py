import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

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

    def test_display_size_accepts_ios_26_list_shape_and_primary_display(self):
        data = {
            "displays": [
                {
                    "primary": False,
                    "currentMode": {"size": [0.0, 0.0]},
                },
                {
                    "primary": True,
                    "currentMode": {"size": [1206.0, 2622.0]},
                },
            ]
        }
        self.assertEqual(windows._display_size(data), (1206, 2622))


class DeviceSelectionTests(unittest.TestCase):
    def test_device_discovery_is_usb_only(self):
        with patch.object(windows, "_json_pm3", return_value=["device-1"]) as query:
            self.assertEqual(windows.device_udids(), ["device-1"])
        query.assert_called_once_with("usbmux", "list", "--usb", "--simple")

    def test_select_device_requires_exactly_one_device(self):
        self.assertEqual(windows._select_device(["device-1"]), "device-1")
        with self.assertRaises(RuntimeError):
            windows._select_device([])
        with self.assertRaises(RuntimeError):
            windows._select_device(["device-1", "device-2"])

    def test_remote_control_support_starts_at_ios_27(self):
        self.assertFalse(windows._remote_control_supported("26.6"))
        self.assertTrue(windows._remote_control_supported("27.0"))

    def test_tap_rejects_ios_26_before_sending_hid(self):
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_run_pm3") as run:
            with self.assertRaisesRegex(RuntimeError, "iOS 27.0"):
                windows.tap(100, 200)
        run.assert_not_called()


class AppResolutionTests(unittest.TestCase):
    def test_normalize_apps_accepts_installation_proxy_mapping(self):
        data = {
            "com.apple.Preferences": {"CFBundleDisplayName": "Settings"},
            "com.apple.mobilesafari": {"CFBundleName": "MobileSafari"},
        }
        apps = windows._normalize_apps(data)
        self.assertEqual(apps[0]["bundleIdentifier"], "com.apple.Preferences")
        self.assertEqual(apps[0]["CFBundleDisplayName"], "Settings")

    def test_resolve_app_bundle_matches_display_name(self):
        apps = [
            {"bundleIdentifier": "com.apple.Preferences", "CFBundleDisplayName": "Settings"},
            {"bundleIdentifier": "com.apple.mobilesafari", "CFBundleDisplayName": "Safari"},
        ]
        self.assertEqual(windows._resolve_app_bundle("settings", apps), "com.apple.Preferences")

    def test_resolve_app_bundle_accepts_bundle_identifier(self):
        apps = [{"bundleIdentifier": "com.example.app", "CFBundleDisplayName": "Example"}]
        self.assertEqual(windows._resolve_app_bundle("com.example.app", apps), "com.example.app")

    def test_resolve_app_bundle_accepts_documented_english_system_aliases_on_localized_device(self):
        apps = [
            {"bundleIdentifier": "com.apple.Preferences", "CFBundleDisplayName": "localized-settings"},
            {"bundleIdentifier": "com.apple.mobilenotes", "CFBundleDisplayName": "localized-notes"},
            {"bundleIdentifier": "com.apple.weather", "CFBundleDisplayName": "localized-weather"},
        ]
        self.assertEqual(windows._resolve_app_bundle("Settings", apps), "com.apple.Preferences")
        self.assertEqual(windows._resolve_app_bundle("Notes", apps), "com.apple.mobilenotes")
        self.assertEqual(windows._resolve_app_bundle("Weather", apps), "com.apple.weather")

    def test_resolve_app_bundle_rejects_ambiguous_names(self):
        apps = [
            {"bundleIdentifier": "com.example.one", "CFBundleDisplayName": "Example"},
            {"bundleIdentifier": "com.example.two", "CFBundleDisplayName": "Example"},
        ]
        with self.assertRaises(RuntimeError):
            windows._resolve_app_bundle("Example", apps)

    def test_open_app_does_not_kill_existing_process(self):
        apps = {"com.example.app": {"CFBundleDisplayName": "Example"}}
        with patch.object(windows, "_require_device", return_value="device-1"), \
                patch.object(windows, "_json_pm3", return_value=apps) as query, \
                patch.object(windows, "_run_pm3") as run:
            bundle = windows.open_app("Example")

        self.assertEqual(bundle, "com.example.app")
        query.assert_called_once_with("apps", "list")
        run.assert_called_once_with(
            "developer", "dvt", "launch", "--no-kill-existing", "com.example.app"
        )


class TypingTests(unittest.TestCase):
    def test_printable_ascii(self):
        self.assertTrue(windows._is_printable_ascii("hello 123!?"))
        self.assertFalse(windows._is_printable_ascii("hello\n"))
        self.assertFalse(windows._is_printable_ascii("日本語"))

    def test_empty_text_is_a_noop(self):
        with patch.object(windows, "_require_device") as require, \
                patch.object(windows, "_run_pm3") as run:
            windows.type_text("")
        require.assert_not_called()
        run.assert_not_called()


class GestureSafetyTests(unittest.TestCase):
    def test_zero_scroll_is_a_noop(self):
        with patch.object(windows, "drag") as drag:
            windows.scroll_wheel(0, 100, 200)
        drag.assert_not_called()


class StabilityTests(unittest.TestCase):
    def test_image_signature_is_stable_for_same_pixels(self):
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            Image.new("RGB", (200, 100), "white").save(first)
            Image.new("RGB", (200, 100), "white").save(second)
            self.assertEqual(windows.image_signature(first), windows.image_signature(second))

    def test_image_signatures_close_tolerates_small_live_screen_noise(self):
        stable = bytes([8] * 4096)
        noisy = bytearray(stable)
        for index in range(10):
            noisy[index] += 1
        changed = bytes([0] * 4096)

        self.assertTrue(windows.image_signatures_close(stable, bytes(noisy)))
        self.assertFalse(windows.image_signatures_close(stable, changed))


class TransportRobustnessTests(unittest.TestCase):
    def test_run_pm3_normalizes_timeouts(self):
        with patch("phone_harness.windows.subprocess.run", side_effect=TimeoutExpired(["pm3"], 5)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                windows._run_pm3("usbmux", "list", timeout=5)

    def test_run_pm3_rejects_zero_exit_device_error(self):
        failed = CompletedProcess(
            ["pm3"],
            0,
            stdout="some output",
            stderr="2026-08-11 ERROR Remote control requires iOS 27.0 or later on this device.",
        )
        with patch("phone_harness.windows.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "iOS 27.0 or later"):
                windows._run_pm3("developer", "core-device", "universal-hid-service", "tap")

    def test_run_pm3_rejects_zero_exit_traceback(self):
        failed = CompletedProcess(
            ["pm3"],
            0,
            stdout="",
            stderr="Traceback (most recent call last):\nCoreDeviceError: failed",
        )
        with patch("phone_harness.windows.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "CoreDeviceError"):
                windows._run_pm3("developer", "core-device", "universal-hid-service", "session")

    def test_capture_preserves_existing_output_when_capture_fails(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (10, 10), "white").save(path)
            original = path.read_bytes()
            old_size = windows._SCREEN_SIZE
            windows._SCREEN_SIZE = (10, 10)

            def fake_run(*args, **kwargs):
                self.assertNotEqual(Path(args[-1]), path)
                self.assertTrue(path.exists())
                raise RuntimeError("capture failed")

            try:
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_run_pm3", side_effect=fake_run):
                    with self.assertRaisesRegex(RuntimeError, "capture failed"):
                        windows.capture(path)

                self.assertEqual(path.read_bytes(), original)
                self.assertIsNone(windows._SCREEN_SIZE)
            finally:
                windows._SCREEN_SIZE = old_size

    def test_failed_pm3_command_forgets_cached_device(self):
        old_udid = windows._DEVICE_UDID
        old_size = windows._SCREEN_SIZE
        windows._DEVICE_UDID = "device-1"
        windows._SCREEN_SIZE = (1179, 2556)
        failed = CompletedProcess(["pm3"], 1, stdout="", stderr="failed")
        try:
            with patch("phone_harness.windows.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    windows._run_pm3("developer", "core-device", "get-display-info")
            self.assertIsNone(windows._DEVICE_UDID)
            self.assertIsNone(windows._SCREEN_SIZE)
        finally:
            windows._DEVICE_UDID = old_udid
            windows._SCREEN_SIZE = old_size

    def test_capture_replaces_existing_output_only_after_fresh_png(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (10, 10), "white").save(path)

            def fake_run(*args, **kwargs):
                Image.new("RGB", (20, 30), "black").save(Path(args[-1]))

            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_run_pm3", side_effect=fake_run):
                result, bounds = windows.capture(path)

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 20, "h": 30})
            with Image.open(path) as image:
                self.assertEqual(image.size, (20, 30))


if __name__ == "__main__":
    unittest.main()
