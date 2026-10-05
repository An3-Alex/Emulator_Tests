"""Offline fixtures for targeted, recoverable legacy-driver isolation."""
import json
import re
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_legacy_driver as legacy
import audio_image_stage as stage
import image_setup


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before = {
            "WINDOWS/system32/drivers/ALCXWDM.SYS": b"old SYS",
            "WINDOWS/INF/realtekac97.inf": b"[CopyFiles]\nALCXWDM.SYS\n",
            "WINDOWS/INF/realtekac97.PNF": b"cache",
            "WINDOWS/INF/oem17.inf": "ALCXWDM.SYS".encode("utf-16"),
            "WINDOWS/INF/oem17.pnf": b"oem cache",
            "WINDOWS/INF/network.inf": b"unrelated network driver",
        }
        for relative, data in self.before.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_only_targeted_files_move_and_every_original_has_verified_backup(self):
        legacy.quarantine(self.root)
        legacy.validate_quarantine(self.root)
        entries = legacy.manifest(self.root)
        self.assertEqual(len(entries), 5)
        for entry in entries:
            self.assertFalse((self.root / entry["path"]).exists())
            self.assertEqual((self.root / legacy.BACKUP / entry["path"]).read_bytes(), self.before[entry["path"]])
        self.assertEqual((self.root / "WINDOWS/INF/network.inf").read_bytes(), b"unrelated network driver")
        legacy.quarantine(self.root)
        self.assertEqual(legacy.manifest(self.root), entries)

    def test_interrupted_removal_can_resume(self):
        unlink = Path.unlink
        count = 0
        def interrupted(path, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("interrupted")
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                legacy.quarantine(self.root)
        self.assertEqual(len(legacy.manifest(self.root)), 5)
        legacy.quarantine(self.root)
        legacy.validate_quarantine(self.root)

    def test_changed_reappearing_file_is_not_deleted(self):
        legacy.quarantine(self.root)
        source = self.root / "WINDOWS/system32/drivers/ALCXWDM.SYS"
        source.write_bytes(b"new driver belongs to user")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            legacy.quarantine(self.root)
        self.assertEqual(source.read_bytes(), b"new driver belongs to user")

    def test_same_reappearing_file_is_isolated_again(self):
        legacy.quarantine(self.root)
        source = self.root / "WINDOWS/system32/drivers/ALCXWDM.SYS"
        source.write_bytes(self.before["WINDOWS/system32/drivers/ALCXWDM.SYS"])
        legacy.quarantine(self.root)
        legacy.validate_quarantine(self.root)

    def test_new_oem_inf_is_detected_instead_of_silently_accepted(self):
        legacy.quarantine(self.root)
        (self.root / "WINDOWS/INF/oem999.inf").write_bytes(b"ALCXWDM.SYS")
        with self.assertRaisesRegex(ValueError, "loadable"):
            legacy.validate_quarantine(self.root)

    def test_manifest_cannot_target_unrelated_or_outside_paths(self):
        legacy.quarantine(self.root)
        marker = self.root / legacy.MANIFEST
        saved = json.loads(marker.read_text())
        for relative in ("../outside", "WINDOWS/system32/portcls.sys", "WINDOWS/system32/drivers/other.sys", "WINDOWS/INF/../../outside", "C:/outside"):
            with self.subTest(relative=relative):
                data = json.loads(json.dumps(saved))
                data["files"][0]["path"] = relative
                marker.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    legacy.manifest(self.root)
        marker.write_text(json.dumps(saved))

    def test_backup_copy_failure_never_removes_source(self):
        with mock.patch.object(legacy, "durable_copy", side_effect=OSError("no space")):
            with self.assertRaisesRegex(OSError, "no space"):
                legacy.quarantine(self.root)
        for relative, data in self.before.items():
            self.assertEqual((self.root / relative).read_bytes(), data)
        self.assertFalse((self.root / legacy.MANIFEST).exists())

    def test_expected_legacy_files_must_exist_before_any_removal(self):
        (self.root / "WINDOWS/system32/drivers/ALCXWDM.SYS").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete backup"):
            legacy.quarantine(self.root)
        self.assertTrue((self.root / "WINDOWS/INF/realtekac97.inf").exists())


class BinaryPinsTests(unittest.TestCase):
    def test_bundled_audio_helpers_and_stage_share_hashes(self):
        self.assertEqual(image_setup.COMPONENTS["audio_software"][1], stage.SOFTWARE_HASH)
        self.assertEqual(image_setup.COMPONENTS["audio_installer"][1], stage.INSTALLER_HASH)
        self.assertEqual(image_setup.COMPONENTS["audio_verify"][1], stage.VERIFIER_HASH)

    @unittest.skipUnless(shutil.which("wsl.exe"), "WSL bash unavailable")
    def test_legacy_image_checker_accepts_all_resumable_audio_phases_without_mount(self):
        source = (Path(__file__).resolve().parents[1] / "scripts/check_image_stage.sh").read_text()
        branch = re.search(r'case "\$audio_stage" in.*?esac', source, re.S).group(0)
        states = ("staging", "software", "install", "verify", "ready", "legacy-install", "legacy-verify", "legacy-ready")
        # Execute just the status case in subshells; no image, mount or VM calls.
        command = 'set -e; for audio_stage in "$@"; do ( ' + branch + ' ); done'
        result = subprocess.run(["wsl.exe", "--exec", "bash", "-c", command, "--", *states],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["legacy-ready"] * len(states))
        bad = subprocess.run(["wsl.exe", "--exec", "bash", "-c", command, "--", "unknown"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(bad.returncode, 3)


if __name__ == "__main__":
    unittest.main()
