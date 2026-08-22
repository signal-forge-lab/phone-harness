import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class McpPythonBridgeTests(unittest.TestCase):
    def test_unserializable_runtime_result_returns_protocol_error_without_killing_bridge(self):
        bridge = Path(__file__).resolve().parents[1] / "mcp-server" / "python_bridge.py"
        with TemporaryDirectory() as directory:
            package = Path(directory) / "phone_harness"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "runtime.py").write_text(
                textwrap.dedent(
                    """
                    class Trace:
                        def emit(self, *args, **kwargs):
                            pass

                    class PhoneRuntimeError(RuntimeError):
                        def to_dict(self):
                            return {"error": {"code": "TEST", "message": str(self)}}

                    class PhoneRuntime:
                        def __init__(self):
                            self.trace = Trace()

                        def trace_host_request(self, method, params):
                            pass

                        def run_workflow(self, name, options=None):
                            return {"bad": object()}

                        def status(self):
                            return {"connection_state": "ready"}

                        def close(self):
                            pass
                    """
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = directory
            requests = "\n".join([
                json.dumps({"id": 1, "method": "workflow", "params": {"name": "x"}}),
                json.dumps({"id": 2, "method": "status", "params": {}}),
                "",
            ])
            completed = subprocess.run(
                [sys.executable, str(bridge)],
                input=requests,
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=True,
            )

        lines = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(lines[0]["id"], 1)
        self.assertFalse(lines[0]["ok"])
        self.assertEqual(lines[0]["error"]["code"], "SERIALIZATION_ERROR")
        self.assertTrue(lines[1]["ok"])
        self.assertEqual(lines[1]["result"]["connection_state"], "ready")

    def test_native_stdout_noise_does_not_corrupt_jsonl_protocol(self):
        bridge = Path(__file__).resolve().parents[1] / "mcp-server" / "python_bridge.py"
        with TemporaryDirectory() as directory:
            package = Path(directory) / "phone_harness"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "helpers.py").write_text("def screenshot():\n    raise AssertionError('unused')\n", encoding="utf-8")
            (package / "runtime.py").write_text(
                textwrap.dedent(
                    """
                    import os

                    class PhoneRuntimeError(RuntimeError):
                        def to_dict(self):
                            return {"error": {"code": "TEST", "message": str(self)}}

                    class PhoneRuntime:
                        def trace_host_request(self, method, params):
                            pass

                        def status(self):
                            os.write(1, b"native stdout noise\\n")
                            return {"connection_state": "ready"}

                        def close(self):
                            pass
                    """
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = directory
            completed = subprocess.run(
                [sys.executable, str(bridge)],
                input=json.dumps({"id": 1, "method": "status", "params": {}}) + "\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=True,
            )

        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["result"]["connection_state"], "ready")
        self.assertIn("native stdout noise", completed.stderr)

if __name__ == "__main__":
    unittest.main()
