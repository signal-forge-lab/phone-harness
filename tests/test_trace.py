import json
import tempfile
import unittest
from pathlib import Path

from phone_harness.trace import TraceSink, read_trace_events


class TraceTests(unittest.TestCase):
    def test_trace_sink_appends_compact_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            preview = Path(directory) / "screen.jpg"
            preview.write_bytes(b"preview")
            sink = TraceSink("session-1", path=path)
            event = sink.emit(
                "observation",
                summary="Visual frame",
                data={"capture_ms": 401.2, "region": (1, 2, 3, 4)},
                preview_path=preview,
            )
            self.assertEqual(event["session_id"], "session-1")
            events = read_trace_events(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "observation")
            self.assertEqual(events[0]["data"]["region"], [1, 2, 3, 4])
            self.assertTrue(Path(events[0]["preview_path"]).is_file())

    def test_trace_rotation_keeps_new_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x" * 256, encoding="utf-8")
            sink = TraceSink("s", path=path, max_bytes=64)
            sink.emit("runtime.start", summary="start")
            self.assertTrue(path.with_suffix(".jsonl.1").exists())
            self.assertEqual(read_trace_events(path)[0]["type"], "runtime.start")


if __name__ == "__main__":
    unittest.main()

