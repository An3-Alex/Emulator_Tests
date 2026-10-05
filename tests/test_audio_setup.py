from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_driver_package as package
import audio_image_stage as stage
import audio_setup_runner as runner
import audio_legacy_driver as legacy


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PackageTests(unittest.TestCase):
    def test_only_verified_allowlisted_members_are_extracted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "package.exe"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("ich/wdm/stac97.sys", b"driver")
                z.writestr("../../outside", b"bad")
            with mock.patch.object(package, "FILES", {"stac97.sys":digest(b"driver")}), \
                 mock.patch.object(package, "PACKAGE_HASH", digest(archive.read_bytes())):
                package.extract(archive, root / "out")
                self.assertEqual((root / "out/stac97.sys").read_bytes(), b"driver")
                self.assertEqual(len(list((root / "out").iterdir())), 1)

    def test_unknown_package_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "bad.exe"
            file.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                package.extract(file, root / "out")
            self.assertFalse((root / "out").exists())


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.guest = self.root / "guest"
        self.installer, self.verifier, self.driver = [self.root / n for n in ("install", "verify", "driver")]
        self.installer.write_bytes(b"install")
        self.software = self.root / "audio-software.exe"
        self.software.write_bytes(b"software helper")
        self.verifier.write_bytes(b"verify")
        self.driver.mkdir()
        (self.driver / "stac97.sys").write_bytes(b"driver")
        for key, value in {"BOOTSTRAP_HASH":digest(b"shell"), "PREVIOUS_BOOTSTRAP":digest(b"old shell"),
                "CGOS_HASH":digest(b"cgos"), "INSTALLER_HASH":digest(b"install"),
                "SOFTWARE_HASH":digest(b"software helper"),
                "VERIFIER_HASH":digest(b"verify"), "FILES":{"stac97.sys":digest(b"driver")}}.items():
            self.stack.enter_context(mock.patch.object(stage, key, value))
        self.stack.enter_context(mock.patch.object(stage, "validate"))
        self.starts = {"ControlSet001/Services/ALCXWDM":3, "ControlSet001/Services/FBWF":0,
                       "ControlSet002/Services/ALCXWDM":3, "ControlSet002/Services/FBWF":0}
        def reg(root, values=None):
            before = dict(self.starts)
            if values is not None:
                self.starts.update(values)
            return before
        self.stack.enter_context(mock.patch.object(stage, "registry", side_effect=reg))
        for name, data in {"WINDOWS/explorer.exe":b"shell", "WINDOWS/system32/Cgos.dll":b"cgos",
                           "WINDOWS/system32/config/SYSTEM":b"system",
                           "WINDOWS/system32/config/SOFTWARE":b"software",
                           "WINDOWS/INF/realtekac97.inf":b"ALCXWDM.SYS",
                           "WINDOWS/INF/realtekac97.pnf":b"old cache",
                           "WINDOWS/system32/drivers/ALCXWDM.SYS":b"legacy driver",
                           "WINDOWS/system32/dllcache/ALCXWDM.SYS":b"legacy driver",
                           **{f"WINDOWS/system32/drivers/{name}.sys":b"original" for name in ("swenum","sysaudio","wdmaud","kmixer")},
                           "WINDOWS/system32/streamci.dll":b"original"}.items():
            file = self.guest / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        (self.guest / "NVRAM").mkdir()

    def run_stage(self, action):
        # Most pre-existing tests begin at hardware installation. Exercise the
        # preceding no-card phase explicitly, rather than bypassing its guard.
        if action == "install" and stage.status(self.guest) in ("required", "staging"):
            self.run_stage("software")
            self.software_result()
        return stage.stage(self.guest, action, self.installer, self.verifier, self.driver, self.software)

    def software_result(self):
        (self.guest / "NVRAM/m90_audio_software.log").write_text(
            "XP audio software devices ready.\nSoftware audio preparation complete.\n")

    def install_result(self):
        file = self.guest / "WINDOWS/system32/drivers/stac97.sys"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"driver")
        (self.guest / "NVRAM/m90_audio_install.log").write_text(
            "DIF_REGISTERCOINSTALLERS result: 0x00000001\nDIF_INSTALLINTERFACES result: 0x00000001\n"
            "Matched devices: 0x00000001\nInstalled devices: 0x00000001\nInstaller complete.\n")

    def test_check_is_read_only(self):
        self.assertEqual(self.run_stage("check"), "required")
        self.assertFalse((self.guest / stage.MARKER).exists())
        self.assertEqual((self.guest / "WINDOWS/explorer.exe").read_bytes(), b"shell")

    def test_complete_cycle_preserves_originals_disables_crashing_driver_and_restores_fbwf(self):
        self.assertEqual(self.run_stage("install"), "install")
        backup = self.guest / stage.BACKUP
        self.assertEqual((backup / "SYSTEM").read_bytes(), b"system")
        self.assertEqual((backup / "explorer.exe").read_bytes(), b"shell")
        self.assertEqual(set(self.starts.values()), {4})
        self.install_result()
        self.assertEqual(self.run_stage("verify"), "verify")
        (self.guest / "NVRAM/m90_audio_verify.log").write_text(
            "waveOutOpen: 0x00000000\nwaveOutWrite: 0x00000000\nPlayback completed: 0x00000001\nAudio verified\n")
        self.assertEqual(self.run_stage("finish"), "ready")
        self.assertEqual(self.run_stage("check"), "ready")
        self.assertEqual((self.guest / "WINDOWS/explorer.exe").read_bytes(), b"shell")
        self.assertEqual(self.starts["ControlSet001/Services/ALCXWDM"], 4)
        self.assertEqual(self.starts["ControlSet002/Services/ALCXWDM"], 4)
        self.assertEqual(self.starts["ControlSet001/Services/FBWF"], 0)

    def test_missing_guest_success_cannot_finalize(self):
        self.run_stage("install")
        with self.assertRaises(FileNotFoundError):
            self.run_stage("verify")
        self.assertEqual(self.run_stage("check"), "install")
        self.install_result()
        self.run_stage("verify")
        (self.guest / "NVRAM/m90_audio_verify.log").write_text("Audio verified\n")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.run_stage("finish")
        self.assertEqual(self.run_stage("check"), "verify")

    def test_oem_driver_filename_case_is_accepted(self):
        self.run_stage("install")
        self.install_result()
        driver = self.guest / "WINDOWS/system32/drivers/stac97.sys"
        driver.rename(driver.with_name("STAC97.sys"))
        self.assertEqual(self.run_stage("verify"), "verify")

    def test_unknown_shell_refuses_changes(self):
        (self.guest / "WINDOWS/explorer.exe").write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.run_stage("install")
        self.assertFalse((self.guest / stage.BACKUP).exists())

    def test_resuming_install_keeps_first_backup(self):
        self.run_stage("install")
        self.run_stage("install")
        self.assertEqual((self.guest / stage.BACKUP / "explorer.exe").read_bytes(), b"shell")

    def test_corrupt_backup_prevents_resume(self):
        self.run_stage("install")
        (self.guest / stage.BACKUP / "SYSTEM").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.run_stage("install")

    def test_previous_installer_can_resume_and_is_replaced_without_new_backup(self):
        self.run_stage("install")
        shell = self.guest / "WINDOWS/explorer.exe"
        shell.write_bytes(b"previous installer")
        state = stage.read_state(self.guest)
        state.pop("audio_protocol")
        stage.write_state(self.guest, state)
        with mock.patch.object(stage, "PREVIOUS_INSTALLERS", {digest(b"previous installer")}):
            self.assertEqual(self.run_stage("check"), "legacy-install")
            self.assertEqual(self.run_stage("software"), "software")
            self.software_result()
            self.assertEqual(self.run_stage("install"), "install")
        self.assertEqual(shell.read_bytes(), b"install")
        self.assertEqual((self.guest / stage.BACKUP / "explorer.exe").read_bytes(), b"shell")

    def test_hardware_install_requires_successful_no_card_preparation(self):
        self.run_stage("software")
        with self.assertRaises(FileNotFoundError):
            self.run_stage("install")
        self.assertEqual(self.run_stage("check"), "software")
        (self.guest / "NVRAM/m90_audio_software.log").write_text("Preparing XP audio software devices.\n")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.run_stage("install")

    def test_old_driver_files_are_backed_up_not_loadable_and_preserve_unrelated_files(self):
        unrelated = self.guest / "WINDOWS/INF/network.inf"
        unrelated.write_bytes(b"unrelated")
        self.run_stage("software")
        legacy.validate_quarantine(self.guest)
        self.assertFalse((self.guest / "WINDOWS/system32/drivers/ALCXWDM.SYS").exists())
        self.assertFalse((self.guest / "WINDOWS/system32/dllcache/ALCXWDM.SYS").exists())
        self.assertFalse((self.guest / "WINDOWS/INF/realtekac97.inf").exists())
        self.assertEqual((self.guest / legacy.BACKUP / "WINDOWS/system32/drivers/ALCXWDM.SYS").read_bytes(), b"legacy driver")
        self.assertEqual(unrelated.read_bytes(), b"unrelated")

    def test_software_retry_is_idempotent_and_discards_stale_success(self):
        self.run_stage("software")
        self.software_result()
        self.run_stage("software")
        self.assertFalse((self.guest / "NVRAM/m90_audio_software.log").exists())
        legacy.validate_quarantine(self.guest)

    def test_corrupt_quarantine_backup_refuses_hardware_start(self):
        self.run_stage("software")
        self.software_result()
        (self.guest / legacy.BACKUP / "WINDOWS/system32/drivers/ALCXWDM.SYS").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.run_stage("install")

    def test_legacy_ready_image_migrates_without_overwriting_original_backups(self):
        self.run_stage("install")
        self.install_result()
        self.run_stage("verify")
        (self.guest / "NVRAM/m90_audio_verify.log").write_text(
            "waveOutOpen: 0x00000000\nwaveOutWrite: 0x00000000\nPlayback completed: 0x00000001\nAudio verified\n")
        self.run_stage("finish")
        state = stage.read_state(self.guest)
        state.pop("audio_protocol")
        stage.write_state(self.guest, state)
        self.assertEqual(self.run_stage("check"), "legacy-ready")
        self.run_stage("software")
        self.assertEqual((self.guest / stage.BACKUP / "SYSTEM").read_bytes(), b"system")


