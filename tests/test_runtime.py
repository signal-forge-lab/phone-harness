import time
import unittest
from unittest.mock import patch

from phone_harness.runtime import PhoneRuntime


class RuntimeTests(unittest.TestCase):
    def test_observe_reuses_short_lived_snapshot(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "8", "source": "accessibility", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements) as read:
            first = runtime.observe()
            second = runtime.observe()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        read.assert_called_once_with()

    def test_tap_text_batch_preflights_before_mutating(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [
            {"text": "1", "source": "accessibility", "name": "one", "x": 10, "y": 20},
            {"text": "2", "source": "accessibility", "name": "two", "x": 30, "y": 20},
        ]
        runtime._observation = {"source": "accessibility", "elements": elements}
        runtime._observed_at = time.monotonic()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.tap_accessibility_batch") as tap:
            result = runtime.act([
                {"op": "tap_text", "text": "1", "exact": True},
                {"op": "tap_text", "text": "2", "exact": True},
            ])
        self.assertEqual(result["count"], 2)
        tap.assert_called_once()
        self.assertEqual([item["text"] for item in tap.call_args.args[0]], ["1", "2"])

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
