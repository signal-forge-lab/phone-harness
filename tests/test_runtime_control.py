import tempfile
import unittest
from pathlib import Path

from phone_harness.runtime_control import load_runtime_control, set_runtime_paused


class RuntimeControlTests(unittest.TestCase):
    def test_missing_control_file_defaults_to_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            self.assertFalse(load_runtime_control(path)["paused"])

    def test_pause_and_resume_are_persisted_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            paused = set_runtime_paused(True, path=path, source="test")
            self.assertTrue(paused["paused"])
            self.assertTrue(load_runtime_control(path)["paused"])
            resumed = set_runtime_paused(False, path=path, source="test")
            self.assertFalse(resumed["paused"])
            self.assertFalse(load_runtime_control(path)["paused"])

    def test_pause_generation_records_a_pause_even_after_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"

            first_pause = set_runtime_paused(True, path=path, source="test")
            first_generation = first_pause["pause_generation"]
            first_pause_unix = first_pause["last_pause_unix"]
            resumed = set_runtime_paused(False, path=path, source="test")

            self.assertEqual(first_generation, 1)
            self.assertEqual(resumed["pause_generation"], first_generation)
            self.assertEqual(resumed["last_pause_unix"], first_pause_unix)

            second_pause = set_runtime_paused(True, path=path, source="test")
            self.assertEqual(second_pause["pause_generation"], first_generation + 1)
            self.assertGreaterEqual(second_pause["last_pause_unix"], first_pause_unix)

    def test_legacy_paused_state_is_migrated_without_temporarily_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(
                '{"schema_version":1,"paused":true,"changed_unix":5.0,"source":"legacy"}\n',
                encoding="utf-8",
            )

            migrated = set_runtime_paused(True, path=path, source="monitor-web")

            self.assertTrue(migrated["paused"])
            self.assertEqual(migrated["pause_generation"], 1)
            self.assertIsNotNone(migrated["last_pause_unix"])


if __name__ == "__main__":
    unittest.main()