class RunnerTests(unittest.TestCase):
    def test_prepare_sequence_and_legacy_migration_are_gated_by_software_success(self):
        for initial in ("required", "software", "legacy-install", "legacy-verify", "legacy-ready", "install", "verify", "ready"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                original, image = project / "original.img", project / "working.img"
                original.write_bytes(b"original")
                image.write_bytes(b"copy")
                events = []
                def run(command, **kwargs):
                    action = next(word for word in command if word in ("check", "software", "install", "verify", "finish"))
                    events.append(action)
                    return subprocess.CompletedProcess(command, 0, initial if action == "check" else action, "")
                def boot(*args):
                    events.append("boot-" + args[-1])
                with mock.patch.object(runner, "require_stopped"), mock.patch.object(runner, "wsl_path", side_effect=str), \
                     mock.patch.object(runner.subprocess, "run", side_effect=run), \
                     mock.patch.object(runner, "ensure"), mock.patch.object(runner, "diagnostic_boot", side_effect=boot):
                    runner.prepare(original, image, Path("qemu"), project)
                if initial == "ready":
                    expected = ["check"]
                elif initial == "verify":
                    expected = ["check", "boot-verify", "finish"]
                elif initial == "install":
                    expected = ["check", "install", "boot-install", "verify", "boot-verify", "finish"]
                else:
                    expected = ["check", "software", "boot-software", "install", "boot-install", "verify", "boot-verify", "finish"]
                self.assertEqual(events, expected)

    def test_software_boot_failure_never_attaches_sound_card_or_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            original, image = project / "original.img", project / "working.img"
            original.write_bytes(b"original")
            image.write_bytes(b"copy")
            actions = []
            def run(command, **kwargs):
                action = next(word for word in command if word in ("check", "software", "install", "verify", "finish"))
                actions.append(action)
                return subprocess.CompletedProcess(command, 0, "required" if action == "check" else action, "")
            with mock.patch.object(runner, "require_stopped"), mock.patch.object(runner, "wsl_path", side_effect=str), \
                 mock.patch.object(runner.subprocess, "run", side_effect=run), mock.patch.object(runner, "ensure"), \
                 mock.patch.object(runner, "diagnostic_boot", side_effect=TimeoutError("software failed")) as boot:
                with self.assertRaises(TimeoutError):
                    runner.prepare(original, image, Path("qemu"), project)
            self.assertEqual(actions, ["check", "software"])
            self.assertEqual(boot.call_args.args[-1], "software")

    def test_process_guard_fails_closed(self):
        for code, output in ((1,"0"), (0,"1"), (0,"")):
            with mock.patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([],code,output,"")):
                with self.assertRaisesRegex(RuntimeError, "QEMU"):
                    runner.require_stopped()

    def test_original_cannot_be_used_as_working_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "original.img"
            file.write_bytes(b"original")
            with self.assertRaisesRegex(ValueError, "verschiedene"):
                runner.prepare(file, file, Path("qemu"), Path(folder))


if __name__ == "__main__":
    unittest.main()
