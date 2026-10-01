"""Audio failure evidence with fake QMP/VMs and ordinary directory fixtures."""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_diagnostics as diagnostics
import audio_setup_runner as runner


class ExportTests(unittest.TestCase):
    def test_only_allowlisted_logs_and_bounded_tails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "guest"
            dest = Path(directory) / "report"
            (root / "NVRAM").mkdir(parents=True)
            (root / "NVRAM/m90_audio_install.log").write_bytes(b"old-start-NEW-END")
            (root / "NVRAM/private.bin").write_bytes(b"secret")
            with mock.patch.object(diagnostics, "MAX_LOG_BYTES", 7):
                report = diagnostics.export_guest_logs(root, dest)
            self.assertEqual((dest / "m90_audio_install.log").read_bytes(), b"NEW-END")
            self.assertFalse((dest / "private.bin").exists())
            self.assertEqual(report["truncated"], ["NVRAM/m90_audio_install.log"])
            self.assertIn("WINDOWS/setupapi.log", report["missing"])
            self.assertEqual((root / "NVRAM/m90_audio_install.log").read_bytes(), b"old-start-NEW-END")

    def test_refuses_guest_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Gast"):
                diagnostics.export_guest_logs(Path(directory), Path(directory) / "out")

    def test_refused_guest_path_does_not_hide_other_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "guest"
            root.mkdir()
            with mock.patch.object(diagnostics, "inside", side_effect=ValueError("Symlink refused")):
                report = diagnostics.export_guest_logs(root, Path(directory) / "out")
            self.assertEqual(len(report["errors"]), len(diagnostics.GUEST_FILES))
            self.assertEqual(report["copied"], [])

    def test_screenshots_both_devices_with_ppm_fallback(self):
        qmp = mock.Mock(events=[{"event": "STOP"}])
        def execute(command, arguments=None):
            if command == "query-status":
                return {"status": "running"}
            if arguments.get("format") == "png":
                raise RuntimeError("PNG unavailable")
            Path(arguments["filename"]).write_bytes(b"P6")
            return {}
        qmp.execute.side_effect = execute
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            diagnostics.capture_guest(qmp, dest, "install", TimeoutError("no signal"), b"partial")
            report = json.loads((dest / "failure.json").read_text())
            self.assertEqual(set(report["screenshots"]), {"upper", "lower"})
            self.assertTrue((dest / "xp-lower.ppm").is_file())
            self.assertEqual((dest / "serial-result.log").read_bytes(), b"partial")


class BootTests(unittest.TestCase):
    def test_timeout_captures_before_shutdown_and_keeps_display_visible(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            events = []
            process = mock.Mock()
            process.poll.return_value = None
            process.wait.return_value = 0
            qmp = mock.Mock(events=[])
            qmp.execute.side_effect = lambda *args: events.append(args[0]) or {}
            connection = mock.Mock()
            connection.recv.side_effect = socket.timeout
            stack.enter_context(mock.patch.object(runner, "require_stopped"))
            stack.enter_context(mock.patch.object(runner, "require_free_port"))
            popen = stack.enter_context(mock.patch.object(runner.subprocess, "Popen", return_value=process))
            stack.enter_context(mock.patch.object(runner, "connect_with_retry", return_value=qmp))
            stack.enter_context(mock.patch.object(runner.socket, "create_connection", return_value=connection))
            stack.enter_context(mock.patch.object(runner.time, "monotonic", side_effect=[0, 301]))
            with self.assertRaises(TimeoutError):
                runner.boot(Path("qemu.exe"), Path("work.img"), "install",
                            Path(directory) / "stderr.log", diagnostics=Path(directory) / "report")
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("-display") + 1], "gtk,show-tabs=on")
            self.assertLess(events.index("screendump"), events.index("system_powerdown"))
            connection.close.assert_called_once()
            qmp.close.assert_called_once()
            process.wait.assert_called_once()

    def test_success_stops_vm_without_failure_capture(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            process = mock.Mock()
            process.poll.return_value = None
            process.wait.return_value = 0
            qmp = mock.Mock()
            connection = mock.Mock()
            connection.recv.return_value = b"M90-AUDIO-SETUP-OK\n"
            stack.enter_context(mock.patch.object(runner, "require_stopped"))
            stack.enter_context(mock.patch.object(runner, "require_free_port"))
            stack.enter_context(mock.patch.object(runner.subprocess, "Popen", return_value=process))
            stack.enter_context(mock.patch.object(runner, "connect_with_retry", return_value=qmp))
            stack.enter_context(mock.patch.object(runner.socket, "create_connection", return_value=connection))
            capture = stack.enter_context(mock.patch.object(runner, "capture_guest"))
            runner.boot(Path("qemu.exe"), Path("work.img"), "install",
                        Path(directory) / "stderr.log", diagnostics=Path(directory) / "report")
            capture.assert_not_called()
            qmp.execute.assert_called_once_with("system_powerdown")

    def test_export_requires_stopped_vm_and_keeps_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with mock.patch.object(runner, "boot", side_effect=TimeoutError("no guest signal")), \
                 mock.patch.object(runner, "require_stopped", side_effect=RuntimeError("VM still running")), \
                 mock.patch.object(runner.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "no guest signal"):
                    runner.diagnostic_boot(Path("original.img"), Path("work.img"), Path("qemu"), project, "install")
            run.assert_not_called()
            reports = list((project / "logs/audio-diagnostics").glob("*/export-error.json"))
            self.assertEqual(len(reports), 1)
            self.assertIn("VM still running", reports[0].read_text())

    def test_export_runs_after_boot_cleanup_and_quotes_paths_as_arguments(self):
        with tempfile.TemporaryDirectory(prefix="audio logs ") as directory:
            project = Path(directory)
            events = []
            def boot(*args, **kwargs):
                events.append("vm-stopped")
                raise TimeoutError("no signal")
            def guard():
                events.append("guard")
            def run(command, **kwargs):
                events.append("export")
                self.assertIn("original image.img", command)
                return subprocess.CompletedProcess(command, 0, "{}", "")
            with mock.patch.object(runner, "boot", side_effect=boot), \
                 mock.patch.object(runner, "require_stopped", side_effect=guard), \
                 mock.patch.object(runner, "wsl_path", side_effect=lambda p: str(p)), \
                 mock.patch.object(runner.subprocess, "run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "Audio-Diagnose:"):
                    runner.diagnostic_boot(Path("original image.img"), Path("work.img"), Path("qemu"), project, "install")
            self.assertEqual(events, ["vm-stopped", "guard", "export"])

    def test_early_helper_is_enabled_only_for_audio(self):
        project = Path(__file__).resolve().parents[1]
        audio = (project / "src/audio_installer.c").read_text()
        common = (project / "src/qxl_installer.c").read_text()
        self.assertIn("#define INSTALLER_EARLY_DIALOG_HELPER 1", audio)
        early = common.index("signing_thread = start_signing_helper();", common.index("write_text(INSTALLER_TITLE)"))
        self.assertLess(early, common.index("if (!INSTALLER_PREPARE())"))


if __name__ == "__main__":
    unittest.main()
