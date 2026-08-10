import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from phone_harness import run


class CommandExecutionTests(unittest.TestCase):
    def test_c_executes_code_with_helpers_scope(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["phone-harness", "-c", "print(callable(screen_info))"]), \
                redirect_stdout(output):
            run.main()
        self.assertEqual(output.getvalue().strip(), "True")

    def test_c_requires_exactly_one_code_argument(self):
        with patch.object(sys, "argv", ["phone-harness", "-c"]):
            with self.assertRaises(SystemExit):
                run.main()

    @unittest.skipUnless(sys.platform == "win32", "Windows stdio hardening")
    def test_windows_cli_emits_unicode_without_cp932_failure(self):
        result = subprocess.run(
            [sys.executable, "-m", "phone_harness.run", "-c", "print('📱—設定')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "📱—設定")


if __name__ == "__main__":
    unittest.main()
