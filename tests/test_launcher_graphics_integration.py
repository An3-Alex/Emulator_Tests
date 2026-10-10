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


# The software path, now chosen explicitly: QEMU-3dfx is the default.
SOFTWARE = dict(graphics_backend="swiftshader", swap_displays=False)

class LauncherGraphicsTests(unittest.TestCase):
    def test_gpu_initial_install_and_repeat_start_are_automatic(self):
        # A GPU copy keeps its audio/SRAM/service files current on every start
        # (graphics_update in GPU mode), not only when it is first switched.
        for marker, commands in (("REQUIRED", [["graphics", "--gpu"], ["audio"], ["gpu"], ["idle"]]),
                                 ("CURRENT", [["gpu"], ["graphics", "--gpu"], ["idle"]]),
                                 ("UPDATE_REQUIRED", [["gpu"], ["graphics", "--gpu"], ["idle"]])):
            with self.subTest(marker=marker):
                target = SimpleNamespace(events=queue.Queue(), _run_step=mock.Mock())
                selection = Selection(image="work.img", graphics_backend="qemu3dfx")
                with mock.patch.object(launcher.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, "0\n", ""),
                        subprocess.CompletedProcess([], 0, "QEMU3DFX_IMAGE_" + marker + "\n", "")]), \
                     mock.patch.object(launcher, "gpu_stage_command", return_value=["gpu"]), \
                     mock.patch.object(launcher, "graphics_update_command",
                                       side_effect=lambda *a, gpu=False: ["graphics", "--gpu"] if gpu else ["graphics"]), \
                     mock.patch.object(launcher, "audio_bridge_setup_command", return_value=["audio"]), \
                     mock.patch.object(launcher, "loader_idle_setup_command", return_value=["idle"]), \
                     mock.patch("qemu3dfx_package.verify_launch") as verify:
                    launcher.Launcher._update_graphics(target, selection)
                self.assertEqual([call.args[0] for call in target._run_step.call_args_list], commands)
                verify.assert_called_once()

    def test_failed_gpu_status_is_read_only_and_blocks_install(self):
        target = SimpleNamespace(_run_step=mock.Mock())
        with mock.patch.object(launcher.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "0\n", ""),
                subprocess.CompletedProcess([], 1, "", "invalid image")]), \
             mock.patch.object(launcher, "gpu_stage_command", return_value=["status"]):
            with self.assertRaisesRegex(RuntimeError, "invalid image"):
                launcher.Launcher._update_graphics(target, Selection(graphics_backend="qemu3dfx"))
        target._run_step.assert_not_called()

    def test_invalid_original_path_blocks_all_image_updates(self):
        target = SimpleNamespace(events=queue.Queue(), _update_graphics=mock.Mock())
        selection = Selection(image="original.img", original_image="original.img")
        with mock.patch.object(launcher, "check_runtime", return_value=[
                "CF-Image: Original darf nicht als Arbeitskopie gestartet werden"]), \
             mock.patch.object(launcher.subprocess, "run") as run:
            launcher.Launcher._check_worker(target, selection, True)
        run.assert_not_called()
        target._update_graphics.assert_not_called()
        self.assertTrue(target.events.get()[1][2])

    def test_only_start_updates_ready_working_copy(self):
        for start in (False, True):
            with self.subTest(start=start):
                selection = Selection(image="working.img")
                resolved = Selection(image="working.img", db_key="D27B7159")
                target = SimpleNamespace(events=queue.Queue(), _update_graphics=mock.Mock(),
                                         _resolve_database_key=mock.Mock(return_value=resolved),
                                         _report_database_version=mock.Mock())
                with mock.patch.object(launcher, "check_runtime", return_value=[]), \
                     mock.patch.object(launcher, "stage_check_command", return_value=["read-only"]), \
                     mock.patch.object(launcher.subprocess, "run", return_value=
                                       subprocess.CompletedProcess([], 0, "ready\n", "")):
                    launcher.Launcher._check_worker(target, selection, start)
                self.assertEqual(target._update_graphics.call_count, int(start))
                # Only a start resolves the database key; the launch uses the resolved key.
                self.assertEqual(target._resolve_database_key.call_count, int(start))
                self.assertEqual(target._report_database_version.call_count, int(start))
                expected = resolved if start else selection
                self.assertEqual(target.events.get(), ("checked", (expected, start, [])))

    def test_key_search_failure_blocks_start(self):
        target = SimpleNamespace(events=queue.Queue(), _update_graphics=mock.Mock(),
                                 _resolve_database_key=mock.Mock(side_effect=ValueError("kein Schlüssel")),
                                 _report_database_version=mock.Mock())
        with mock.patch.object(launcher, "check_runtime", return_value=[]), \
             mock.patch.object(launcher, "stage_check_command", return_value=["read-only"]), \
             mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "ready\n", "")):
            launcher.Launcher._check_worker(target, Selection(image="working.img"), True)
        self.assertEqual(target.events.get()[1][2], ["Prüfung fehlgeschlagen: kein Schlüssel"])
        target._report_database_version.assert_not_called()

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

    def test_clear_probe_updates_runtime_then_enables_bridge(self):
        target = SimpleNamespace(_run_step=mock.Mock())
        selection = Selection(image="working.img", **SOFTWARE)
        with mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "0\n", "")), \
             mock.patch.object(launcher, "graphics_update_command", return_value=["guarded-update"]), \
             mock.patch.object(launcher, "audio_bridge_setup_command", return_value=["guarded-backend"]), \
             mock.patch.object(launcher, "loader_idle_setup_command", return_value=["guarded-idle"]):
            launcher.Launcher._update_graphics(target, selection)
        self.assertEqual(target._run_step.call_count, 3)
        self.assertEqual(target._run_step.call_args_list[0].args[0], ["guarded-update"])
        self.assertEqual(target._run_step.call_args_list[1].args[0], ["guarded-backend"])
        self.assertEqual(target._run_step.call_args.args[0], ["guarded-idle"])

    def test_bridge_skips_ac97_driver_installation(self):
        target = SimpleNamespace(_run_step=mock.Mock())
        with mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "0\n", "")), \
             mock.patch.object(launcher, "graphics_update_command", return_value=["guarded-update"]), \
             mock.patch.object(launcher, "audio_bridge_setup_command", return_value=["guarded-backend"]), \
             mock.patch.object(launcher, "loader_idle_setup_command", return_value=["guarded-idle"]):
            launcher.Launcher._update_graphics(target, Selection(audio_output="bridge", **SOFTWARE))
        self.assertFalse(hasattr(launcher, "audio_setup_command"))
        self.assertEqual(target._run_step.call_count, 3)

    def test_failed_graphics_update_prevents_bridge_marker_change(self):
        target = SimpleNamespace(_run_step=mock.Mock(side_effect=RuntimeError("Update failed")))
        with mock.patch.object(launcher.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "0\n", "")), \
             mock.patch.object(launcher, "graphics_update_command", return_value=["guarded-update"]), \
             mock.patch.object(launcher, "audio_bridge_setup_command") as backend:
            with self.assertRaisesRegex(RuntimeError, "Update failed"):
                launcher.Launcher._update_graphics(target, Selection(**SOFTWARE))
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
