"""The loader's device routine is patched wherever it is found, never by file version."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import guest_files
import patch_loader_null_device as null
import unittest.mock

VERIFIED_ORIGINALS = guest_files.ORIGINALS
ROUTINE = (null.ROUTINE_HEAD + null.PATCH_EXPECTED + null.ROUTINE_CALL + bytes.fromhex("04 92 42 00")
           + null.ROUTINE_CHECK + bytes.fromhex("10 8b 4e 28 51 e8 90 03 00 00 83 c4 04") + null.ROUTINE_FAILURE)


def loader(routine_at=0x1430, *, virtual_size=0x284E, raw_size=0x3000, routine=ROUTINE):
    """A minimal executable: headers, one code section at file offset 0x1000."""
    data = bytearray(0x1000 + raw_size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x14C, 1)
    struct.pack_into("<H", data, 0x80 + 20, 0xE0)
    struct.pack_into("<I", data, 0x80 + 24 + 28, 0x400000)
    struct.pack_into("<8sIIII", data, 0x80 + 24 + 0xE0, b".text", virtual_size, 0x1000, raw_size, 0x1000)
    data[0x1000:0x1000 + virtual_size] = b"\x90" * virtual_size
    data[routine_at:routine_at + len(routine)] = routine
    return bytes(data)


class LoaderNullDeviceTests(unittest.TestCase):
    def test_routine_is_patched_wherever_this_build_keeps_it(self):
        for routine_at in (0x1430, 0x2011):
            with self.subTest(routine_at=routine_at):
                source = loader(routine_at)
                output = null.patched(source)
                site = routine_at + len(null.ROUTINE_HEAD)
                cave = 0x1000 + 0x2850                      # behind the code, aligned to 16
                self.assertEqual(output[site], 0xE9)
                self.assertEqual(site + 5 + struct.unpack_from("<i", output, site + 1)[0], cave)
                self.assertEqual(output[site + 5:site + 7], b"\x90\x90")
                code = null.trampoline(site, cave)
                self.assertEqual(output[cave:cave + len(code)], code)
                # The trampoline returns to the instruction behind the replaced ones.
                back = cave + 11
                self.assertEqual(back + 5 + struct.unpack_from("<i", code, 12)[0], site + 7)
                # Only the site, the trampoline and the section's virtual size change.
                changed = {i for i in range(len(source)) if source[i] != output[i]}
                allowed = set(range(site, site + 7)) | set(range(cave, cave + len(code))) \
                    | set(range(0x80 + 24 + 0xE0 + 8, 0x80 + 24 + 0xE0 + 12))
                self.assertLessEqual(changed, allowed)
                self.assertEqual(struct.unpack_from("<I", output, 0x80 + 24 + 0xE0 + 8)[0], 0x3000)
                self.assertEqual(null.trampoline_offset(output), cave)
                self.assertIsNone(null.trampoline_offset(source))
                self.assertEqual(null.patched(output), output)  # already patched: nothing left to find

    def test_loader_without_the_routine_stays_unchanged(self):
        other = loader(routine=b"\x90" * len(ROUTINE))
        self.assertEqual(null.patched(other), other)
        self.assertEqual(null.patched(b"not an executable"), b"not an executable")
        # The same instructions with another failure exit are not this routine.
        different_exit = ROUTINE.replace(null.ROUTINE_FAILURE, bytes.fromhex("33 c0 5e c2"))
        changed = loader(routine=different_exit)
        self.assertEqual(null.patched(changed), changed)
        # Two candidates cannot be told apart.
        twice = bytearray(loader())
        twice[0x2100:0x2100 + len(ROUTINE)] = ROUTINE
        self.assertEqual(null.patched(bytes(twice)), bytes(twice))

    def test_used_or_missing_padding_is_never_overwritten(self):
        used = bytearray(loader())
        used[0x1000 + 0x2900] = 0x55                        # something lives behind the code
        self.assertEqual(null.patched(bytes(used)), bytes(used))
        full = loader(virtual_size=0x2FF0)                  # no room for the trampolines
        self.assertEqual(null.patched(full), full)

    def test_verified_loader_must_give_its_verified_result(self):
        source = loader()
        with unittest.mock.patch.object(null, "ORIGINAL_SHA256", null.digest(source)):
            with self.assertRaisesRegex(ValueError, "verified form"):
                null.patched(source)
            without = loader(routine=b"\x90" * len(ROUTINE))
            with unittest.mock.patch.object(null, "ORIGINAL_SHA256", null.digest(without)):
                with self.assertRaisesRegex(ValueError, "verified loader"):
                    null.patched(without)

    def test_command_line_writes_and_verifies_the_patched_form(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "explorer_original.exe", Path(directory) / "patched.exe"
            source.write_bytes(loader())
            run = lambda *arguments: subprocess.run(
                [sys.executable, str(SCRIPTS / "patch_loader_null_device.py"), *arguments],
                capture_output=True, text=True)
            written = run(str(source), str(target))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertIn("nicht verifiziert", written.stdout)
            self.assertEqual(target.read_bytes(), null.patched(source.read_bytes()))
            self.assertEqual(run("--verify", str(source), str(target)).returncode, 0)
            target.write_bytes(source.read_bytes())
            self.assertNotEqual(run("--verify", str(source), str(target)).returncode, 0)
            # A loader without the routine: copied unchanged, and that is its verified form.
            source.write_bytes(loader(routine=b"\x90" * len(ROUTINE)))
            written = run(str(source), str(target))
            self.assertIn("bleibt unverändert", written.stdout)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(run("--verify", str(source), str(target)).returncode, 0)


class GuestFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.originals = tuple((relative, guest_files.hashlib.sha256(relative.encode()).hexdigest(), label)
                               for relative, _, label in guest_files.ORIGINALS)
        patcher = unittest.mock.patch.object(guest_files, "ORIGINALS", self.originals)
        patcher.start(); self.addCleanup(patcher.stop)
        for relative, _, _ in self.originals:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())

    def test_verified_card_needs_no_note(self):
        self.assertEqual(guest_files.original_notes(self.root), [])

    def test_other_versions_are_named_once_each(self):
        (self.root / "NVRAM/game.exe").write_bytes(b"other games")
        (self.root / "WorkDir/game.exe").write_bytes(b"other games")
        (self.root / "WINDOWS/explorer.exe").write_bytes(b"other loader")
        notes = guest_files.original_notes(self.root)
        self.assertEqual(len(notes), 2)
        self.assertIn("Loader (explorer.exe)", notes[0])
        self.assertIn("Spielprogramm (game.exe)", notes[1])

    def test_missing_file_means_it_is_not_such_an_image(self):
        (self.root / "WINDOWS/system32/Cgos.dll").unlink()
        with self.assertRaisesRegex(ValueError, "missing file"):
            guest_files.original_notes(self.root)

    def test_verified_hashes_are_the_ones_the_updates_use(self):
        import graphics_update
        verified = {relative: value for relative, value, _ in VERIFIED_ORIGINALS}
        self.assertEqual(verified["NVRAM/game.exe"], graphics_update.GAME_HASH)
        self.assertEqual(verified["WorkDir/game.exe"], graphics_update.GAME_HASH)
        self.assertEqual(verified["WINDOWS/explorer.exe"], null.ORIGINAL_SHA256)


if __name__ == "__main__":
    unittest.main()
