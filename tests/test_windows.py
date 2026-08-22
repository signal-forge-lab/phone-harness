import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

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

    def test_ensure_window_fetches_point_scale_when_screen_size_is_already_cached(self):
        old_size, old_scale = windows._SCREEN_SIZE, windows._POINT_SCALE
        windows._SCREEN_SIZE = (1206, 2622)
        windows._POINT_SCALE = None
        display_info = {
            "displays": [
                {
                    "primary": True,
                    "pointScale": 3,
                    "currentMode": {"size": [1206.0, 2622.0]},
                }
            ]
        }
        try:
            with patch.object(windows, "_require_device"), patch.object(
                windows, "_json_pm3", return_value=display_info
            ) as get_display_info:
                self.assertEqual(
                    windows.ensure_window(),
                    {"x": 0, "y": 0, "w": 1206, "h": 2622},
                )
            self.assertEqual(windows._POINT_SCALE, 3)
            get_display_info.assert_called_once_with(
                "developer", "core-device", "get-display-info", timeout=90
            )
        finally:
            windows._SCREEN_SIZE, windows._POINT_SCALE = old_size, old_scale

    def test_ensure_window_caches_display_info_for_repeated_coordinate_conversion(self):
        old_size, old_scale = windows._SCREEN_SIZE, windows._POINT_SCALE
        windows._SCREEN_SIZE = None
        windows._POINT_SCALE = None
        display_info = {
            "displays": [
                {
                    "primary": True,
                    "pointScale": 3,
                    "currentMode": {"size": [1206.0, 2622.0]},
                }
            ]
        }
        try:
            with patch.object(windows, "_require_device"), patch.object(
                windows, "_json_pm3", return_value=display_info
            ) as get_display_info:
                self.assertEqual(windows.ensure_window()["w"], 1206)
                self.assertEqual(windows.ensure_window()["w"], 1206)
            get_display_info.assert_called_once_with(
                "developer", "core-device", "get-display-info", timeout=90
            )
            self.assertEqual(windows._SCREEN_SIZE, (1206, 2622))
            self.assertEqual(windows._POINT_SCALE, 3)
        finally:
            windows._SCREEN_SIZE, windows._POINT_SCALE = old_size, old_scale

    def test_ensure_window_prefers_inprocess_display_info_on_wifi(self):
        old_size, old_scale = windows._SCREEN_SIZE, windows._POINT_SCALE
        windows._SCREEN_SIZE = None
        windows._POINT_SCALE = None
        display_info = {
            "displays": [{"primary": True, "pointScale": 3, "currentMode": {"size": [1206, 2622]}}]
        }
        try:
            with patch.object(windows, "_require_device"), \
                    patch.object(windows, "_inprocess_supported", return_value=True), \
                    patch.object(windows, "_inprocess_get_display_info", return_value=display_info) as direct, \
                    patch.object(windows, "_json_pm3") as cli:
                result = windows.ensure_window()
        finally:
            windows._SCREEN_SIZE, windows._POINT_SCALE = old_size, old_scale

        self.assertEqual(result["w"], 1206)
        direct.assert_called_once_with(timeout=90)
        cli.assert_not_called()


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
        run.assert_called_once_with("tap-coordinate", 100, 200, "--attach-active-app")

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
            "swipe", 10, 20, 30, 40, "--duration", 0.7, "--attach-active-app",
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
                    patch.object(windows, "_json_wda", return_value=items) as json_wda:
                result = windows.accessibility_elements()
        finally:
            windows._POINT_SCALE = old_scale
        self.assertEqual(result[0]["text"], "8")
        self.assertEqual(result[0]["source"], "accessibility")
        self.assertEqual(result[0]["x"], 375)
        self.assertEqual(result[0]["y"], 690)
        json_wda.assert_called_once_with("list-items", "--with-rect", "--lean-source")

    def test_accessibility_elements_prefers_inprocess_wda_on_wifi(self):
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
                    patch.object(windows, "_inprocess_supported", return_value=True), \
                    patch.object(windows, "_ensure_wda_runner") as ensure, \
                    patch.object(windows, "_inprocess_wda_items", return_value=items) as direct, \
                    patch.object(windows, "_json_wda") as cli:
                result = windows.accessibility_elements()
        finally:
            windows._POINT_SCALE = old_scale

        self.assertEqual(result[0]["text"], "8")
        ensure.assert_called_once_with()
        direct.assert_called_once_with()
        cli.assert_not_called()

    def test_wda_items_from_source_preserves_tap_ready_fields(self):
        source = (
            '<XCUIElementTypeApplication visible="true">'
            '<XCUIElementTypeButton name="eight" label="8" visible="true" '
            'x="100" y="200" width="50" height="60" />'
            '</XCUIElementTypeApplication>'
        )
        items = windows._wda_items_from_source(source)
        button = next(item for item in items if item["name"] == "eight")
        self.assertEqual(button["label"], "8")
        self.assertEqual(button["visible"], "true")
        self.assertEqual(button["rect"]["width"], "50")

    def test_accessibility_elements_restarts_once_for_stale_wda_application(self):
        old_scale = windows._POINT_SCALE
        windows._POINT_SCALE = 3
        stale = RuntimeError(
            "pymobiledevice3 failed: WDA error (status=404): "
            "The previously found element \"Application\n'local.pid.0'\" is not present "
            "in the current view anymore. Original error: Application local.pid.0 is not running"
        )
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
                    patch.object(windows, "_json_wda", side_effect=[stale, items]) as json_wda, \
                    patch.object(windows, "_restart_wda_runner") as restart:
                result = windows.accessibility_elements()
        finally:
            windows._POINT_SCALE = old_scale

        self.assertEqual(result[0]["text"], "8")
        self.assertEqual(json_wda.call_count, 2)
        restart.assert_called_once_with()

    def test_accessibility_elements_does_not_restart_for_other_wda_errors(self):
        with patch.object(windows, "_require_device", return_value="device-1"), \
                patch.object(windows, "_wda_runner_bundle", return_value="runner"), \
                patch.object(windows, "_json_wda", side_effect=RuntimeError("WDA timeout")), \
                patch.object(windows, "_restart_wda_runner") as restart:
            with self.assertRaisesRegex(RuntimeError, "WDA timeout"):
                windows.accessibility_elements()
        restart.assert_not_called()

    def test_tap_accessibility_prefers_accessibility_id(self):
        item = {"name": "com.example.button", "label": "Button", "x": 10, "y": 20}
        with patch.object(windows, "_product_version", return_value="26.6"), \
                patch.object(windows, "_run_wda") as run:
            windows.tap_accessibility(item)
        run.assert_called_once_with(
            "tap", "com.example.button", "--using", "accessibility id", "--attach-active-app"
        )

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
                patch.object(windows, "_wda_point", side_effect=[(5, 10), (15, 20)]), \
                patch.object(windows, "_run_wda_batch") as run:
            windows.tap_accessibility_batch(items)
        run.assert_called_once_with([
            {"op": "tap-coordinate", "x": 5, "y": 10},
            {"op": "tap-coordinate", "x": 15, "y": 20},
        ])

    def test_wda_batch_attaches_new_session_to_active_application(self):
        with patch.object(windows, "_ensure_wda_runner"), \
                patch.object(windows, "_inprocess_supported", return_value=False), \
                patch.object(windows, "_run_pm3") as run:
            windows._run_wda_batch([{"op": "tap", "selector": "one"}])
        self.assertEqual(
            run.call_args.args[:6],
            ("developer", "wda", "batch", "--attach-active-app", "--wait-for-idle-timeout", 0),
        )

    def test_wda_batch_prefers_inprocess_runtime_on_wifi(self):
        actions = [{"op": "tap", "selector": "one"}]
        with patch.object(windows, "_ensure_wda_runner"), \
                patch.object(windows, "_inprocess_supported", return_value=True), \
                patch.object(windows, "_inprocess_wda_batch", return_value=True) as run_inprocess, \
                patch.object(windows, "_run_pm3") as run_cli:
            windows._run_wda_batch(actions)
        run_inprocess.assert_called_once_with(actions, timeout=30)
        run_cli.assert_not_called()

    def test_inprocess_wda_batch_reuses_cached_session(self):
        class FakeClient:
            def __init__(self):
                self.session_id = "session-1"
                self.started = 0
                self.idle = []
                self.batches = []

            async def start_session_for_active_app(self):
                self.started += 1
                self.session_id = "session-new"
                return self.session_id

            async def set_wait_for_idle_timeout(self, timeout, session_id=None):
                self.idle.append((timeout, session_id))

            async def run_batch_actions(self, actions, session_id=None):
                self.batches.append((actions, session_id))

        client = FakeClient()
        actions = [{"op": "tap-coordinate", "x": 10, "y": 20}]
        with patch.object(windows, "_inprocess_wda_client", return_value=client):
            self.assertTrue(windows._inprocess_wda_batch(actions))
        self.assertEqual(client.started, 0)
        self.assertEqual(client.idle, [])
        self.assertEqual(client.batches, [(actions, "session-1")])

    def test_inprocess_wda_batch_creates_session_only_once(self):
        class FakeClient:
            def __init__(self):
                self.session_id = None
                self.started = 0
                self.idle = []
                self.batches = []

            async def start_session_for_active_app(self):
                self.started += 1
                self.session_id = "session-new"
                return self.session_id

            async def set_wait_for_idle_timeout(self, timeout, session_id=None):
                self.idle.append((timeout, session_id))

            async def run_batch_actions(self, actions, session_id=None):
                self.batches.append((actions, session_id))

        client = FakeClient()
        actions = [{"op": "tap-coordinate", "x": 10, "y": 20}]
        with patch.object(windows, "_inprocess_wda_client", return_value=client):
            self.assertTrue(windows._inprocess_wda_batch(actions))
            self.assertTrue(windows._inprocess_wda_batch(actions))
        self.assertEqual(client.started, 1)
        self.assertEqual(client.idle, [(0, "session-new")])
        self.assertEqual(client.batches, [
            (actions, "session-new"),
            (actions, "session-new"),
        ])

    def test_inprocess_wda_batch_spaces_only_repeated_same_coordinate_taps(self):
        class FakeClient:
            def __init__(self):
                self.session_id = "session-1"
                self.batches = []

            async def run_batch_actions(self, actions, session_id=None):
                self.batches.append((list(actions), session_id))

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        client = FakeClient()
        actions = [
            {"op": "tap-coordinate", "x": 10, "y": 20},
            {"op": "tap-coordinate", "x": 10, "y": 20},
            {"op": "tap-coordinate", "x": 10, "y": 20},
        ]
        with patch.object(windows, "_inprocess_wda_client", return_value=client), \
                patch.object(windows.asyncio, "sleep", side_effect=fake_sleep):
            self.assertTrue(windows._inprocess_wda_batch(actions))

        self.assertEqual(client.batches, [
            ([actions[0]], "session-1"),
            ([actions[1]], "session-1"),
            ([actions[2]], "session-1"),
        ])
        self.assertEqual(
            sleeps,
            [windows._WDA_REPEAT_TAP_SETTLE_SECONDS, windows._WDA_REPEAT_TAP_SETTLE_SECONDS],
        )

    def test_inprocess_wda_batch_keeps_mixed_actions_in_one_fast_batch(self):
        class FakeClient:
            def __init__(self):
                self.session_id = "session-1"
                self.batches = []

            async def run_batch_actions(self, actions, session_id=None):
                self.batches.append((list(actions), session_id))

        client = FakeClient()
        actions = [
            {"op": "tap-coordinate", "x": 10, "y": 20},
            {"op": "tap-coordinate", "x": 30, "y": 40},
            {"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4, "duration": 0.2},
        ]
        with patch.object(windows, "_inprocess_wda_client", return_value=client):
            self.assertTrue(windows._inprocess_wda_batch(actions))

        self.assertEqual(client.batches, [(actions, "session-1")])

    def test_inprocess_wda_batch_failure_drops_session_without_replay(self):
        class FakeClient:
            def __init__(self):
                self.session_id = "stale-session"
                self.calls = 0

            async def run_batch_actions(self, actions, session_id=None):
                self.calls += 1
                raise RuntimeError("stale session")

        client = FakeClient()
        with patch.object(windows, "_inprocess_wda_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "in-process WDA batch failed"):
                windows._inprocess_wda_batch([{"op": "tap-coordinate", "x": 10, "y": 20}])
        self.assertEqual(client.calls, 1)
        self.assertIsNone(client.session_id)

    def test_wda_swipe_action_uses_native_direction(self):
        action = windows._wda_action_for_swipe("up", distance=0.4)
        self.assertEqual(action, {"op": "swipe-direction", "direction": "up"})

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
        with patch.object(windows, "ensure_window", return_value={"x": 0, "y": 0, "w": 1200, "h": 2400}):
            action = windows._wda_action_for_scroll(300)
        self.assertEqual(action, {
            "op": "scroll-direction",
            "direction": "down",
            "distance": 0.125,
        })

    def test_wda_scroll_zero_is_a_noop(self):
        with patch.object(windows, "ensure_window", return_value={"x": 0, "y": 0, "w": 1200, "h": 2400}):
            self.assertIsNone(windows._wda_action_for_scroll(0))

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
            {"bundleIdentifier": "com.apple.calculator", "CFBundleDisplayName": "localized-calculator"},
            {"bundleIdentifier": "com.apple.mobiletimer", "CFBundleDisplayName": "時計"},
            {"bundleIdentifier": "com.apple.mobilecal", "CFBundleDisplayName": "カレンダー"},
        ]
        self.assertEqual(windows._resolve_app_bundle("Settings", apps), "com.apple.Preferences")
        self.assertEqual(windows._resolve_app_bundle("Notes", apps), "com.apple.mobilenotes")
        self.assertEqual(windows._resolve_app_bundle("Weather", apps), "com.apple.weather")
        self.assertEqual(windows._resolve_app_bundle("Calculator", apps), "com.apple.calculator")
        self.assertEqual(windows._resolve_app_bundle("Clock", apps), "com.apple.mobiletimer")
        self.assertEqual(windows._resolve_app_bundle("時計", apps), "com.apple.mobiletimer")
        self.assertEqual(windows._resolve_app_bundle("Calendar", apps), "com.apple.mobilecal")

    def test_preflight_open_app_resolves_and_caches_without_launching(self):
        old_cache = dict(windows._APP_BUNDLE_CACHE)
        windows._APP_BUNDLE_CACHE.clear()
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_inprocess_supported", return_value=False), \
                    patch.object(windows, "_json_pm3", return_value={"com.apple.mobiletimer": {"CFBundleDisplayName": "時計"}}), \
                    patch.object(windows, "_run_pm3") as run:
                bundle = windows.preflight_open_app("Clock")
        finally:
            windows._APP_BUNDLE_CACHE.clear()
            windows._APP_BUNDLE_CACHE.update(old_cache)
        self.assertEqual(bundle, "com.apple.mobiletimer")
        run.assert_not_called()

    def test_resolve_app_bundle_rejects_ambiguous_names(self):
        apps = [
            {"bundleIdentifier": "com.example.one", "CFBundleDisplayName": "Example"},
            {"bundleIdentifier": "com.example.two", "CFBundleDisplayName": "Example"},
        ]
        with self.assertRaises(RuntimeError):
            windows._resolve_app_bundle("Example", apps)

    def test_open_app_does_not_kill_existing_process(self):
        apps = {"com.example.app": {"CFBundleDisplayName": "Example"}}
        old_cache = dict(windows._APP_BUNDLE_CACHE)
        windows._APP_BUNDLE_CACHE.clear()
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_json_pm3", return_value=apps) as query, \
                    patch.object(windows, "_run_pm3") as run:
                bundle = windows.open_app("Example")
        finally:
            windows._APP_BUNDLE_CACHE.clear()
            windows._APP_BUNDLE_CACHE.update(old_cache)

        self.assertEqual(bundle, "com.example.app")
        query.assert_called_once_with("apps", "list")
        run.assert_called_once_with(
            "developer",
            "core-device",
            "launch-application",
            "--no-kill-existing",
            "com.example.app",
            "",
        )

    def test_open_app_reuses_process_local_bundle_resolution(self):
        apps = {"com.example.app": {"CFBundleDisplayName": "Example"}}
        old_cache = dict(windows._APP_BUNDLE_CACHE)
        windows._APP_BUNDLE_CACHE.clear()
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_json_pm3", return_value=apps) as query, \
                    patch.object(windows, "_run_pm3") as run:
                windows.open_app("Example")
                windows.open_app("example")
        finally:
            windows._APP_BUNDLE_CACHE.clear()
            windows._APP_BUNDLE_CACHE.update(old_cache)

        query.assert_called_once_with("apps", "list")
        self.assertEqual(run.call_count, 2)

    def test_open_app_invalidates_cached_bundle_after_launch_failure(self):
        old_cache = dict(windows._APP_BUNDLE_CACHE)
        windows._APP_BUNDLE_CACHE.clear()
        windows._APP_BUNDLE_CACHE["example"] = "com.example.app"
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_json_pm3") as query, \
                    patch.object(windows, "_run_pm3", side_effect=RuntimeError("launch failed")):
                with self.assertRaisesRegex(RuntimeError, "launch failed"):
                    windows.open_app("Example")
        finally:
            cached_after_failure = windows._APP_BUNDLE_CACHE.get("example")
            windows._APP_BUNDLE_CACHE.clear()
            windows._APP_BUNDLE_CACHE.update(old_cache)

        query.assert_not_called()
        self.assertIsNone(cached_after_failure)

    def test_open_app_prefers_inprocess_listing_and_launch_on_wifi(self):
        old_cache = dict(windows._APP_BUNDLE_CACHE)
        windows._APP_BUNDLE_CACHE.clear()
        apps = {"com.example.app": {"CFBundleDisplayName": "Example"}}
        try:
            with patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_inprocess_supported", return_value=True), \
                    patch.object(windows, "_inprocess_list_apps", return_value=apps) as list_apps, \
                    patch.object(windows, "_inprocess_launch_app", return_value=True) as launch, \
                    patch.object(windows, "_json_pm3") as cli_query, \
                    patch.object(windows, "_run_pm3") as cli_run:
                bundle = windows.open_app("Example")
        finally:
            windows._APP_BUNDLE_CACHE.clear()
            windows._APP_BUNDLE_CACHE.update(old_cache)

        self.assertEqual(bundle, "com.example.app")
        list_apps.assert_called_once_with()
        launch.assert_called_once_with("com.example.app")
        cli_query.assert_not_called()
        cli_run.assert_not_called()


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
        run.assert_called_once_with("type", "日本語", "--attach-active-app")


class GestureSafetyTests(unittest.TestCase):
    def test_zero_scroll_is_a_noop(self):
        with patch.object(windows, "drag") as drag:
            windows.scroll_wheel(0, 100, 200)
        drag.assert_not_called()

    def test_home_press_prefers_inprocess_hid_on_wifi(self):
        with patch.object(windows, "_require_device"), \
                patch.object(windows, "_inprocess_supported", return_value=True), \
                patch.object(windows, "_inprocess_press_home", return_value=True) as home, \
                patch.object(windows, "_run_pm3") as cli:
            windows.press("home")
        home.assert_called_once_with()
        cli.assert_not_called()


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

    def test_image_signature_distance_reports_mean_quantized_difference(self):
        first = bytes([4, 6, 8, 10])
        second = bytes([4, 8, 6, 12])
        self.assertEqual(windows.image_signature_distance(first, second), 1.5)

    def test_image_signature_distance_rejects_mismatched_shapes(self):
        with self.assertRaises(ValueError):
            windows.image_signature_distance(b"abc", b"ab")


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
        self.assertIn("wda_recovery_count", status)
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

    def test_wda_pending_backoff_uses_only_a_short_ready_probe(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_state = windows._WDA_STATE
        old_backoff = windows._WDA_STARTUP_BACKOFF_UNTIL
        process = MagicMock()
        process.poll.return_value = None
        windows._WDA_READY = False
        windows._WDA_RUNNER_PROCESS = process
        windows._WDA_STATE = "pending"
        windows._WDA_STARTUP_BACKOFF_UNTIL = 100.0
        try:
            with patch("phone_harness.windows.time.monotonic", return_value=50.0), \
                    patch.object(windows, "_inprocess_supported", return_value=True), \
                    patch.object(
                        windows, "_inprocess_wda_status", side_effect=RuntimeError("not ready")
                    ) as status, \
                    patch.object(windows, "_require_device") as require_device, \
                    patch("phone_harness.windows.time.sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "still starting"):
                    windows._ensure_wda_runner()

            status.assert_called_once_with(timeout=1)
            require_device.assert_not_called()
            sleep.assert_not_called()
            self.assertEqual(windows._WDA_STATE, "pending")
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_STATE = old_state
            windows._WDA_STARTUP_BACKOFF_UNTIL = old_backoff

    def test_wda_ready_probe_refreshes_stale_inprocess_rsd_after_backoff(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_state = windows._WDA_STATE
        old_backoff = windows._WDA_STARTUP_BACKOFF_UNTIL
        process = MagicMock()
        process.poll.return_value = None
        windows._WDA_READY = False
        windows._WDA_RUNNER_PROCESS = process
        windows._WDA_STATE = "pending"
        windows._WDA_STARTUP_BACKOFF_UNTIL = 0.0
        try:
            with patch("phone_harness.windows.time.monotonic", return_value=50.0), \
                    patch.object(windows, "_inprocess_supported", return_value=True), \
                    patch.object(windows, "_inprocess_wda_status", side_effect=[RuntimeError("stale rsd"), {}]) as status, \
                    patch.object(windows, "_close_inprocess_rsd") as close_rsd, \
                    patch.object(windows, "_require_device") as require_device:
                windows._ensure_wda_runner()

            self.assertEqual(status.call_count, 2)
            close_rsd.assert_called_once_with()
            require_device.assert_not_called()
            self.assertTrue(windows._WDA_READY)
            self.assertEqual(windows._WDA_STATE, "ready")
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_STATE = old_state
            windows._WDA_STARTUP_BACKOFF_UNTIL = old_backoff

    def test_wda_startup_timeout_enters_pending_backoff(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_state = windows._WDA_STATE
        old_backoff = windows._WDA_STARTUP_BACKOFF_UNTIL
        windows._WDA_READY = False
        windows._WDA_RUNNER_PROCESS = None
        windows._WDA_STATE = "not_started"
        windows._WDA_STARTUP_BACKOFF_UNTIL = 0.0
        process = MagicMock()
        process.poll.return_value = None
        try:
            with patch.object(windows, "_inprocess_supported", return_value=False), \
                    patch.object(windows, "_run_pm3", side_effect=RuntimeError("not ready")), \
                    patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_wda_runner_bundle", return_value="com.example.Runner"), \
                    patch.object(windows, "_transport_cli_args", return_value=[]), \
                    patch("phone_harness.windows.subprocess.Popen", return_value=process), \
                    patch("phone_harness.windows.time.monotonic", side_effect=[0.0, 0.0, 36.0, 36.0]), \
                    patch("phone_harness.windows.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "35 seconds"):
                    windows._ensure_wda_runner()

            self.assertEqual(windows._WDA_STATE, "pending")
            self.assertGreater(windows._WDA_STARTUP_BACKOFF_UNTIL, 36.0)
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_STATE = old_state
            windows._WDA_STARTUP_BACKOFF_UNTIL = old_backoff

    def test_transport_metrics_expose_safe_wda_lifecycle_without_identifiers(self):
        old_state = windows._WDA_STATE
        old_process = windows._WDA_RUNNER_PROCESS
        process = MagicMock()
        process.poll.return_value = None
        windows._WDA_STATE = "starting"
        windows._WDA_RUNNER_PROCESS = process
        try:
            status = windows.runtime_transport_status()
        finally:
            windows._WDA_STATE = old_state
            windows._WDA_RUNNER_PROCESS = old_process

        self.assertEqual(status["wda_state"], "starting")
        self.assertTrue(status["wda_runner_alive"])
        self.assertNotIn("udid", repr(status).lower())

    def test_wda_startup_retains_runner_process_handle(self):
        old_ready = windows._WDA_READY
        old_process = getattr(windows, "_WDA_RUNNER_PROCESS", None)
        windows._WDA_READY = False
        if hasattr(windows, "_WDA_RUNNER_PROCESS"):
            windows._WDA_RUNNER_PROCESS = None
        runner_process = MagicMock()
        runner_process.poll.return_value = None
        try:
            with patch.object(
                windows,
                "_run_pm3",
                side_effect=[RuntimeError("not ready"), "{}"],
            ), patch.object(windows, "_require_device", return_value="device-1"), \
                    patch.object(windows, "_wda_runner_bundle", return_value="com.example.Runner"), \
                    patch.object(windows, "_developer_transport_cli_args", return_value=["--tunnel", "device-1"]), \
                    patch("phone_harness.windows.subprocess.Popen", return_value=runner_process) as popen, \
                    patch("phone_harness.windows.time.sleep"):
                windows._ensure_wda_runner()

            self.assertIs(windows._WDA_RUNNER_PROCESS, runner_process)
            command = popen.call_args.args[0]
            self.assertIn("--startup-timeout", command)
            self.assertEqual(command[command.index("--startup-timeout") + 1], "600")
        finally:
            windows._WDA_READY = old_ready
            if hasattr(windows, "_WDA_RUNNER_PROCESS"):
                windows._WDA_RUNNER_PROCESS = old_process

    def test_wda_startup_rediscovers_transport_after_failed_ready_probe(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        windows._WDA_READY = False
        windows._WDA_RUNNER_PROCESS = None
        runner_process = MagicMock()
        runner_process.poll.return_value = None
        try:
            with patch.object(
                windows,
                "_run_pm3",
                side_effect=[RuntimeError("not ready"), RuntimeError("still starting"), "{}"],
            ), patch.object(windows, "_require_device", return_value="device-1") as require_device, \
                    patch.object(windows, "_wda_runner_bundle", return_value="com.example.Runner"), \
                    patch.object(windows, "_developer_transport_cli_args", return_value=["--tunnel", "device-1"]), \
                    patch("phone_harness.windows.subprocess.Popen", return_value=runner_process), \
                    patch("phone_harness.windows.time.sleep"):
                windows._ensure_wda_runner()

            self.assertEqual(require_device.call_count, 2)
            self.assertTrue(windows._WDA_READY)
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process

    def test_wda_restart_is_scoped_to_phone_harness_runner_bundle(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_recovery_count = windows._WDA_RECOVERY_COUNT
        process = MagicMock()
        process.poll.return_value = None
        windows._WDA_READY = True
        windows._WDA_RUNNER_PROCESS = process
        windows._WDA_RECOVERY_COUNT = 0
        try:
            with patch.object(windows, "_wda_runner_bundle", return_value="com.iw.phoneharness.wda.Runner"), \
                    patch.object(windows, "_run_pm3") as run, \
                    patch.object(windows, "_stop_owned_process_tree") as stop_tree, \
                    patch.object(windows, "_ensure_wda_runner") as ensure:
                windows._restart_wda_runner()

            stop_tree.assert_called_once_with(process)
            run.assert_called_once_with(
                "developer", "dvt", "pkill", "com.iw.phoneharness.wda.Runner", "--bundle", timeout=10
            )
            ensure.assert_called_once_with()
            self.assertIsNone(windows._WDA_RUNNER_PROCESS)
            self.assertFalse(windows._WDA_READY)
            self.assertEqual(windows._WDA_RECOVERY_COUNT, 1)
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_RECOVERY_COUNT = old_recovery_count

    def test_shutdown_runtime_stops_only_owned_wda_runner(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_bundle = windows._WDA_RUNNER_BUNDLE
        process = MagicMock()
        process.poll.return_value = None
        windows._WDA_READY = True
        windows._WDA_RUNNER_PROCESS = process
        windows._WDA_RUNNER_BUNDLE = "com.iw.phoneharness.wda.Runner"
        try:
            with patch.object(windows, "_run_pm3") as run, patch.object(
                windows, "_stop_owned_process_tree"
            ) as stop_tree:
                windows.shutdown_runtime()
            run.assert_called_once_with(
                "developer", "dvt", "pkill", "com.iw.phoneharness.wda.Runner", "--bundle", timeout=3
            )
            stop_tree.assert_called_once_with(process, timeout=2)
            self.assertFalse(windows._WDA_READY)
            self.assertIsNone(windows._WDA_RUNNER_PROCESS)
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_RUNNER_BUNDLE = old_bundle

    def test_stop_owned_process_tree_uses_windows_taskkill_tree(self):
        process = MagicMock()
        process.pid = 1234
        process.poll.return_value = None
        process.wait.return_value = 0
        completed = CompletedProcess(["taskkill"], 0, stdout="", stderr="")
        with patch.object(windows.sys, "platform", "win32"), patch.object(
            windows.subprocess, "run", return_value=completed
        ) as run:
            windows._stop_owned_process_tree(process, timeout=2)
        run.assert_called_once_with(
            ["taskkill", "/PID", "1234", "/T"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        process.wait.assert_called_once_with(timeout=2)

    def test_wda_restart_fails_closed_when_orphan_cannot_be_stopped(self):
        old_ready = windows._WDA_READY
        old_process = windows._WDA_RUNNER_PROCESS
        old_recovery_count = windows._WDA_RECOVERY_COUNT
        windows._WDA_READY = True
        windows._WDA_RUNNER_PROCESS = None
        windows._WDA_RECOVERY_COUNT = 0
        try:
            with patch.object(windows, "_wda_runner_bundle", return_value="com.iw.phoneharness.wda.Runner"), \
                    patch.object(windows, "_run_pm3", side_effect=RuntimeError("pkill failed")), \
                    patch.object(windows, "_ensure_wda_runner") as ensure:
                with self.assertRaisesRegex(RuntimeError, "pkill failed"):
                    windows._restart_wda_runner()

            ensure.assert_not_called()
            self.assertEqual(windows._WDA_RECOVERY_COUNT, 0)
        finally:
            windows._WDA_READY = old_ready
            windows._WDA_RUNNER_PROCESS = old_process
            windows._WDA_RECOVERY_COUNT = old_recovery_count

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

    def test_wifi_developer_pm3_commands_use_tunneld_selector(self):
        old_udid = windows._DEVICE_UDID
        old_transport = windows._DEVICE_TRANSPORT
        old_rsd = windows._DEVICE_RSD
        windows._DEVICE_UDID = "device-1"
        windows._DEVICE_TRANSPORT = "wifi"
        windows._DEVICE_RSD = ("fd00::1", 12345)
        ok = CompletedProcess(["pm3"], 0, stdout="{}", stderr="")
        try:
            with patch("phone_harness.windows.subprocess.run", return_value=ok) as run:
                windows._run_pm3("developer", "wda", "status")
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ["--tunnel", "device-1"])
            self.assertNotIn("--rsd", command)
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

    def test_capture_prefers_inprocess_wda_png_when_runner_is_ready(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            buffer = BytesIO()
            Image.new("RGB", (24, 36), "black").save(buffer, format="PNG")
            old_ready = windows._WDA_READY
            try:
                windows._WDA_READY = True
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="26.6"), \
                        patch.object(windows, "_ensure_wda_runner") as ensure_wda, \
                        patch.object(windows, "_inprocess_wda_screenshot", return_value=buffer.getvalue()), \
                        patch.object(windows, "_run_pm3") as core_capture:
                    result, bounds = windows.capture(path)
            finally:
                windows._WDA_READY = old_ready

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 24, "h": 36})
            ensure_wda.assert_called_once_with()
            core_capture.assert_not_called()

    def test_ios26_capture_starts_wda_instead_of_falling_back_to_coredevice(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            buffer = BytesIO()
            Image.new("RGB", (24, 36), "black").save(buffer, format="PNG")
            old_ready = windows._WDA_READY
            try:
                windows._WDA_READY = False
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="26.6"), \
                        patch.object(windows, "_ensure_wda_runner") as ensure_wda, \
                        patch.object(windows, "_inprocess_wda_screenshot", return_value=buffer.getvalue()), \
                        patch.object(windows, "_run_pm3") as core_capture:
                    result, bounds = windows.capture(path)
            finally:
                windows._WDA_READY = old_ready

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 24, "h": 36})
            ensure_wda.assert_called_once_with()
            core_capture.assert_not_called()

    def test_ios26_capture_restarts_wda_once_when_ui_testing_authorization_is_stale(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            buffer = BytesIO()
            Image.new("RGB", (24, 36), "black").save(buffer, format="PNG")
            unauthorized = RuntimeError(
                'WDA error (status=500): Error Domain=XCTDaemonErrorDomain Code=41 '
                '"Not authorized for performing UI testing actions."'
            )
            old_ready = windows._WDA_READY
            try:
                windows._WDA_READY = True
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="26.6"), \
                        patch.object(windows, "_ensure_wda_runner") as ensure_wda, \
                        patch.object(windows, "_restart_wda_runner") as restart_wda, \
                        patch.object(
                            windows,
                            "_inprocess_wda_screenshot",
                            side_effect=[unauthorized, buffer.getvalue()],
                        ) as screenshot, \
                        patch.object(windows, "_run_pm3") as core_capture:
                    result, bounds = windows.capture(path)
            finally:
                windows._WDA_READY = old_ready

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 24, "h": 36})
            ensure_wda.assert_called_once_with()
            restart_wda.assert_called_once_with()
            self.assertEqual(screenshot.call_count, 2)
            core_capture.assert_not_called()

    def test_capture_promotes_already_running_wda_for_visual_only_process(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            buffer = BytesIO()
            Image.new("RGB", (24, 36), "black").save(buffer, format="PNG")
            old_ready = windows._WDA_READY
            old_backoff = windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL
            try:
                windows._WDA_READY = False
                windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL = 0.0
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="27.0"), \
                        patch.object(windows, "_inprocess_wda_screenshot", return_value=buffer.getvalue()) as screenshot, \
                        patch.object(windows, "_run_pm3") as core_capture:
                    result, bounds = windows.capture(path)
            finally:
                windows._WDA_READY = old_ready
                windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL = old_backoff

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 24, "h": 36})
            screenshot.assert_called_once_with(timeout=1)
            core_capture.assert_not_called()

    def test_failed_capture_wda_probe_uses_backoff_and_coredevice_fallback(self):
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            old_ready = windows._WDA_READY
            old_backoff = windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL

            def fake_core_capture(*args, **kwargs):
                Image.new("RGB", (30, 45), "black").save(Path(args[-1]))

            try:
                windows._WDA_READY = False
                windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL = 0.0
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="27.0"), \
                        patch.object(windows, "_inprocess_wda_screenshot", side_effect=RuntimeError("not ready")) as screenshot, \
                        patch.object(windows, "_run_pm3", side_effect=fake_core_capture):
                    windows.capture(first)
                    windows.capture(second)
            finally:
                windows._WDA_READY = old_ready
                windows._WDA_CAPTURE_PROBE_BACKOFF_UNTIL = old_backoff

            screenshot.assert_called_once_with(timeout=1)

    def test_capture_falls_back_to_coredevice_when_wda_png_fails(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            old_ready = windows._WDA_READY

            def fake_core_capture(*args, **kwargs):
                Image.new("RGB", (30, 45), "black").save(Path(args[-1]))

            try:
                windows._WDA_READY = True
                with patch.object(windows, "_require_device", return_value="device-1"), \
                        patch.object(windows, "_inprocess_supported", return_value=True), \
                        patch.object(windows, "_product_version", return_value="27.0"), \
                        patch.object(windows, "_inprocess_wda_screenshot", side_effect=RuntimeError("wda screenshot failed")), \
                        patch.object(windows, "_run_pm3", side_effect=fake_core_capture) as core_capture:
                    result, bounds = windows.capture(path)
            finally:
                windows._WDA_READY = old_ready

            self.assertEqual(result, str(path))
            self.assertEqual(bounds, {"x": 0, "y": 0, "w": 30, "h": 45})
            core_capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
