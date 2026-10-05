"""Filesystem fixtures, no guest image, VM or external owner files involved."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import graphics_update as graphics
import image_setup


def digest(data):
    return hashlib.sha256(data).hexdigest()


class GraphicsUpdateTests(unittest.TestCase):
    def test_service_update_is_not_skipped_when_graphics_already_current(self):
        self.run_update()
        with mock.patch("service_sram.update", side_effect=["Service SRAM update required", "Service SRAM updated"]) as service:
            self.assertEqual(self.run_update(sram=Path("proxy.dll")), "Service SRAM updated")
        self.assertEqual(service.call_count, 2)

    def test_unknown_service_blocks_graphics_before_any_write(self):
        with mock.patch("service_sram.update", side_effect=ValueError("Unrecognized service")):
            with self.assertRaisesRegex(ValueError, "Unrecognized service"):
                self.run_update(sram=Path("proxy.dll"))
        self.assert_original_files()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.guest = self.root / "guest"
        self.bootstrap = self.root / "new-bootstrap"
        self.proxy = self.root / "new-proxy"
        self.bootstrap.write_bytes(b"new-bootstrap")
        self.proxy.write_bytes(b"new-proxy")
        for name, value in {
            "BOOTSTRAP_HASH": digest(b"new-bootstrap"), "PROXY_HASH": digest(b"new-proxy"),
            "PREVIOUS_BOOTSTRAP": digest(b"old-bootstrap"),
            "PREVIOUS_PROXIES": {digest(b"old-proxy")},
            "CGOS_HASH": digest(b"cgos"), "GAME_HASH": digest(b"game"),
            "SWIFTSHADER_HASH": digest(b"swift"),
        }.items():
            self.stack.enter_context(mock.patch.object(graphics, name, value))
        files = {"WINDOWS/explorer.exe": b"old-bootstrap",
                 "WINDOWS/system32/Cgos.dll": b"cgos",
                 "NVRAM/m90_setup_stage.txt": b"stage=ready\n"}
        for directory in ("NVRAM", "WorkDir"):
            files.update({f"{directory}/game.exe": b"game",
                          f"{directory}/swiftshader_d3d9.dll": b"swift",
                          f"{directory}/d3d9.dll": b"old-proxy"})
        self.before = files
        for relative, value in files.items():
            path = self.guest / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)

    def run_update(self, **options):
        return graphics.update(self.guest, self.bootstrap, self.proxy, **options)

    def assert_original_files(self):
        for relative, data in self.before.items():
            self.assertEqual((self.guest / relative).read_bytes(), data)

    def test_read_only_validation_makes_no_backup_or_writes(self):
        self.assertEqual(self.run_update(check_only=True), "Graphics update required")
        self.assert_original_files()
        self.assertFalse((self.guest / "NVRAM/m90-graphics-backups").exists())

    def test_installs_three_files_keeps_backup_and_is_idempotent(self):
        self.assertIn("Graphics updated", self.run_update())
        self.assertEqual((self.guest / "WINDOWS/explorer.exe").read_bytes(), b"new-bootstrap")
        for directory in ("NVRAM", "WorkDir"):
            self.assertEqual((self.guest / directory / "d3d9.dll").read_bytes(), b"new-proxy")
            self.assertEqual((self.guest / directory / "game.exe").read_bytes(), b"game")
        backups = list((self.guest / "NVRAM/m90-graphics-backups").iterdir())
        self.assertEqual(len(backups), 1)
        for relative in ("WINDOWS/explorer.exe", "NVRAM/d3d9.dll", "WorkDir/d3d9.dll"):
            self.assertEqual((backups[0] / relative).read_bytes(), self.before[relative])
        self.assertEqual(json.loads((backups[0] / "manifest.json").read_text())["state"], "installed")
        self.assertEqual(self.run_update(), "Graphics already current")
        self.assertEqual(len(list((self.guest / "NVRAM/m90-graphics-backups").iterdir())), 1)

    def test_unknown_last_target_aborts_before_any_change(self):
        target = self.guest / "WorkDir/d3d9.dll"
        target.write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.run_update()
        self.assertEqual((self.guest / "WINDOWS/explorer.exe").read_bytes(), b"old-bootstrap")
        self.assertFalse((self.guest / "NVRAM/m90-graphics-backups").exists())

    def test_failed_second_replace_restores_every_changed_file(self):
        original_replace = graphics.os.replace
        count = 0
        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected I/O failure")
            original_replace(source, target)
        with mock.patch.object(graphics.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "I/O failure"):
                self.run_update()
        self.assert_original_files()
        self.assertEqual(list(self.guest.rglob("*.m90-graphics-new")), [])
        manifest = next((self.guest / "NVRAM/m90-graphics-backups").glob("*/manifest.json"))
        self.assertEqual(json.loads(manifest.read_text())["state"], "rolled-back")

    def test_unprepared_stage_and_wrong_source_are_rejected(self):
        (self.guest / "NVRAM/m90_setup_stage.txt").write_text("stage=qxl-pnp")
        with self.assertRaisesRegex(ValueError, "ready"):
            self.run_update()
        self.proxy.write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.run_update()

    def test_interrupted_staging_is_not_silently_overwritten(self):
        (self.guest / "WorkDir/d3d9.dll.m90-graphics-new").write_bytes(b"recover")
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.run_update()
        self.assert_original_files()

    def audio_fixture(self):
        audio = self.root / "new-audio"
        audio.write_bytes(b"new-audio")
        for name, value in {"AUDIO_HASH": digest(b"new-audio"),
                "PREVIOUS_AUDIO": {digest(b"old-audio")},
                "ORIGINAL_AUDIO_HASH": digest(b"original-audio")}.items():
            self.stack.enter_context(mock.patch.object(graphics, name, value))
        for relative, value in {"WINDOWS/system32/irrKlang.dll": b"original-audio",
                "WINDOWS/INF/realtekac97.inf": b"driver inf",
                "WINDOWS/system32/drivers/ALCXWDM.SYS": b"driver sys",
                "NVRAM/irrKlang.dll": b"old-audio", "WorkDir/irrKlang.dll": b"old-audio"}.items():
            self.before[relative] = value
            path = self.guest / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        return audio

    def test_audio_update_is_validated_backed_up_and_idempotent(self):
        audio = self.audio_fixture()
        self.assertEqual(self.run_update(audio=audio, check_only=True), "Runtime update required")
        self.assert_original_files()
        self.assertIn("Runtime updated", self.run_update(audio=audio))
        for directory in ("NVRAM", "WorkDir"):
            self.assertEqual((self.guest / directory / "irrKlang.dll").read_bytes(), b"new-audio")
        backup = next((self.guest / "NVRAM/m90-graphics-backups").iterdir())
        self.assertEqual((backup / "WorkDir/irrKlang.dll").read_bytes(), b"old-audio")
        self.assertEqual((self.guest / "WINDOWS/system32/irrKlang.dll").read_bytes(), b"original-audio")
        self.assertEqual(self.run_update(audio=audio), "Runtime already current")

    def test_audio_target_and_original_library_must_be_known_before_any_write(self):
        audio = self.audio_fixture()
        for relative in ("WorkDir/irrKlang.dll", "WINDOWS/system32/irrKlang.dll"):
            with self.subTest(relative=relative):
                target = self.guest / relative
                target.write_bytes(b"unknown")
                with self.assertRaisesRegex(ValueError, "Unrecognized"):
                    self.run_update(audio=audio)
                self.assertFalse((self.guest / "NVRAM/m90-graphics-backups").exists())
                target.write_bytes(self.before[relative])
        self.assert_original_files()

    def test_audio_update_accepts_only_validated_legacy_quarantine(self):
        audio = self.audio_fixture()
        from audio_legacy_driver import quarantine, MANIFEST
        (self.guest / "WINDOWS/INF/realtekac97.inf").write_bytes(b"ALCXWDM.SYS")
        quarantine(self.guest)
        self.assertEqual(self.run_update(audio=audio, check_only=True), "Runtime update required")
        record = self.guest / MANIFEST
        data = record.read_text()
        record.write_text('{"version":1,"files":[]}')
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.run_update(audio=audio)
        self.assertFalse((self.guest / "NVRAM/m90-graphics-backups").exists())
        record.write_text(data)

    def test_audio_replace_failure_rolls_back_graphics_and_audio(self):
        audio = self.audio_fixture()
        original_replace = graphics.os.replace
        count = 0
        def fail_fifth(source, target):
            nonlocal count
            count += 1
            if count == 5:
                raise OSError("audio I/O failure")
            original_replace(source, target)
        with mock.patch.object(graphics.os, "replace", side_effect=fail_fifth):
            with self.assertRaisesRegex(OSError, "audio I/O failure"):
                self.run_update(audio=audio)
        self.assert_original_files()
        self.assertEqual(list(self.guest.rglob("*.m90-graphics-new")), [])


class GraphicsPinsTests(unittest.TestCase):
    def test_updater_and_bundle_expect_the_same_binaries(self):
        self.assertEqual(graphics.BOOTSTRAP_HASH, image_setup.COMPONENTS["bootstrap"][1])
        self.assertEqual(graphics.PROXY_HASH, image_setup.COMPONENTS["d3d9"][1])
        self.assertEqual(graphics.AUDIO_HASH, image_setup.COMPONENTS["irrklang"][1])


if __name__ == "__main__":
    unittest.main()
