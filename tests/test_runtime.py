import time
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phone_harness.runtime import PhoneRuntime, PhoneRuntimeError, RUNTIME_CONTRACT_VERSION


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._status_dir = TemporaryDirectory()
        self._status_patch = patch(
            "phone_harness.runtime.STATUS_FILE",
            Path(self._status_dir.name) / "runtime-status.json",
        )
        self._status_patch.start()

    def tearDown(self):
        self._status_patch.stop()
        self._status_dir.cleanup()

    def test_monitor_file_exposes_safe_runtime_state_without_screen_text(self):
        with TemporaryDirectory() as directory:
            status_file = Path(directory) / "runtime-status.json"
            runtime = PhoneRuntime(observation_ttl=10)
            elements = [{"text": "PRIVATE SCREEN TEXT", "source": "accessibility", "x": 10, "y": 20}]
            with patch("phone_harness.runtime.STATUS_FILE", status_file), \
                    patch("phone_harness.runtime.helpers.elements", return_value=elements):
                observed = runtime.observe()

            state = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(state["schema_version"], 1)
            self.assertEqual(state["contract_version"], RUNTIME_CONTRACT_VERSION)
            self.assertEqual(state["last_observation"]["observation_id"], observed["observation_id"])
            self.assertEqual(state["last_observation"]["source"], "accessibility")
            self.assertEqual(state["last_observation"]["element_count"], 1)
            self.assertNotIn("PRIVATE SCREEN TEXT", status_file.read_text(encoding="utf-8"))
            self.assertIn("runtime_id", state)
            self.assertIn("pid", state)

    def test_monitor_file_records_machine_error_without_raw_message(self):
        with TemporaryDirectory() as directory:
            status_file = Path(directory) / "runtime-status.json"
            runtime = PhoneRuntime()
            with patch("phone_harness.runtime.STATUS_FILE", status_file), \
                    patch("phone_harness.runtime.sys.platform", "darwin"), \
                    patch("phone_harness.runtime.helpers.type_text", side_effect=RuntimeError("secret-ish low-level detail")):
                with self.assertRaises(PhoneRuntimeError):
                    runtime.act([{"op": "type_text", "text": "abc"}])
            text = status_file.read_text(encoding="utf-8")
            state = json.loads(text)
            self.assertFalse(state["last_action"]["ok"])
            self.assertEqual(state["last_action"]["error_code"], "ACTION_FAILED")
            self.assertNotIn("secret-ish low-level detail", text)

    def test_monitor_file_exposes_current_operation_without_action_payload(self):
        with TemporaryDirectory() as directory:
            status_file = Path(directory) / "runtime-status.json"
            runtime = PhoneRuntime()

            def inspect_during_action(_text):
                state = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertEqual(state["current_operation"]["kind"], "act")
                self.assertEqual(state["current_operation"]["phase"], "execute")
                self.assertEqual(state["current_operation"]["backend"], "sequential")
                self.assertEqual(state["current_operation"]["action_count"], 1)
                self.assertNotIn("PRIVATE ACTION TEXT", status_file.read_text(encoding="utf-8"))

            with patch("phone_harness.runtime.STATUS_FILE", status_file), \
                    patch("phone_harness.runtime.sys.platform", "darwin"), \
                    patch("phone_harness.runtime.helpers.type_text", side_effect=inspect_during_action):
                runtime.act([{"op": "type_text", "text": "PRIVATE ACTION TEXT"}])

            state = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertIsNone(state["current_operation"])
            self.assertTrue(state["last_action"]["ok"])

    def test_observe_reuses_short_lived_snapshot(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "8", "source": "accessibility", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements) as read:
            first = runtime.observe()
            second = runtime.observe()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["contract_version"], RUNTIME_CONTRACT_VERSION)
        self.assertEqual(first["observation_id"], second["observation_id"])
        self.assertGreaterEqual(first["duration_ms"], 0)
        read.assert_called_once_with()

    def test_forced_observe_advances_observation_id(self):
        runtime = PhoneRuntime(observation_ttl=10)
        with patch("phone_harness.runtime.helpers.elements", return_value=[]):
            first = runtime.observe()
            second = runtime.observe(force=True)
        self.assertGreater(second["observation_id"], first["observation_id"])

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
        self.assertGreaterEqual(status["duration_ms"], 0)
        from phone_harness import runtime as runtime_module
        state = json.loads(runtime_module.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["health"]["tunneld"], {"reachable": True, "device_count": 1})

    def test_tap_text_batch_preflights_before_mutating(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [
            {"text": "1", "source": "accessibility", "name": "one", "x": 10, "y": 20},
            {"text": "2", "source": "accessibility", "name": "two", "x": 30, "y": 20},
        ]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observation = runtime.observe()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.windows.tap_accessibility_batch") as tap:
            result = runtime.act([
                {"op": "tap_text", "text": "1", "exact": True},
                {"op": "tap_text", "text": "2", "exact": True},
            ], observation_id=observation["observation_id"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["contract_version"], RUNTIME_CONTRACT_VERSION)
        self.assertEqual(result["consumed_observation_id"], observation["observation_id"])
        self.assertGreaterEqual(result["duration_ms"], 0)
        tap.assert_called_once()
        self.assertEqual([item["text"] for item in tap.call_args.args[0]], ["1", "2"])

    def test_ios_26_batches_accessibility_type_and_swipe_in_one_wda_call(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "Search", "source": "accessibility", "name": "search", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observation = runtime.observe()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=True), \
                patch("phone_harness.windows._wda_action_for_accessibility", return_value={"op": "tap", "selector": "search"}), \
                patch("phone_harness.windows._wda_action_for_swipe", return_value={"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4}), \
                patch("phone_harness.windows.run_wda_runtime_batch") as run:
            result = runtime.act([
                {"op": "tap_text", "text": "Search", "exact": True},
                {"op": "type_text", "text": "Bluetooth"},
                {"op": "swipe", "direction": "up"},
            ], observation_id=observation["observation_id"])
        run.assert_called_once_with([
            {"op": "tap", "selector": "search"},
            {"op": "type", "text": "Bluetooth"},
            {"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4},
        ])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["backend"], "wda_batch")

    def test_ios_26_batches_raw_tap_drag_and_scroll_in_one_wda_call(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=True), \
                patch("phone_harness.windows._wda_action_for_tap", return_value={"op": "tap-coordinate", "x": 1, "y": 2}), \
                patch("phone_harness.windows._wda_action_for_drag", return_value={"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4, "duration": 0.5}), \
                patch("phone_harness.windows._wda_action_for_scroll", return_value={"op": "swipe", "start_x": 5, "start_y": 6, "end_x": 7, "end_y": 8, "duration": 0.35}), \
                patch("phone_harness.windows.run_wda_runtime_batch") as run:
            result = runtime.act([
                {"op": "tap", "x": 10, "y": 20},
                {"op": "drag", "x1": 10, "y1": 20, "x2": 30, "y2": 40, "duration": 0.5},
                {"op": "scroll", "amount": 300},
            ])
        run.assert_called_once_with([
            {"op": "tap-coordinate", "x": 1, "y": 2},
            {"op": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4, "duration": 0.5},
            {"op": "swipe", "start_x": 5, "start_y": 6, "end_x": 7, "end_y": 8, "duration": 0.35},
        ])
        self.assertEqual(result["backend"], "wda_batch")

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
        self.assertEqual(result["backend"], "sequential")

    def test_missing_tap_text_fails_before_any_action(self):
        runtime = PhoneRuntime(observation_ttl=10)
        with patch("phone_harness.runtime.helpers.elements", return_value=[{"text": "1"}]):
            observation = runtime.observe()
        with patch("phone_harness.runtime.helpers.tap") as tap:
            with self.assertRaises(PhoneRuntimeError) as raised:
                runtime.act([
                    {"op": "tap", "x": 1, "y": 2},
                    {"op": "tap_text", "text": "missing"},
                ], observation_id=observation["observation_id"])
        self.assertEqual(raised.exception.code, "TARGET_NOT_FOUND")
        self.assertEqual(raised.exception.phase, "preflight")
        self.assertEqual(raised.exception.completed_actions, 0)
        tap.assert_not_called()

    def test_tap_text_requires_current_observation_id(self):
        runtime = PhoneRuntime(observation_ttl=10)
        with patch("phone_harness.runtime.helpers.elements", return_value=[{"text": "1"}]):
            observation = runtime.observe()
        runtime.invalidate()
        with self.assertRaises(PhoneRuntimeError) as raised:
            runtime.act([{"op": "tap_text", "text": "1"}], observation_id=observation["observation_id"])
        self.assertEqual(raised.exception.code, "STALE_OBSERVATION")
        self.assertTrue(raised.exception.retryable)

    def test_tap_text_without_observation_id_is_rejected(self):
        runtime = PhoneRuntime()
        with self.assertRaises(PhoneRuntimeError) as raised:
            runtime.act([{"op": "tap_text", "text": "1"}])
        self.assertEqual(raised.exception.code, "STALE_OBSERVATION")

    def test_runtime_error_is_machine_readable(self):
        error = PhoneRuntimeError(
            "ACTION_FAILED", "boom", retryable=False, phase="execute", action_index=2, completed_actions=2
        )
        self.assertEqual(error.to_dict(), {
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "error": {
                "code": "ACTION_FAILED",
                "message": "boom",
                "retryable": False,
                "phase": "execute",
                "action_index": 2,
                "completed_actions": 2,
            },
        })

    def test_stale_id_is_rejected_for_raw_observe_derived_action_when_supplied(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.elements", return_value=[]):
            observation = runtime.observe()
        runtime.invalidate()
        with self.assertRaises(PhoneRuntimeError) as raised:
            runtime.act([{"op": "tap", "x": 10, "y": 20}], observation_id=observation["observation_id"])
        self.assertEqual(raised.exception.code, "STALE_OBSERVATION")

    def test_invalid_later_action_fails_before_any_action(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.tap") as tap:
            with self.assertRaises(PhoneRuntimeError) as raised:
                runtime.act([
                    {"op": "tap", "x": 1, "y": 2},
                    {"op": "type_text"},
                ])
        self.assertEqual(raised.exception.code, "INVALID_REQUEST")
        self.assertEqual(raised.exception.phase, "preflight")
        tap.assert_not_called()

    def test_execution_failure_reports_action_index_without_marking_retryable(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.runtime.helpers.tap"), \
                patch("phone_harness.runtime.helpers.type_text", side_effect=RuntimeError("device failed")):
            with self.assertRaises(PhoneRuntimeError) as raised:
                runtime.act([
                    {"op": "tap", "x": 1, "y": 2},
                    {"op": "type_text", "text": "abc"},
                ])
        self.assertEqual(raised.exception.code, "ACTION_FAILED")
        self.assertEqual(raised.exception.action_index, 1)
        self.assertEqual(raised.exception.completed_actions, 1)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(str(raised.exception), "phone action failed")

    def test_mutating_batch_invalidates_observation(self):
        runtime = PhoneRuntime(observation_ttl=10)
        runtime._observation = {"source": "ocr", "elements": []}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.runtime.helpers.tap"):
            runtime.act([{"op": "tap", "x": 1, "y": 2}])
        self.assertIsNone(runtime._observation)

    def test_batch_size_is_bounded(self):
        runtime = PhoneRuntime()
        with self.assertRaises(PhoneRuntimeError) as raised:
            runtime.act([{"op": "home"}] * 101)
        self.assertEqual(raised.exception.code, "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
