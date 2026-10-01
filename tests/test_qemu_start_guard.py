"""Exercise the real PowerShell start guard with fake processes, never a VM."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QemuStartGuardTests(unittest.TestCase):
    def guard_result(self, exited, log_exists=True):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            self.skipTest("PowerShell required")
        source = (ROOT / "test-swiftshader.ps1").read_text()
        function = re.search(r"^function Assert-QemuStartup \{.*?^\}", source,
                             re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(function)
        harness = function.group(0) + "\n" + r'''
$ErrorActionPreference = 'Stop'
$script:logRead = $false
function Test-Path { param($LiteralPath, $PathType) return LOG_EXISTS }
function Get-Content {
    param($LiteralPath, $Tail)
    $script:logRead = $true
    return 'SDL failed to initialize audio subsystem'
}
$process = [pscustomobject]@{ ExitCode = 1; Timeout = 0 }
$process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
    param($milliseconds)
    $this.Timeout = $milliseconds
    return EXITED
}
$failed = $false
$message = ''
try { Assert-QemuStartup -Process $process -StderrPath 'fake.log' }
catch { $failed = $true; $message = $_.Exception.Message }
[ordered]@{ failed=$failed; message=$message; timeout=$process.Timeout;
            log_read=$script:logRead } | ConvertTo-Json -Compress
'''
        harness = harness.replace("LOG_EXISTS", "$true" if log_exists else "$false")
        harness = harness.replace("EXITED", "$true" if exited else "$false")
        result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-Command", harness], capture_output=True, text=True,
                                check=True, timeout=30)
        return json.loads(result.stdout)

    def test_live_process_continues_without_reading_stderr(self):
        result = self.guard_result(False)
        self.assertFalse(result["failed"])
        self.assertFalse(result["log_read"])
        self.assertEqual(result["timeout"], 1500)

    def test_immediate_failure_reports_exit_code_and_backend_error(self):
        result = self.guard_result(True)
        self.assertTrue(result["failed"])
        self.assertIn("Code 1", result["message"])
        self.assertIn("SDL failed to initialize audio subsystem", result["message"])

    def test_missing_stderr_still_reports_failed_process(self):
        result = self.guard_result(True, log_exists=False)
        self.assertTrue(result["failed"])
        self.assertIn("Code 1", result["message"])
        self.assertFalse(result["log_read"])

    def test_guard_precedes_pid_publication_and_sidecar_start(self):
        source = (ROOT / "test-swiftshader.ps1").read_text()
        self.assertLess(source.index("Assert-QemuStartup -Process $vm"),
                        source.index('Write-Output "QEMU_PID='))
        integrated = (ROOT / "start-real-database.ps1").read_text()
        self.assertLess(integrated.index("$qemuLaunchOutput = @(& $visibleLauncher"),
                        integrated.index("$viewer = Start-Process"))


if __name__ == "__main__":
    unittest.main()
