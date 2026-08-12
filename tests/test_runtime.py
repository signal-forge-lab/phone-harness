import time
import unittest
from unittest.mock import patch

from phone_harness.runtime import PhoneRuntime, RUNTIME_CONTRACT_VERSION


class RuntimeTests(unittest.TestCase):
    def test_observe_reuses_short_lived_snapshot(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "8", "source": "accessibility", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements) as read:
            first = runtime.observe()
            second = runtime.observe()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["contract_version"], RUNTIME_CONTRACT_VERSION)
        read.assert_called_once_with()

    def test_status_exposes_contract_and_tunneld_health(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.connection_state", return_value="ready"), \
                patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.transport_mode", return_value="wifi"), \
                patch("phone_harness.windows.tunneld_status", return_value={"reachable": True, "device_count": 1}), \
                patch("phone_harness.windows.active_transport", return_value="wifi"):
            status = runtime.status()
        self.assertEqual(status["contract_version"], RUNTIME_CONTRACT_VERSION)
        self.assertEqual(status["tunneld"], {"reachable": True, "device_count": 1})
        self.assertEqual(status["active_transport"], "wifi")

    def test_tap_text_batch_preflights_before_mutating(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [
            {"text": "1", "source": "accessibility", "name": "one", "x": 10, "y": 20},
            {"text": "2", "source": "accessibility", "name": "two", "x": 30, "y": 20},
        ]
        runtime._observation = {"source": "accessibility", "elements": elements}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.windows.tap_accessibility_batch") as tap:
            result = runtime.act([
                {"op": "tap_text", "text": "1", "exact": True},
                {"op": "tap_text", "text": "2", "exact": True},
            ])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["contract_version"], RUNTIME_CONTRACT_VERSION)
        tap.assert_called_once()
        self.assertEqual([item["text"] for item in tap.call_args.args[0]], ["1", "2"])

    def test_ios_26_batches_accessibility_type_and_swipe_in_one_wda_call(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "Search", "source": "accessibility", "name": "search", "x": 10, "y": 20}]
        runtime._observation = {"source": "accessibility", "elements": elements}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=True), \
                patch("phone_harness.windows._wda_action_for_accessibility", return_value={"op": "tap", "selector": "search"}), \
                patch("phone_harness.windows._wda_action_for_swipe", return_value={"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4}), \
                patch("phone_harness.windows.run_wda_runtime_batch") as run:
            result = runtime.act([
                {"op": "tap_text", "text": "Search", "exact": True},
                {"op": "type_text", "text": "Bluetooth"},
                {"op": "swipe", "direction": "up"},
            ])
        run.assert_called_once_with([
            {"op": "tap", "selector": "search"},
            {"op": "type", "text": "Bluetooth"},
            {"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4},
        ])
        self.assertEqual(result["count"], 3)

    def test_ios_27_keeps_native_helpers_instead_of_wda_batch(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.windows.run_wda_runtime_batch") as wda_batch, \
                patch("phone_harness.runtime.helpers.type_text") as type_text, \
                patch("phone_harness.runtime.helpers.swipe") as swipe:
            result = runtime.act([
                {"op": "type_text", "text": "abc"},
                {"op": "swipe", "direction": "up"},
            ])
        wda_batch.assert_not_called()
        type_text.assert_called_once_with("abc")
        swipe.assert_called_once_with("up", distance=0.4)
        self.assertEqual(result["count"], 2)

    def test_missing_tap_text_fails_before_any_action(self):
        runtime = PhoneRuntime(observation_ttl=10)
        runtime._observation = {"source": "accessibility", "elements": [{"text": "1"}]}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.helpers.tap") as tap:
            with self.assertRaisesRegex(RuntimeError, "no visible text"):
                runtime.act([
                    {"op": "tap", "x": 1, "y": 2},
                    {"op": "tap_text", "text": "missing"},
                ])
        tap.assert_not_called()

    def test_invalid_later_action_fails_before_any_action(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.tap") as tap:
            with self.assertRaisesRegex(ValueError, "type_text requires text"):
                runtime.act([
                    {"op": "tap", "x": 1, "y": 2},
                    {"op": "type_text"},
                ])
        tap.assert_not_called()

    def test_mutating_batch_invalidates_observation(self):
        runtime = PhoneRuntime(observation_ttl=10)
        runtime._observation = {"source": "ocr", "elements": []}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.helpers.tap"):
            runtime.act([{"op": "tap", "x": 1, "y": 2}])
        self.assertIsNone(runtime._observation)

    def test_batch_size_is_bounded(self):
        runtime = PhoneRuntime()
        with self.assertRaisesRegex(ValueError, "at most 100"):
            runtime.act([{"op": "home"}] * 101)


if __name__ == "__main__":
    unittest.main()
