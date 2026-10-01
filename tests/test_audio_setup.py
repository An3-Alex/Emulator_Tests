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
        self.verifier.write_bytes(b"verify")
        self.driver.mkdir()
        (self.driver / "stac97.sys").write_bytes(b"driver")
        for key, value in {"BOOTSTRAP_HASH":digest(b"shell"), "PREVIOUS_BOOTSTRAP":digest(b"old shell"),
                "CGOS_HASH":digest(b"cgos"), "INSTALLER_HASH":digest(b"install"),
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
                           **{f"WINDOWS/system32/drivers/{name}.sys":b"original" for name in ("swenum","sysaudio","wdmaud","kmixer")},
                           "WINDOWS/system32/streamci.dll":b"original"}.items():
            file = self.guest / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        (self.guest / "NVRAM").mkdir()

    def run_stage(self, action):
        return stage.stage(self.guest, action, self.installer, self.verifier, self.driver)

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


class RunnerTests(unittest.TestCase):
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
