"""Launcher update flow with mocked processes; no UI, mount or VM involved."""
from pathlib import Path
import queue
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import emulator_launcher as launcher
from portable_launcher_model import Selection


class LauncherGraphicsTests(unittest.TestCase):
    def test_only_start_updates_ready_working_copy(self):
        for start in (False, True):
            with self.subTest(start=start):
                target = SimpleNamespace(events=queue.Queue(), _update_graphics=mock.Mock())
                selection = Selection(image="working.img")
                with mock.patch.object(launcher, "check_runtime", return_value=[]), \
                     mock.patch.object(launcher, "stage_check_command", return_value=["read-only"]), \
                     mock.patch.object(launcher.subprocess, "run", return_value=
                                       subprocess.CompletedProcess([], 0, "ready\n", "")):
                    launcher.Launcher._check_worker(target, selection, start)
                self.assertEqual(target._update_graphics.call_count, int(start))
                self.assertEqual(target.events.get(), ("checked", (selection, start, [])))

    def test_bad_stage_never_updates(self):
        target = SimpleNamespace(events=queue.Queue(), _update_graphics=mock.Mock())
        selection = Selection(image="working.img")
        with mock.patch.object(launcher, "check_runtime", return_value=[]), \
             mock.patch.object(launcher, "stage_check_command", return_value=["read-only"]), \
             mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "unprepared\n", "")):
            launcher.Launcher._check_worker(target, selection, True)
        target._update_graphics.assert_not_called()
        self.assertTrue(target.events.get()[1][2])

    def test_failed_update_blocks_start(self):
        target = SimpleNamespace(events=queue.Queue(),
                                 _update_graphics=mock.Mock(side_effect=RuntimeError("update failed")))
        selection = Selection(image="working.img")
        with mock.patch.object(launcher, "check_runtime", return_value=[]), \
             mock.patch.object(launcher, "stage_check_command", return_value=["read-only"]), \
             mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "legacy-ready\n", "")):
            launcher.Launcher._check_worker(target, selection, True)
        self.assertIn("update failed", target.events.get()[1][2][0])

    def test_running_qemu_or_failed_probe_prevents_mount(self):
        for code, output in ((0, "1\n"), (1, "0\n"), (0, "")):
            with self.subTest(code=code, output=output):
                target = SimpleNamespace(_run_step=mock.Mock())
                with mock.patch.object(launcher.subprocess, "run", return_value=
                                       subprocess.CompletedProcess([], code, output, "")), \
                     mock.patch.object(launcher, "graphics_update_command") as command:
                    with self.assertRaisesRegex(RuntimeError, "QEMU"):
                        launcher.Launcher._update_graphics(target, Selection())
                command.assert_not_called()
                target._run_step.assert_not_called()

    def test_clear_probe_installs_audio_before_graphics_update(self):
        target = SimpleNamespace(_run_step=mock.Mock())
        selection = Selection(image="working.img")
        with mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "0\n", "")), \
             mock.patch.object(launcher, "audio_setup_command", return_value=["guarded-audio"]), \
             mock.patch.object(launcher, "graphics_update_command", return_value=["guarded-update"]):
            launcher.Launcher._update_graphics(target, selection)
        self.assertEqual(target._run_step.call_count, 2)
        self.assertEqual(target._run_step.call_args_list[0].args[0], ["guarded-audio"])
        self.assertEqual(target._run_step.call_args.args[0], ["guarded-update"])

    def test_failed_audio_setup_prevents_graphics_mount(self):
        target = SimpleNamespace(_run_step=mock.Mock(side_effect=RuntimeError("Audio failed")))
        with mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "0\n", "")), \
             mock.patch.object(launcher, "audio_setup_command", return_value=["guarded-audio"]), \
             mock.patch.object(launcher, "graphics_update_command") as graphics:
            with self.assertRaisesRegex(RuntimeError, "Audio failed"):
                launcher.Launcher._update_graphics(target, Selection())
        graphics.assert_not_called()


if __name__ == "__main__":
    unittest.main()
