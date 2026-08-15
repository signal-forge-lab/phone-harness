import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class McpPythonBridgeTests(unittest.TestCase):
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

    def test_observe_can_attach_visual_grid_analysis(self):
        bridge = Path(__file__).resolve().parents[1] / "mcp-server" / "python_bridge.py"
        with TemporaryDirectory() as directory:
            package = Path(directory) / "phone_harness"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            image = Path(directory) / "screen.png"
            image.write_bytes(b"png")
            (package / "helpers.py").write_text(
                f"def screenshot():\n    return {str(image)!r}\n",
                encoding="utf-8",
            )
            (package / "visual_grid.py").write_text(
                "def analyze_grid(path, **config):\n"
                "    return {'rows': config['rows'], 'columns': config['columns'], 'exact_groups': [['r0c0', 'r0c1']]}\n",
                encoding="utf-8",
            )
            (package / "runtime.py").write_text(
                textwrap.dedent(
                    """
                    class PhoneRuntimeError(RuntimeError):
                        def to_dict(self):
                            return {"error": {"code": "TEST", "message": str(self)}}

                    class PhoneRuntime:
                        def observe(self, force=False, include_text_content=False):
                            return {"observation_id": 1, "elements": []}

                        def close(self):
                            pass
                    """
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = directory
            request = {
                "id": 1,
                "method": "observe",
                "params": {
                    "include_image": True,
                    "visual_grid": {
                        "rows": 9,
                        "columns": 7,
                        "bounds": {"x": 0, "y": 0, "w": 70, "h": 90},
                    },
                },
            }
            completed = subprocess.run(
                [sys.executable, str(bridge)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=True,
            )

        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["visual_grid"]["rows"], 9)
        self.assertEqual(result["visual_grid"]["exact_groups"], [["r0c0", "r0c1"]])
        self.assertEqual(result["_image"]["data"], "cG5n")


if __name__ == "__main__":
    unittest.main()
