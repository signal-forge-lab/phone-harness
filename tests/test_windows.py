import os
import unittest
from io import BytesIO
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

    def test_display_point_scale_reads_primary_display(self):
        data = {
            "displays": [
                {"primary": False, "pointScale": 1},
                {"primary": True, "pointScale": 3},
            ]
        }
        self.assertEqual(windows._display_point_scale(data), 3)


class DeviceSelectionTests(unittest.TestCase):
    def test_usb_is_default_transport(self):
        with patch.dict(os.environ, {}, clear=False), \
                patch.object(windows, "_usb_device_udids", return_value=["device-1"]):
            os.environ.pop("PHONE_HARNESS_TRANSPORT", None)
            self.assertEqual(windows.device_udids(), ["device-1"])

    def test_wifi_transport_discovers_tunneld_device(self):
        with patch.dict(os.environ, {"PHONE_HARNESS_TRANSPORT": "wifi"}), \
                patch.object(windows, "_tunneld_wifi_devices", return_value=[("device-1", ("fd00::1", 12345))]):
            self.assertEqual(windows.device_udids(), ["device-1"])

    def test_tunneld_listing_returns_rsd_endpoint(self):
        payload = b'{"device-1":[{"tunnel-address":"fd00::1","tunnel-port":12345,"interface":"host.local"}]}'
        with patch.object(windows, "urlopen", return_value=BytesIO(payload)):
            self.assertEqual(windows._tunneld_wifi_devices(), [("device-1", ("fd00::1", 12345))])

    def test_auto_transport_prefers_usb(self):
        with patch.dict(os.environ, {"PHONE_HARNESS_TRANSPORT": "auto"}), \
                patch.object(windows, "_usb_device_udids", return_value=["usb-device"]), \
                patch.object(windows, "_tunneld_wifi_devices") as wifi:
            self.assertEqual(windows.device_udids(), ["usb-device"])
        wifi.assert_not_called()

    def test_invalid_transport_is_rejected(self):
        with patch.dict(os.environ, {"PHONE_HARNESS_TRANSPORT": "bluetooth"}):
            with self.assertRaisesRegex(RuntimeError, "usb, wifi, or auto"):
                windows.device_udids()

    def test_select_device_requires_exactly_one_device(self):
        self.assertEqual(windows._select_device(["device-1"]), "device-1")
        with self.assertRaises(RuntimeError):
            windows._select_device([])
        with self.assertRaises(RuntimeError):
            windows._select_device(["device-1", "device-2"])

    def test_remote_control_support_starts_at_ios_27(self):
        self.assertFalse(windows._remote_control_supported("26.6"))
        self.assertTrue(windows._remote_control_supported("27.0"))

    def test_tap_uses_wda_fallback_on_ios_26(self):
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_wda_point", return_value=(100, 200)), \
                patch.object(windows, "_run_wda") as run:
            windows.tap(300, 600)
        run.assert_called_once_with("tap-coordinate", 100, 200)

    def test_tap_keeps_coredevice_hid_on_ios_27(self):
        with patch.object(windows, "_product_version", return_value="27.0"), \
                patch.object(windows, "_screen_point", return_value=(123, 456)), \
                patch.object(windows, "_run_pm3") as run:
            windows.tap(300, 600)
        run.assert_called_once_with(
            "developer", "core-device", "universal-hid-service", "tap", 123, 456
        )

    def test_drag_uses_wda_fallback_on_ios_26(self):
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_wda_point", side_effect=[(10, 20), (30, 40)]), \
                patch.object(windows, "_run_wda") as run:
            windows.drag(100, 200, 300, 400, duration=0.7, steps=15)
        run.assert_called_once_with(
            "swipe", 10, 20, 30, 40, "--duration", 0.7,
        )

    def test_wda_point_scales_screenshot_pixels_to_logical_points(self):
        old_scale = windows._POINT_SCALE
        windows._POINT_SCALE = 3
        try:
            with patch.object(windows, "ensure_window", return_value={"w": 1206, "h": 2622}):
                self.assertEqual(windows._wda_point(603, 1311), (201, 437))
        finally:
            windows._POINT_SCALE = old_scale

    def test_accessibility_elements_scale_wda_rects_to_screenshot_pixels(self):
        old_scale = windows._POINT_SCALE
        windows._POINT_SCALE = 3
        items = [{
            "type": "XCUIElementTypeButton",
            "name": "eight",
            "label": "8",
            "value": None,
            "visible": "true",
            "rect": {"x": "100", "y": "200", "width": "50", "height": "60"},
        }]
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_wda_runner_bundle", return_value="runner"), \
                    patch.object(windows, "_json_wda", return_value=items):
                result = windows.accessibility_elements()
        finally:
            windows._POINT_SCALE = old_scale
        self.assertEqual(result[0]["text"], "8")
        self.assertEqual(result[0]["source"], "accessibility")
        self.assertEqual(result[0]["x"], 375)
        self.assertEqual(result[0]["y"], 690)

    def test_tap_accessibility_prefers_accessibility_id(self):
        item = {"name": "com.example.button", "label": "Button", "x": 10, "y": 20}
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_run_wda") as run:
            windows.tap_accessibility(item)
        run.assert_called_once_with("tap", "com.example.button", "--using", "accessibility id")

    def test_tap_accessibility_keeps_hid_on_ios_27(self):
        item = {"name": "com.example.button", "label": "Button", "x": 10, "y": 20}
        with patch.object(windows, "_product_version", return_value="27.0"), \
                patch.object(windows, "tap") as tap, \
                patch.object(windows, "_run_wda") as run:
            windows.tap_accessibility(item)
        tap.assert_called_once_with(10, 20)
        run.assert_not_called()

    def test_tap_accessibility_batch_uses_one_wda_batch(self):
        items = [
            {"text": "1", "name": "one", "label": "1", "x": 10, "y": 20},
            {"text": "2", "name": "two", "label": "2", "x": 30, "y": 40},
        ]
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_run_wda_batch") as run:
            windows.tap_accessibility_batch(items)
        run.assert_called_once_with([
            {"op": "tap", "selector": "one", "using": "accessibility id"},
            {"op": "tap", "selector": "two", "using": "accessibility id"},
        ])

    def test_wda_swipe_action_uses_logical_coordinates(self):
        with patch.object(windows, "ensure_window", return_value={"x": 0, "y": 0, "w": 1200, "h": 2400}), \
                patch.object(windows, "_wda_point", side_effect=[(200, 560), (200, 240)]):
            action = windows._wda_action_for_swipe("up", distance=0.4)
        self.assertEqual(action, {
            "op": "swipe",
            "start_x": 200,
            "start_y": 560,
            "end_x": 200,
            "end_y": 240,
            "duration": 0.12,
        })

    def test_wda_raw_actions_normalize_to_logical_coordinates(self):
        with patch.object(windows, "_wda_point", side_effect=[(10, 20), (30, 40), (50, 60)]):
            tap = windows._wda_action_for_tap(100, 200)
            drag = windows._wda_action_for_drag(100, 200, 300, 400, duration=0.5)
        self.assertEqual(tap, {"op": "tap-coordinate", "x": 10, "y": 20})
        self.assertEqual(drag, {
            "op": "swipe",
            "start_x": 30,
            "start_y": 40,
            "end_x": 50,
            "end_y": 60,
            "duration": 0.5,
        })

    def test_wda_scroll_matches_scroll_wheel_direction(self):
        with patch.object(windows, "ensure_window", return_value={"x": 0, "y": 0, "w": 1200, "h": 2400}), \
                patch.object(windows, "_wda_action_for_drag") as drag:
            drag.return_value = {"op": "swipe"}
            action = windows._wda_action_for_scroll(300)
        self.assertEqual(action, {"op": "swipe"})
        drag.assert_called_once_with(600, 1200, 600, 900, duration=0.35)

    def test_tunneld_status_hides_device_identifiers(self):
        with patch.object(windows, "_tunneld_wifi_devices", return_value=[("private-udid", ("fd00::1", 12345))]):
            status = windows.tunneld_status()
        self.assertEqual(status, {"reachable": True, "device_count": 1})


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

    def test_ios_26_wda_typing_accepts_unicode(self):
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_run_wda") as run:
            windows.type_text("日本語")
        run.assert_called_once_with("type", "日本語")


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
    def test_runtime_transport_status_is_privacy_safe(self):
        old_transport = windows._DEVICE_TRANSPORT
        old_rsd = windows._DEVICE_RSD
        old_ready = windows._WDA_READY
        windows._DEVICE_TRANSPORT = "wifi"
        windows._DEVICE_RSD = ("fd00::private", 12345)
        windows._WDA_READY = True
        try:
            status = windows.runtime_transport_status()
        finally:
            windows._DEVICE_TRANSPORT = old_transport
            windows._DEVICE_RSD = old_rsd
            windows._WDA_READY = old_ready
        self.assertEqual(status["active_transport"], "wifi")
        self.assertTrue(status["wda_ready"])
        self.assertNotIn("fd00", repr(status))

    def test_wda_ready_state_skips_repeated_status_probe(self):
        old_ready = windows._WDA_READY
        windows._WDA_READY = True
        try:
            with patch.object(windows, "_run_pm3") as run:
                windows._ensure_wda_runner()
            run.assert_not_called()
        finally:
            windows._WDA_READY = old_ready

    def test_wifi_pm3_commands_use_tunneld_target(self):
        old_udid = windows._DEVICE_UDID
        old_transport = windows._DEVICE_TRANSPORT
        old_rsd = windows._DEVICE_RSD
        windows._DEVICE_UDID = "device-1"
        windows._DEVICE_TRANSPORT = "wifi"
        windows._DEVICE_RSD = ("fd00::1", 12345)
        ok = CompletedProcess(["pm3"], 0, stdout="{}", stderr="")
        try:
            with patch("phone_harness.windows.subprocess.run", return_value=ok) as run:
                windows._run_pm3("apps", "list")
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ["--rsd", "fd00::1", "12345"])
        finally:
            windows._DEVICE_UDID = old_udid
            windows._DEVICE_TRANSPORT = old_transport
            windows._DEVICE_RSD = old_rsd

    def test_run_pm3_normalizes_timeouts(self):
        with patch("phone_harness.windows.subprocess.run", side_effect=TimeoutExpired(["pm3"], 5)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                windows._run_pm3("usbmux", "list", timeout=5)

    def test_run_pm3_forwards_stdin_payload(self):
        completed = CompletedProcess(["pm3"], 0, stdout="ok", stderr="")
        with patch("phone_harness.windows.subprocess.run", return_value=completed) as run:
            self.assertEqual(
                windows._run_pm3("developer", "wda", "batch", input_text='[{"op":"tap"}]'),
                "ok",
            )
        self.assertEqual(run.call_args.kwargs["input"], '[{"op":"tap"}]')

    def test_pm3_telemetry_does_not_record_selector_payload(self):
        completed = CompletedProcess(["pm3"], 0, stdout="ok", stderr="")
        with patch("phone_harness.windows.subprocess.run", return_value=completed):
            windows._run_pm3("developer", "wda", "tap", "PRIVATE SELECTOR")
        status = windows.runtime_transport_status()
        self.assertEqual(status["last_pm3_operation"], "developer/wda/tap")
        self.assertNotIn("PRIVATE SELECTOR", repr(status))

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
        old_ready = windows._WDA_READY
        old_rsd = windows._DEVICE_RSD
        windows._DEVICE_UDID = "device-1"
        windows._SCREEN_SIZE = (1179, 2556)
        windows._WDA_READY = True
        windows._DEVICE_RSD = ("fd00::1", 12345)
        failed = CompletedProcess(["pm3"], 1, stdout="", stderr="failed")
        try:
            with patch("phone_harness.windows.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    windows._run_pm3("developer", "core-device", "get-display-info")
            self.assertIsNone(windows._DEVICE_UDID)
            self.assertIsNone(windows._SCREEN_SIZE)
            self.assertFalse(windows._WDA_READY)
            self.assertIsNone(windows._DEVICE_RSD)
        finally:
            windows._DEVICE_UDID = old_udid
            windows._SCREEN_SIZE = old_size
            windows._WDA_READY = old_ready
            windows._DEVICE_RSD = old_rsd

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
