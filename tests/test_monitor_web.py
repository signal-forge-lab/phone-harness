import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phone_harness import monitor_web


class MonitorWebTests(unittest.TestCase):
    def test_client_disconnect_while_writing_response_is_not_an_error(self):
        class ClosedWriter:
            def write(self, _data):
                raise ConnectionAbortedError("client left")

        handler = object.__new__(monitor_web.MonitorRequestHandler)
        handler.wfile = ClosedWriter()
        self.assertFalse(handler._write_payload(b"payload"))

    def test_client_is_lan_allows_only_requested_local_ranges(self):
        allowed = (
            "127.0.0.1",
            "10.0.0.3",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.50",
            "169.254.1.2",
            "::1",
            "fe80::1",
        )
        rejected = (
            "8.8.8.8",
            "172.15.255.255",
            "172.32.0.1",
            "192.0.2.1",
            "198.51.100.1",
            "203.0.113.1",
            "240.0.0.1",
            "0.0.0.0",
            "::",
            "fd00::1",
        )
        for address in allowed:
            with self.subTest(address=address):
                self.assertTrue(monitor_web.client_is_lan(address))
        for address in rejected:
            with self.subTest(address=address):
                self.assertFalse(monitor_web.client_is_lan(address))

    def test_html_is_responsive_and_has_teaching_controls(self):
        html = monitor_web.HTML_PATH.read_text(encoding="utf-8")
        self.assertIn('name="viewport"', html)
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', html)
        self.assertIn('href="/assets/dockview.css"', html)
        self.assertIn('href="/assets/monitor-ui.css"', html)
        self.assertIn('src="/assets/dockview.js"', html)
        self.assertIn('src="/assets/monitor-ui.js"', html)
        self.assertIn("grid-template-areas", html)
        self.assertIn("@media (max-width:820px)", html)
        self.assertIn("prefers-reduced-motion", html)
        self.assertIn('role="listbox"', html)
        self.assertIn('role="option"', html)
        self.assertIn("eventTone", html)
        self.assertIn("alternatives", html)
        self.assertIn('id="teaching"', html)
        self.assertIn("/api/respond", html)
        self.assertIn('id="failure"', html)
        self.assertIn("Failure Judgment", html)
        self.assertIn('id="teachMessage"', html)
        self.assertIn('id="teachImage"', html)
        self.assertIn('id="qaHistory"', html)
        self.assertIn("質問時の画像を見る", html)
        self.assertIn("/api/teach", html)
        self.assertIn('id="humanPresence"', html)
        self.assertIn("/api/presence", html)
        self.assertIn('id="previewFit"', html)
        self.assertIn('id="previewOriginal"', html)
        self.assertIn('id="splitLeft"', html)
        self.assertIn('id="splitCenter"', html)
        self.assertIn('id="splitRow1"', html)
        self.assertIn('id="splitRow4"', html)
        self.assertIn('id="splitRight"', html)
        self.assertIn('id="splitTimeline"', html)
        self.assertIn("setPreviewMode", html)
        self.assertIn("splitterPointerDown", html)
        self.assertIn('id="hostActivity"', html)
        self.assertIn('id="automationState"', html)
        self.assertIn('id="pauseAutomation"', html)
        self.assertIn("/api/pause", html)
        self.assertIn("pause-requested", html)
        self.assertIn('id="performance"', html)
        self.assertIn("Pipeline Timing", html)
        self.assertIn('id="ocrSummary"', html)
        self.assertIn('id="humanPresence"', html)
        self.assertIn("/api/presence", html)

    def test_monitor_ui_assets_cover_requested_desktop_and_keyboard_controls(self):
        script = monitor_web.MONITOR_UI_JS_PATH.read_text(encoding="utf-8")
        style = monitor_web.MONITOR_UI_CSS_PATH.read_text(encoding="utf-8")
        self.assertIn("createDockview", script)
        self.assertIn("Ctrl+Enter", script)
        self.assertIn("preview_events", script)
        self.assertIn("phoneHarnessDesktop", script)
        self.assertIn("STALE_OBSERVATION", script)
        self.assertIn("orientation: landscape", style)
        self.assertIn("desktop-dock", style)
        self.assertTrue(monitor_web.DOCKVIEW_JS_PATH.is_file())
        self.assertTrue(monitor_web.DOCKVIEW_CSS_PATH.is_file())

    def test_web_manifest_requests_standalone_display(self):
        manifest = json.loads(
            monitor_web.HTML_PATH.with_name("manifest.webmanifest").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "Phone Harness Monitor")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")

    def test_incremental_state_returns_only_events_after_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "events.jsonl"
            trace.write_text(
                "\n".join([
                    json.dumps({"event_id": "a", "monotonic_ns": 10, "type": "one"}),
                    json.dumps({"event_id": "b", "monotonic_ns": 20, "type": "two"}),
                ]) + "\n",
                encoding="utf-8",
            )
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace):
                state = monitor_web.build_state(after_monotonic_ns=10)
            self.assertEqual([event["event_id"] for event in state["events"]], ["b"])

    def test_state_exposes_preview_log_metadata_without_filesystem_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "frame.jpg"
            image.write_bytes(b"image")
            trace = root / "events.jsonl"
            trace.write_text(json.dumps({
                "event_id": "preview-1",
                "monotonic_ns": 10,
                "type": "observation.result",
                "summary": "Captured frame",
                "preview_path": str(image),
                "data": {"image_profile": "glance", "image_bytes": 5},
            }) + "\n", encoding="utf-8")
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace):
                item = monitor_web.build_state()["preview_events"][0]
            self.assertEqual(item["event_id"], "preview-1")
            self.assertEqual(item["image_profile"], "glance")
            self.assertNotIn("preview_path", item)
            self.assertNotIn(str(root), json.dumps(item))

    def test_state_exposes_latest_failure_judgment(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "events.jsonl"
            trace.write_text(
                json.dumps({
                    "event_id": "failure",
                    "monotonic_ns": 11,
                    "type": "failure.judgment",
                    "summary": "Expected merge did not occur",
                    "data": {"expected": "one free cell", "actual": "unchanged board"},
                }) + "\n",
                encoding="utf-8",
            )
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace):
                state = monitor_web.build_state()
            self.assertEqual(state["latest_failure"]["event_id"], "failure")

    def test_state_exposes_operator_pause_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "control.json"
            control.write_text(
                json.dumps({"schema_version": 1, "paused": True, "changed_unix": 1, "source": "test"}),
                encoding="utf-8",
            )
            with patch.object(monitor_web, "DEFAULT_CONTROL_FILE", control):
                state = monitor_web.build_state()
            self.assertTrue(state["automation_control"]["paused"])

    def test_pause_state_is_requested_while_phone_action_is_still_in_flight(self):
        control = {
            "schema_version": 1,
            "paused": True,
            "changed_unix": 100.0,
            "source": "monitor-web",
        }
        runtime_status = {
            "current_operation": {"kind": "act", "phase": "execute"},
        }

        view = monitor_web._automation_control_view(control, runtime_status, [])

        self.assertEqual(view["state"], "pause-requested")
        self.assertFalse(view["accepted"])
        self.assertEqual(view["label"], "PAUSE REQUESTED")

    def test_pause_state_is_accepted_after_runtime_reaches_safe_boundary(self):
        control = {
            "schema_version": 1,
            "paused": True,
            "changed_unix": 100.0,
            "source": "monitor-web",
        }
        runtime_status = {
            "runtime_id": "current-runtime",
            "current_operation": {"kind": "act", "phase": "preflight"},
        }
        events = [{
            "session_id": "current-runtime",
            "type": "runtime.pause.wait",
            "monotonic_ns": 20,
            "data": {"boundary": "merge_boss_turn.cycle.3"},
        }]

        view = monitor_web._automation_control_view(control, runtime_status, events)

        self.assertEqual(view["state"], "paused")
        self.assertTrue(view["accepted"])
        self.assertEqual(view["label"], "PAUSED")
        self.assertEqual(view["boundary"], "merge_boss_turn.cycle.3")

    def test_pause_state_ignores_wait_ack_from_a_different_runtime_session(self):
        control = {
            "schema_version": 1,
            "paused": True,
            "changed_unix": 100.0,
            "source": "monitor-web",
        }
        runtime_status = {
            "runtime_id": "current-runtime",
            "current_operation": None,
        }
        events = [{
            "session_id": "old-runtime",
            "type": "runtime.pause.wait",
            "monotonic_ns": 20,
            "data": {"boundary": "action"},
        }]

        view = monitor_web._automation_control_view(control, runtime_status, events)

        self.assertEqual(view["state"], "paused")
        self.assertTrue(view["accepted"])
        self.assertEqual(view["basis"], "safe_gate")
        self.assertIsNone(view["boundary"])

    def test_pause_state_is_accepted_immediately_when_no_phone_action_is_running(self):
        control = {
            "schema_version": 1,
            "paused": True,
            "changed_unix": 100.0,
            "source": "monitor-web",
        }

        view = monitor_web._automation_control_view(control, {"current_operation": None}, [])

        self.assertEqual(view["state"], "paused")
        self.assertTrue(view["accepted"])
        self.assertEqual(view["basis"], "safe_gate")

    def test_resume_state_ignores_stale_pause_wait_when_runtime_is_gone(self):
        control = {
            "schema_version": 1,
            "paused": False,
            "changed_unix": 101.0,
            "source": "monitor-web",
        }
        events = [{
            "type": "runtime.pause.wait",
            "monotonic_ns": 20,
            "data": {"boundary": "action"},
        }]

        view = monitor_web._automation_control_view(control, {}, events)

        self.assertEqual(view["state"], "running")

    def test_resume_state_waits_for_current_runtime_to_leave_pause_wait(self):
        control = {
            "schema_version": 1,
            "paused": False,
            "changed_unix": 101.0,
            "source": "monitor-web",
        }
        runtime_status = {
            "runtime_id": "current-runtime",
            "current_operation": {"kind": "act", "phase": "preflight"},
        }
        events = [{
            "session_id": "current-runtime",
            "type": "runtime.pause.wait",
            "monotonic_ns": 20,
            "data": {"boundary": "action"},
        }]

        view = monitor_web._automation_control_view(control, runtime_status, events)

        self.assertEqual(view["state"], "resuming")
        self.assertFalse(view["accepted"])
        self.assertEqual(view["label"], "RESUMING")

    def test_state_uses_latest_status_event_when_runtime_status_file_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "events.jsonl"
            status = root / "status.json"
            trace.write_text(json.dumps({
                "event_id": "health",
                "monotonic_ns": 12,
                "type": "status.result",
                "data": {"connection_state": "ready", "active_transport": "wifi"},
            }) + "\n", encoding="utf-8")
            status.write_text("{}", encoding="utf-8")
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace), patch.object(monitor_web, "STATUS_FILE", status):
                state = monitor_web.build_state()
            self.assertEqual(state["status"]["connection_state"], "ready")
            self.assertEqual(state["status"]["last_health"]["active_transport"], "wifi")

    def test_operator_conversation_pairs_question_and_answer(self):
        events = [
            {
                "type": "operator.question",
                "at": "2026-08-20T00:00:00+00:00",
                "summary": "Which item?",
                "data": {"question_id": "q1", "context": "left item"},
            },
            {
                "type": "operator.answer",
                "at": "2026-08-20T00:01:00+00:00",
                "data": {"question_id": "q1", "answer": "Tennis Lv10", "choice": None},
            },
        ]
        history = monitor_web._operator_conversation(events)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "Which item?")
        self.assertEqual(history[0]["answer"], "Tennis Lv10")

    def test_timing_summary_groups_pipeline_stages(self):
        summary = monitor_web._timing_summary([
            {"type": "observation.result", "data": {"capture_ms": 100.0}},
            {"type": "observation.result", "data": {"capture_ms": 200.0}},
            {"type": "merge_boss.board", "data": {"duration_ms": 40.0}},
            {"type": "workflow.decision", "data": {"duration_ms": 5.0}},
            {"type": "action.batch", "data": {"duration_ms": 300.0}},
            {"type": "knowledge.write", "data": {"duration_ms": 8.0}},
        ])
        self.assertEqual(summary["capture"]["count"], 2)
        self.assertEqual(summary["capture"]["latest_ms"], 200.0)
        self.assertEqual(summary["capture"]["average_ms"], 150.0)
        self.assertEqual(summary["recognition"]["latest_ms"], 40.0)
        self.assertEqual(summary["decision"]["latest_ms"], 5.0)
        self.assertEqual(summary["action"]["latest_ms"], 300.0)
        self.assertEqual(summary["learning"]["latest_ms"], 8.0)

    def test_host_activity_prefers_explicit_completed_marker(self):
        now = time.monotonic_ns()
        activity = monitor_web._host_activity([
            {
                "type": "host.activity.marker",
                "status": "completed",
                "monotonic_ns": now,
                "data": {"state": "completed", "note": "done"},
            }
        ], None, None)
        self.assertEqual(activity["state"], "completed")
        self.assertEqual(activity["basis"], "explicit_marker")

    def test_host_activity_keeps_explicit_working_across_completed_tool_calls(self):
        now = time.monotonic_ns()
        activity = monitor_web._host_activity([
            {
                "type": "host.activity.marker",
                "status": "working",
                "monotonic_ns": now - 1_000_000,
                "data": {"state": "working"},
            },
            {
                "type": "host.request.complete",
                "status": "completed",
                "monotonic_ns": now,
                "data": {"method": "observe"},
            },
        ], None, None)
        self.assertEqual(activity["state"], "working")
        self.assertEqual(activity["basis"], "explicit_marker")

    def test_host_activity_flags_stale_working_marker_as_possible_interruption(self):
        activity = monitor_web._host_activity([
            {
                "type": "host.activity.marker",
                "status": "working",
                "monotonic_ns": time.monotonic_ns() - 181_000_000_000,
                "data": {"state": "working"},
            }
        ], None, None)
        self.assertEqual(activity["state"], "stale-or-interrupted")

    def test_preview_path_only_resolves_an_event_owned_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "frame.jpg"
            image.write_bytes(b"image")
            trace = root / "events.jsonl"
            trace.write_text(json.dumps({"event_id": "known", "preview_path": str(image)}) + "\n", encoding="utf-8")
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace):
                self.assertEqual(monitor_web.preview_path_for_event("known"), image)
                self.assertIsNone(monitor_web.preview_path_for_event("../frame.jpg"))

    def test_preview_rejects_non_image_even_when_trace_references_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "secret.txt"
            secret.write_text("not an image", encoding="utf-8")
            trace = root / "events.jsonl"
            trace.write_text(json.dumps({"event_id": "bad", "preview_path": str(secret)}) + "\n", encoding="utf-8")
            with patch.object(monitor_web, "DEFAULT_TRACE_FILE", trace):
                self.assertIsNone(monitor_web.preview_path_for_event("bad"))


if __name__ == "__main__":
    unittest.main()
