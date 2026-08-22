import time
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from phone_harness.runtime import PhoneRuntime, PhoneRuntimeError, RUNTIME_CONTRACT_VERSION


class RuntimeTests(unittest.TestCase):
    def test_operator_answer_can_be_promoted_into_pending_human_teaching(self):
        runtime = PhoneRuntime()
        runtime.operator_broker = MagicMock()
        runtime.operator_broker.is_present.return_value = True
        runtime.operator_broker.ask.return_value = {
            "answer": "これはバブルです。マージ後に近くへ追加で出ます。",
            "choice": None,
        }
        runtime.teaching_inbox = MagicMock()
        runtime.teaching_inbox.submit.return_value = {"message_id": "teach-from-question"}

        result = runtime.ask_operator(
            "これは何ですか？",
            context="merge outcome ambiguous",
            promote_answer_to_teaching=True,
        )

        self.assertTrue(result["answered"])
        self.assertEqual(result["teaching_message_id"], "teach-from-question")
        submitted = runtime.teaching_inbox.submit.call_args.args[0]
        self.assertIn("これは何ですか？", submitted)
        self.assertIn("これはバブルです", submitted)
        kwargs = runtime.teaching_inbox.submit.call_args.kwargs
        self.assertEqual(kwargs["source"], "operator_question_answer")
        self.assertFalse(kwargs["blocking"])

    def test_pending_human_teaching_ignores_nonblocking_question_answers(self):
        runtime = PhoneRuntime()
        runtime.teaching_inbox = MagicMock()
        runtime.teaching_inbox.list.return_value = [
            {
                "message_id": "late-answer",
                "text": "回答",
                "status": "pending",
                "blocking": False,
            },
            {
                "message_id": "operator-message",
                "text": "停止して見てほしい",
                "status": "pending",
                "blocking": True,
            },
        ]
        pending = runtime.pending_human_teaching()
        self.assertEqual(pending["message_id"], "operator-message")

    def test_pause_checkpoint_detects_pause_that_already_resumed_and_invalidates_observation(self):
        initial = {
            "schema_version": 1,
            "paused": False,
            "changed_unix": None,
            "source": None,
            "pause_generation": 0,
            "last_pause_unix": None,
        }
        resumed_after_pause = {
            "schema_version": 1,
            "paused": False,
            "changed_unix": 11.0,
            "source": "monitor-web",
            "pause_generation": 1,
            "last_pause_unix": 10.0,
        }
        with patch("phone_harness.runtime.load_runtime_control", return_value=initial):
            runtime = PhoneRuntime()
        runtime._observation = {"source": "visual", "elements": []}
        runtime._observation_id = 9

        with patch("phone_harness.runtime.load_runtime_control", return_value=resumed_after_pause):
            checkpoint = runtime.pause_checkpoint(boundary="test")

        self.assertTrue(checkpoint["replan_required"])
        self.assertEqual(checkpoint["wait_ms"], 0.0)
        self.assertEqual(checkpoint["pause_generation"], 1)
        self.assertIsNone(runtime._observation)
        self.assertIsNone(runtime._observation_id)

    def test_run_workflow_rejects_unknown_name(self):
        runtime = PhoneRuntime()
        with self.assertRaisesRegex(PhoneRuntimeError, "unsupported workflow"):
            runtime.run_workflow("unknown")

    def test_run_workflow_rejects_unknown_option_before_device_work(self):
        runtime = PhoneRuntime()
        with self.assertRaisesRegex(PhoneRuntimeError, "unsupported merge_boss_once option"):
            runtime.run_workflow("merge_boss_once", {"unexpected": True})

    def test_merge_boss_turn_reports_not_ready_perception_without_device_work(self):
        runtime = PhoneRuntime()
        fake = MagicMock()
        fake.status.return_value = {
            "ready": False,
            "reason": "real_device_perception_calibration_required",
            "pending_checks": ["calibrate the full horizontally-scrollable customer/order strip"],
        }
        with patch(
            "phone_harness.workflows.merge_boss_perception.MergeBossLivePerception",
            return_value=fake,
        ):
            result = runtime.run_workflow(
                "merge_boss_turn",
                {"max_cycles": 8, "max_merges": 16, "max_emissions": 20},
            )
        self.assertFalse(result["executed"])
        self.assertEqual(result["reason"], "real_device_perception_calibration_required")
        self.assertTrue(any("customer/order strip" in item for item in result["pending_checks"]))

    def test_merge_boss_turn_reuses_perception_across_workflow_calls(self):
        runtime = PhoneRuntime()
        perception = MagicMock()
        perception.status.return_value = {"ready": True}
        controller = MagicMock()
        controller.run.return_value = {"cycles": [], "duration_ms": 1.0}
        with patch(
            "phone_harness.workflows.merge_boss_perception.MergeBossLivePerception",
            return_value=perception,
        ) as perception_type, patch(
            "phone_harness.workflows.merge_boss_control.MergeBossTurnController",
            return_value=controller,
        ) as controller_type:
            first = runtime.run_workflow("merge_boss_turn", {"max_cycles": 1})
            second = runtime.run_workflow("merge_boss_turn", {"max_cycles": 1})

        self.assertTrue(first["executed"])
        self.assertTrue(second["executed"])
        perception_type.assert_called_once_with(runtime)
        self.assertEqual(controller_type.call_count, 2)
        self.assertIs(controller_type.call_args_list[0].args[1], perception)
        self.assertIs(controller_type.call_args_list[1].args[1], perception)

    def setUp(self):
        self._status_dir = TemporaryDirectory()
        self._status_patch = patch(
            "phone_harness.runtime.STATUS_FILE",
            Path(self._status_dir.name) / "runtime-status.json",
        )
        self._status_patch.start()
        # Runtime tests must never inherit a real operator Pause from the live
        # monitor. Otherwise a unit test that calls act() correctly waits on
        # production control state and the suite appears to deadlock.
        self._control_dir = TemporaryDirectory()
        self._control_patch = patch(
            "phone_harness.runtime_control.DEFAULT_CONTROL_FILE",
            Path(self._control_dir.name) / "control.json",
        )
        self._control_patch.start()

    def tearDown(self):
        self._control_patch.stop()
        self._control_dir.cleanup()
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

    def test_observe_prunes_long_document_content_but_keeps_internal_snapshot(self):
        runtime = PhoneRuntime(observation_ttl=10)
        private_text = "secret " * 100
        elements = [{
            "text": private_text,
            "value": private_text,
            "source": "accessibility",
            "role": "XCUIElementTypeTextView",
            "x": 10, "y": 20, "w": 100, "h": 200,
        }]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observed = runtime.observe()
        self.assertEqual(observed["elements"][0]["text"], "[text content hidden]")
        self.assertTrue(observed["elements"][0]["content_hidden"])
        self.assertNotIn("secret", repr(observed))
        self.assertIn("secret", runtime._observation["elements"][0]["text"])

    def test_observe_hides_short_document_body_by_default(self):
        runtime = PhoneRuntime()
        elements = [{"text": "short private note", "value": "short private note", "source": "accessibility", "role": "XCUIElementTypeTextView", "x": 1, "y": 2}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observed = runtime.observe()
        self.assertEqual(observed["elements"][0]["text"], "[text content hidden]")

    def test_observe_can_explicitly_include_document_content(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "document body", "value": "document body", "source": "accessibility", "role": "XCUIElementTypeTextView", "x": 1, "y": 2}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observed = runtime.observe(include_text_content=True)
        self.assertEqual(observed["elements"][0]["text"], "document body")

    def test_observe_assigns_element_refs(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.elements", return_value=[{"text": "Battery", "source": "accessibility", "role": "XCUIElementTypeButton", "x": 1, "y": 2}]):
            observed = runtime.observe()
        self.assertEqual(observed["elements"][0]["element_ref"], "e1")

    def test_observe_reports_hybrid_source_for_mixed_elements(self):
        runtime = PhoneRuntime()
        elements = [
            {"text": "Button", "source": "accessibility", "role": "XCUIElementTypeButton", "x": 1, "y": 2},
            {"text": "Canvas label", "source": "ocr", "x": 3, "y": 4},
        ]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observed = runtime.observe()
        self.assertEqual(observed["source"], "hybrid")

    def test_observe_region_uses_region_as_cache_scope(self):
        runtime = PhoneRuntime(observation_ttl=10)
        first_region = {"x": 10.0, "y": 20.0, "w": 100.0, "h": 200.0}
        second_region = {"x": 20.0, "y": 20.0, "w": 100.0, "h": 200.0}
        elements = [{"text": "Target", "source": "ocr", "x": 30, "y": 40}]
        with patch("phone_harness.runtime.helpers.normalize_region", side_effect=lambda region: dict(region)), \
                patch("phone_harness.runtime.helpers.elements", return_value=elements) as read:
            first = runtime.observe(region=first_region)
            cached = runtime.observe(region=first_region)
            second = runtime.observe(region=second_region)

        self.assertEqual(first["region"], first_region)
        self.assertTrue(cached["cached"])
        self.assertFalse(second["cached"])
        self.assertNotEqual(first["observation_id"], second["observation_id"])
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args_list[0].kwargs, {"region": first_region})
        self.assertEqual(read.call_args_list[1].kwargs, {"region": second_region})

    def test_observe_include_image_reuses_one_frame_for_elements_and_image(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=10)
            image_path = Path(directory) / "frame.png"
            image_path.write_bytes(b"png")
            frame = MagicMock()
            frame.capture_ms = 12.5
            frame.variant.return_value = image_path
            elements = [{"text": "Target", "source": "ocr", "x": 30, "y": 40}]
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame) as capture_frame, \
                    patch("phone_harness.runtime.helpers.elements", return_value=elements) as read:
                observed = runtime.observe(include_image=True)

            capture_frame.assert_called_once_with()
            read.assert_called_once_with(frame=frame)
            frame.variant.assert_called_once_with(image_format="PNG")
            self.assertEqual(observed["_image_path"], str(image_path))
            self.assertEqual(observed["image_bytes"], 3)
            self.assertEqual(observed["capture_ms"], 12.5)

    def test_cached_image_observation_reuses_retained_frame(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=10)
            image_path = Path(directory) / "frame.png"
            image_path.write_bytes(b"png")
            frame = MagicMock()
            frame.capture_ms = 4.0
            frame.variant.return_value = image_path
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame) as capture_frame, \
                    patch("phone_harness.runtime.helpers.elements", return_value=[]):
                first = runtime.observe(include_image=True)
                second = runtime.observe(include_image=True)

            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            capture_frame.assert_called_once_with()
            self.assertEqual(first["observation_id"], second["observation_id"])

    def test_visual_observe_skips_semantic_analysis_and_returns_coarse_jpeg(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=10)
            image_path = Path(directory) / "glance.jpg"
            image_path.write_bytes(b"jpeg-bytes")
            frame = MagicMock()
            frame.capture_ms = 3.25
            frame.variant.return_value = image_path
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame), \
                    patch("phone_harness.runtime.helpers.elements") as read:
                observed = runtime.observe(mode="visual", image_profile="glance")

            read.assert_not_called()
            frame.variant.assert_called_once_with(
                max_long_edge=256,
                grayscale=False,
                image_format="JPEG",
                quality=28,
            )
            self.assertEqual(observed["source"], "visual")
            self.assertEqual(observed["elements"], [])
            self.assertEqual(observed["_image_path"], str(image_path))
            self.assertEqual(observed["_image_mime_type"], "image/jpeg")
            self.assertEqual(observed["image_profile"], "glance")
            self.assertEqual(observed["image_bytes"], len(b"jpeg-bytes"))
            self.assertEqual(observed["capture_ms"], 3.25)
            self.assertGreaterEqual(observed["analysis_ms"], 0)
            self.assertGreaterEqual(observed["image_prepare_ms"], 0)

    def test_visual_and_semantic_observations_do_not_share_cache_scope(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=10)
            image_path = Path(directory) / "glance.jpg"
            image_path.write_bytes(b"jpeg")
            frame = MagicMock()
            frame.capture_ms = 1.0
            frame.variant.return_value = image_path
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame), \
                    patch("phone_harness.runtime.helpers.elements", return_value=[{"text": "A", "source": "accessibility"}]) as read:
                visual = runtime.observe(mode="visual", image_profile="glance")
                semantic = runtime.observe()

            self.assertFalse(visual["cached"])
            self.assertFalse(semantic["cached"])
            self.assertNotEqual(visual["observation_id"], semantic["observation_id"])
            read.assert_called_once_with()

    def test_visual_refinement_reuses_retained_frame_after_ttl_without_recapture(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=0.01)
            glance = Path(directory) / "glance.jpg"
            detail = Path(directory) / "detail.jpg"
            glance.write_bytes(b"g")
            detail.write_bytes(b"detail")
            frame = MagicMock()
            frame.capture_ms = 7.0
            frame.variant.side_effect = [glance, detail]
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame) as capture_frame, \
                    patch("phone_harness.runtime.time.monotonic", side_effect=[1.0, 2.0]), \
                    patch("phone_harness.runtime.helpers.elements") as read:
                first = runtime.observe(mode="visual", image_profile="glance")
                refined = runtime.observe(
                    mode="visual",
                    image_profile="detail",
                    reuse_observation_id=first["observation_id"],
                )

            read.assert_not_called()
            capture_frame.assert_called_once_with()
            self.assertEqual(refined["observation_id"], first["observation_id"])
            self.assertTrue(refined["cached"])
            self.assertTrue(refined["reused_observation"])
            self.assertEqual(refined["image_profile"], "detail")
            self.assertEqual(refined["image_bytes"], len(b"detail"))

    def test_visual_refinement_rejects_stale_observation_id(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=10)
            glance = Path(directory) / "glance.jpg"
            glance.write_bytes(b"g")
            frame = MagicMock()
            frame.capture_ms = 1.0
            frame.variant.return_value = glance
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame):
                first = runtime.observe(mode="visual", image_profile="glance")
                with self.assertRaisesRegex(PhoneRuntimeError, "current retained visual observation") as error:
                    runtime.observe(
                        mode="visual",
                        image_profile="detail",
                        reuse_observation_id=first["observation_id"] + 1,
                    )
            self.assertEqual(error.exception.code, "STALE_OBSERVATION")

    def test_visual_refinement_rejects_retained_frame_older_than_reuse_ttl(self):
        with TemporaryDirectory() as directory:
            runtime = PhoneRuntime(observation_ttl=0.01, visual_reuse_ttl=30.0)
            glance = Path(directory) / "glance.jpg"
            glance.write_bytes(b"g")
            frame = MagicMock()
            frame.capture_ms = 1.0
            frame.variant.return_value = glance
            with patch("phone_harness.runtime.helpers.capture_frame", return_value=frame), \
                    patch("phone_harness.runtime.time.monotonic", side_effect=[1.0, 32.0]):
                first = runtime.observe(mode="visual", image_profile="glance")
                with self.assertRaises(PhoneRuntimeError) as error:
                    runtime.observe(
                        mode="visual",
                        image_profile="detail",
                        reuse_observation_id=first["observation_id"],
                    )
            self.assertEqual(error.exception.code, "STALE_OBSERVATION")

    def test_observe_redacts_luhn_valid_payment_card_text(self):
        runtime = PhoneRuntime()
        elements = [{"text": "4111 1111 1111 1111", "source": "accessibility", "role": "XCUIElementTypeLink", "x": 1, "y": 2}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observed = runtime.observe()
        self.assertEqual(observed["elements"][0]["text"], "[payment card redacted]")
        self.assertNotIn("4111", repr(observed))

    def test_forced_observe_advances_observation_id(self):
        runtime = PhoneRuntime(observation_ttl=10)
        with patch("phone_harness.runtime.helpers.elements", return_value=[]):
            first = runtime.observe()
            second = runtime.observe(force=True)
        self.assertGreater(second["observation_id"], first["observation_id"])

    def test_status_exposes_contract_and_tunneld_health(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.connection_state", return_value="ready"), \
                patch("phone_harness.runtime.helpers.frame_source_name", return_value="still-png"), \
                patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.transport_mode", return_value="wifi"), \
                patch("phone_harness.windows.tunneld_status", return_value={"reachable": True, "device_count": 1}), \
                patch("phone_harness.windows.active_transport", return_value="wifi"):
            status = runtime.status()
        self.assertEqual(status["contract_version"], RUNTIME_CONTRACT_VERSION)
        self.assertEqual(status["frame_source"], "still-png")
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

    def test_tap_text_prefers_unique_actionable_role_over_child_static_text(self):
        runtime = PhoneRuntime()
        elements = [
            {"text": "Battery", "source": "accessibility", "role": "XCUIElementTypeButton", "name": "battery", "x": 10, "y": 20},
            {"text": "Battery", "source": "accessibility", "role": "XCUIElementTypeStaticText", "x": 10, "y": 20},
        ]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observation = runtime.observe()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.windows.tap_accessibility_batch") as tap:
            runtime.act([{"op": "tap_text", "text": "Battery", "exact": True}], observation_id=observation["observation_id"])
        self.assertEqual(tap.call_args.args[0][0]["role"], "XCUIElementTypeButton")

    def test_tap_element_uses_observation_ref(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.helpers.elements", return_value=[{"text": "Battery", "source": "accessibility", "role": "XCUIElementTypeButton", "name": "battery", "x": 10, "y": 20}]):
            observation = runtime.observe()
        ref = observation["elements"][0]["element_ref"]
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=False), \
                patch("phone_harness.windows.tap_accessibility_batch") as tap:
            runtime.act([{"op": "tap_element", "element_ref": ref}], observation_id=observation["observation_id"])
        tap.assert_called_once()

    def test_tap_element_result_does_not_reveal_hidden_document_text(self):
        runtime = PhoneRuntime()
        elements = [{"text": "private note", "value": "private note", "source": "accessibility", "role": "XCUIElementTypeTextView", "name": "Note", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observation = runtime.observe()
        ref = observation["elements"][0]["element_ref"]
        with patch("phone_harness.runtime.sys.platform", "darwin"), \
                patch("phone_harness.runtime.helpers.tap"):
            result = runtime.act([{"op": "tap_element", "element_ref": ref}], observation_id=observation["observation_id"])
        self.assertEqual(result["results"][0]["result"]["text"], "[text content hidden]")
        self.assertNotIn("private note", repr(result))

    def test_open_app_is_resolved_during_preflight_before_home_mutates(self):
        runtime = PhoneRuntime()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.preflight_open_app", side_effect=RuntimeError("missing")), \
                patch("phone_harness.runtime.helpers.home") as home:
            with self.assertRaises(PhoneRuntimeError) as raised:
                runtime.act([{"op": "home"}, {"op": "open_app", "name": "Missing"}])
        self.assertEqual(raised.exception.phase, "preflight")
        home.assert_not_called()

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

    def test_ios_26_keeps_wda_batch_when_wait_stable_follows_actions(self):
        runtime = PhoneRuntime(observation_ttl=10)
        elements = [{"text": "1", "source": "accessibility", "name": "one", "x": 10, "y": 20}]
        with patch("phone_harness.runtime.helpers.elements", return_value=elements):
            observation = runtime.observe()
        with patch("phone_harness.runtime.sys.platform", "win32"), \
                patch("phone_harness.windows.wda_runtime_batch_supported", return_value=True), \
                patch("phone_harness.windows._wda_action_for_accessibility", return_value={"op": "tap-coordinate", "x": 5, "y": 10}), \
                patch("phone_harness.windows.run_wda_runtime_batch") as run, \
                patch("phone_harness.runtime.helpers.wait_stable", return_value=True) as wait_stable:
            result = runtime.act([
                {"op": "tap_text", "text": "1", "exact": True},
                {"op": "wait_stable", "timeout": 2.0, "interval": 0.2, "settle": 2},
            ], observation_id=observation["observation_id"])
        run.assert_called_once_with([{"op": "tap-coordinate", "x": 5, "y": 10}])
        wait_stable.assert_called_once_with(timeout=2.0, interval=0.2, settle=2)
        self.assertEqual(result["backend"], "wda_batch")
        self.assertEqual([item["op"] for item in result["results"]], ["tap_text", "wait_stable"])

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
