"""Exercise the real PowerShell start guard and stderr relay with fake processes, never a VM."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "scripts" / "qemu_log_relay.py"


class QemuStartGuardTests(unittest.TestCase):
    def guard_result(self, status, log_text=None, relay_exited=False):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            self.skipTest("PowerShell required")
        source = (ROOT / "test-swiftshader.ps1").read_text()
        function = re.search(r"^function Wait-QemuStartup \{.*?^\}", source,
                             re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(function)
        with tempfile.TemporaryDirectory() as temp:
            status_path, log_path = Path(temp) / "status.json", Path(temp) / "stderr.log"
            if status is not None:
                status_path.write_text(json.dumps(status))
            if log_text is not None:
                log_path.write_text(log_text)
            harness = function.group(0) + "\n" + r'''
$ErrorActionPreference = 'Stop'
$relay = [pscustomobject]@{ HasExited = RELAY_EXITED; ExitCode = 2 }
$failed = $false; $message = ''; $pid_ = $null
try { $pid_ = Wait-QemuStartup -Relay $relay -StatusPath 'STATUS' -StderrPath 'LOG' -TimeoutSeconds 2 }
catch { $failed = $true; $message = $_.Exception.Message }
[ordered]@{ failed=$failed; message=$message; pid=$pid_ } | ConvertTo-Json -Compress
'''
            harness = (harness.replace("RELAY_EXITED", "$true" if relay_exited else "$false")
                       .replace("STATUS", str(status_path)).replace("LOG", str(log_path)))
            result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive",
                                     "-Command", harness], capture_output=True, text=True,
                                    check=True, timeout=30)
        return json.loads(result.stdout)

    def test_live_process_publishes_pid(self):
        result = self.guard_result({"pid": 4321})
        self.assertFalse(result["failed"])
        self.assertEqual(result["pid"], 4321)

    def test_immediate_failure_reports_exit_code_and_backend_error(self):
        result = self.guard_result({"exit": 1}, "SDL failed to initialize audio subsystem\n")
        self.assertTrue(result["failed"])
        self.assertIn("Code 1", result["message"])
        self.assertIn("SDL failed to initialize audio subsystem", result["message"])

    def test_missing_stderr_still_reports_failed_process(self):
        result = self.guard_result({"exit": 1})
        self.assertTrue(result["failed"])
        self.assertIn("Code 1", result["message"])

    def test_dead_relay_without_status_fails(self):
        result = self.guard_result(None, relay_exited=True)
        self.assertTrue(result["failed"])
        self.assertIn("Protokollrelais beendet (Code 2)", result["message"])

    def test_guard_precedes_pid_publication_and_sidecar_start(self):
        source = (ROOT / "test-swiftshader.ps1").read_text()
        self.assertLess(source.index("$qemuPid = Wait-QemuStartup -Relay $relay"),
                        source.index('Write-Output "QEMU_PID='))
        integrated = (ROOT / "start-real-database.ps1").read_text()
        self.assertLess(integrated.index("$qemuLaunchOutput = @(& $visibleLauncher"),
                        integrated.index("$viewer = Start-Process"))


class QemuLogRelayTests(unittest.TestCase):
    def run_relay(self, program: str):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        command = directory / "command.txt"
        command.write_text(f'"{sys.executable}" -c "{program}"', encoding="utf-8")
        log, status = directory / "qemu.log", directory / "status.json"
        log.write_text("previous run\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(RELAY), "--log", str(log), "--status", str(status),
             "--command-file", str(command)], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(status.read_text()), log, directory

    def test_running_process_reports_pid_and_logs_stderr(self):
        status, log, directory = self.run_relay(
            "import sys,time; sys.stderr.write('backend ready\\n'); sys.stderr.flush(); time.sleep(2.5)")
        self.assertIn("pid", status)
        self.assertIn("backend ready", log.read_text(encoding="utf-8"))
        self.assertEqual((directory / "qemu.old.log").read_text(encoding="utf-8"), "previous run\n")

    def test_early_exit_reports_code_with_complete_stderr(self):
        status, log, _ = self.run_relay(
            "import sys; sys.stderr.write('option rejected\\n'); sys.exit(3)")
        self.assertEqual(status, {"exit": 3})
        self.assertIn("option rejected", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
