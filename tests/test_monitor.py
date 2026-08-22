import json
import tempfile
import unittest
from pathlib import Path

from phone_harness.monitor import TraceTail, _event_duration, _event_time


class MonitorModelTests(unittest.TestCase):
    def test_trace_tail_reads_only_new_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(json.dumps({"event_id": "a", "type": "one"}) + "\n", encoding="utf-8")
            tail = TraceTail(path)
            self.assertEqual([item["event_id"] for item in tail.poll()], ["a"])
            self.assertEqual(tail.poll(), [])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event_id": "b", "type": "two"}) + "\n")
            self.assertEqual([item["event_id"] for item in tail.poll()], ["b"])

    def test_monitor_formatters_are_tolerant(self):
        self.assertEqual(_event_duration({"data": {"duration_ms": 123.4}}), "123 ms")
        self.assertTrue(_event_time("2026-08-17T05:00:00+09:00").startswith("05:00:00"))


if __name__ == "__main__":
    unittest.main()
