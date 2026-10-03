"""Additive import, persistent data preservation and reversible installation."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import service_sram as sram


def fixture():
    data = bytearray(0x600)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x14C, 2)
    struct.pack_into("<H", data, 0x94, 224)
    opt = 0x98
    struct.pack_into("<H", data, opt, 0x10B)
    struct.pack_into("<IIII", data, opt + 28, 0x400000, 0x1000, 0x200, 0)
    struct.pack_into("<II", data, opt + 56, 0x3000, 0x200)
    struct.pack_into("<I", data, opt + 92, 16)
    struct.pack_into("<II", data, opt + 104, 0x1000, 40)
    for index, rva, raw in ((0, 0x1000, 0x200), (1, 0x2000, 0x400)):
        struct.pack_into("<8sIIIIIIHHI", data, 0x178 + index * 40,
                         b".idata" if index == 0 else b".rsrc", 0x200, rva, 0x200, raw, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<IIIII", data, 0x200, 0, 0, 0, 0x1040, 0x1060)
    data[0x240:0x24D] = b"SETUPAPI.DLL\0"
    data[0x400:0x409] = b"resources"
    return bytes(data)


class ServiceSramTests(unittest.TestCase):
    def setUp(self):
        self.original = fixture()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "guest"
        self.proxy = Path(self.temporary.name) / "proxy.dll"
        self.proxy.write_bytes(b"new-dll")
        self.service = self.root / sram.SERVICE_PATH
        self.service.parent.mkdir(parents=True)
        self.service.write_bytes(self.original)
        for directory in ("WorkDir", "NVRAM"):
            target = self.root / directory / "FBWFLIB.dll"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"old-dll")
        self.sram_file = self.root / "NVRAM/m90_sram.bin"
        self.sram_file.write_bytes(b"existing configuration")
        for name, value in (("SERVICE_HASH", sram.digest(self.original)),
                            ("SRAM_HASH", sram.digest(b"new-dll")),
                            ("PREVIOUS_SRAM_HASH", sram.digest(b"old-dll"))):
            context = patch.object(sram, name, value)
            context.start(); self.addCleanup(context.stop)

    def test_patch_preserves_code_resources_and_old_imports(self):
        output = sram.patch_service(self.original)
        self.assertEqual(output[0x200:0x600], self.original[0x200:0x600])
        self.assertEqual(struct.unpack_from("<H", output, 0x86)[0], 3)
        import_rva, size = struct.unpack_from("<II", output, 0x98 + 104)
        self.assertEqual((import_rva, size), (0x3000, 60))
        self.assertEqual(output[0x600:0x614], self.original[0x200:0x214])
        self.assertIn(b"FBWFLIB.dll\0", output[0x600:])
        self.assertIn(b"\0\0SramCompatInitialize\0", output[0x600:])
        self.assertEqual(output, sram.patch_service(self.original))

    def test_unknown_service_and_missing_header_space_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            sram.patch_service(b"foreign executable")
        changed = bytearray(self.original); changed[0x1C8] = 1
        with patch.object(sram, "SERVICE_HASH", sram.digest(changed)):
            with self.assertRaisesRegex(ValueError, "No safe space"):
                sram.patch_service(changed)

    def test_readonly_preflight_then_idempotent_update_preserves_sram(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertIn("required", sram.update(self.root, self.proxy, check_only=True))
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertIn("updated", sram.update(self.root, self.proxy))
        self.assertEqual(self.service.read_bytes(), sram.patch_service(self.original))
        self.assertEqual(self.service.with_name(self.service.name + ".pre-m90-sram").read_bytes(), self.original)
        self.assertEqual(self.sram_file.read_bytes(), b"existing configuration")
        self.assertEqual(sram.update(self.root, self.proxy), "Service SRAM already current")

    def test_unknown_dll_blocks_all_changes(self):
        (self.root / "WorkDir/FBWFLIB.dll").write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            sram.update(self.root, self.proxy)
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertFalse(self.service.with_name(self.service.name + ".pre-m90-sram").exists())

    def test_failed_executable_replace_rolls_back_dlls(self):
        real_replace = sram.os.replace
        def fail_executable(source, target):
            if Path(target).resolve() == self.service.resolve(): raise OSError("injected failure")
            return real_replace(source, target)
        with patch.object(sram.os, "replace", side_effect=fail_executable):
            with self.assertRaisesRegex(OSError, "injected"):
                sram.update(self.root, self.proxy)
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual((self.root / "NVRAM/FBWFLIB.dll").read_bytes(), b"old-dll")
        self.assertEqual((self.root / "WorkDir/FBWFLIB.dll").read_bytes(), b"old-dll")
        self.assertFalse(self.service.with_name("FBWFLIB.dll").exists())
        self.assertEqual(list(self.root.rglob("*.m90-sram-new")), [])


if __name__ == "__main__": unittest.main()
