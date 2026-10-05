"""Exercise the production hash guard without mounting or booting any image."""
import io
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import emulator_launcher as launcher
from image_setup import COMPONENTS


class QxlRetryGuardTests(unittest.TestCase):
    def test_production_guard_accepts_bundled_and_previous_verifier_only(self):
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        executable = str(bash) if bash.is_file() else shutil.which("bash")
        if not executable:
            self.skipTest("Bash is needed to exercise the production shell function")
        script = (PROJECT / "scripts/retry_qxl_install.sh").read_text()
        function = re.search(r"^is_known_display_verifier\(\) \{.*?^\}", script,
                             re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(function)
        for digest, accepted in (
            (COMPONENTS["display_verify"][1], True),
            ("aacd9215399d0122b46cb3b428dde15fad421de248e74e35c88b7de3645cc789", True),
            ("0" * 64, False),
            ("", False),
        ):
            with self.subTest(digest=digest):
                result = subprocess.run(
                    [executable, "--noprofile", "--norc", "-c",
                     function.group(0) + '\nis_known_display_verifier "$1"', "guard", digest],
                    # Git Bash startup on Windows can exceed ten seconds;
                    # this checks the hash guard, not shell startup speed.
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
        self.assertIn('is_known_display_verifier "$verifier_hash" ||', script)
        self.assertLess(script.index('is_known_display_verifier "$verifier_hash" ||'),
                        script.index('ntfs-3g -o big_writes'))

    def test_step_failure_keeps_concrete_cause_in_error_message(self):
        target = SimpleNamespace(events=queue.Queue())
        process = SimpleNamespace(stdout=io.StringIO("QXL retry: unrecognized active display verifier\n"),
                                  wait=lambda: 3)
        with mock.patch.object(launcher.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "unrecognized active display verifier"):
                launcher.Launcher._run_step(target, ["mock-step"], "QXL retry")

    def test_expected_retry_result_remains_allowed(self):
        target = SimpleNamespace(events=queue.Queue())
        process = SimpleNamespace(stdout=io.StringIO("retry required\n"), wait=lambda: 17)
        with mock.patch.object(launcher.subprocess, "Popen", return_value=process):
            self.assertEqual(launcher.Launcher._run_step(
                target, ["mock-step"], "verify", allowed_exit_codes=(17,)), 17)


if __name__ == "__main__":
    unittest.main()
