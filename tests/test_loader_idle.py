"""VM-free checks of the loader hooks, backups and original-image guards."""
from pathlib import Path
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
sys.path.insert(0, str(PROJECT / ".deps/unicorn"))
import loader_idle as idle
import image_setup
from portable_launcher_model import Selection

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EFLAGS,
    )
except ImportError:
    Uc = None


def fixture():
    data = bytearray(0x30000)
    struct.pack_into("<I", data, 0x1E8, 0x28000)
    for offset, previous, _ in idle.replacements():
        data[offset:offset + len(previous)] = previous
    return bytes(data)


class LoaderIdleTests(unittest.TestCase):
    def setUp(self):
        self.source = fixture()
        self.hash_mock = mock.patch.object(idle, "SOURCE_HASH", idle.digest(self.source))
        self.hash_mock.start()
        self.addCleanup(self.hash_mock.stop)

    def test_only_hook_sites_extra_stack_and_caves_change(self):
        patched = idle.patch_bytes(self.source)
        allowed = {p for offset, _, new in idle.replacements() for p in range(offset, offset + len(new))}
        changed = {i for i, (a, b) in enumerate(zip(self.source, patched)) if a != b}
        self.assertTrue(changed)
        self.assertLessEqual(changed, allowed)
        self.assertEqual(len(patched), len(self.source))
        self.assertTrue(idle.is_current(patched))
        self.assertEqual(idle.TIMEOUT_MS, 5000)
        modified = bytearray(patched)
        modified[0x1500] ^= 1
        self.assertFalse(idle.is_current(modified))
        with self.assertRaises(ValueError):
            idle.patch_bytes(bytes(modified))

    def test_backed_up_idempotent_update_and_read_only_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / idle.TARGET
            target.parent.mkdir()
            target.write_bytes(self.source)
            self.assertIn("required", idle.update(root, check_only=True))
            self.assertFalse((root / "NVRAM").exists())
            self.assertEqual(target.read_bytes(), self.source)
            self.assertIn("bounded", idle.update(root))
            self.assertEqual((root / idle.BACKUP).read_bytes(), self.source)
            self.assertTrue(idle.is_current(target.read_bytes()))
            self.assertIn("already", idle.update(root))
            (root / idle.BACKUP).write_bytes(b"bad backup")
            with self.assertRaises(ValueError):
                idle.update(root)

    def test_unknown_loader_or_partial_update_never_changes_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / idle.TARGET
            target.parent.mkdir()
            target.write_bytes(b"other firmware")
            with self.assertRaisesRegex(ValueError, "Unrecognized"):
                idle.update(root)
            self.assertFalse((root / "NVRAM").exists())
            target.write_bytes(self.source)
            target.with_name(target.name + ".idle-new").write_bytes(b"unfinished")
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                idle.update(root)
            self.assertEqual(target.read_bytes(), self.source)

    def test_failed_atomic_replace_preserves_original_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / idle.TARGET
            target.parent.mkdir()
            target.write_bytes(self.source)
            with mock.patch.object(idle.os, "replace", side_effect=OSError("I/O failure")):
                with self.assertRaises(OSError):
                    idle.update(root)
            self.assertEqual(target.read_bytes(), self.source)
            self.assertEqual((root / idle.BACKUP).read_bytes(), self.source)
            self.assertFalse(target.with_name(target.name + ".idle-new").exists())

    def test_current_requires_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / idle.TARGET
            target.parent.mkdir()
            target.write_bytes(idle.patch_bytes(self.source))
            with self.assertRaisesRegex(ValueError, "backup missing"):
                idle.update(root, check_only=True)

    def test_unprepared_image_is_not_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / idle.TARGET
            target.parent.mkdir()
            target.write_bytes(self.source)
            (root / "NVRAM").mkdir()
            (root / "NVRAM/m90_setup_stage.txt").write_text("stage=installing")
            with self.assertRaisesRegex(ValueError, "ready stage"):
                idle.update(root)
            self.assertEqual(target.read_bytes(), self.source)
            self.assertFalse((root / idle.BACKUP).exists())

    def test_original_and_hardlink_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.img"
            working = Path(directory) / "working image.img"
            original.write_bytes(b"original")
            os.link(original, working)
            for image in (original, working):
                with self.assertRaisesRegex(ValueError, "verschieden"):
                    image_setup.loader_idle_setup_command(
                        Selection(original_image=str(original), image=str(image)), PROJECT)
            working.unlink()
            working.write_bytes(b"working")
            with mock.patch.object(image_setup, "wsl_path", side_effect=lambda p: "/space dir/" + p.name):
                cmd = image_setup.loader_idle_setup_command(
                    Selection(original_image=str(original), image=str(working)), PROJECT)
            self.assertEqual(cmd[-3:], ["/space dir/stage_loader_idle.sh", "/space dir/original.img", "/space dir/working image.img"])


@unittest.skipIf(Uc is None, "Unicorn required for offline x86 execution")
class LoaderMachineCodeTests(unittest.TestCase):
    def machine(self, now):
        cpu = Uc(UC_ARCH_X86, UC_MODE_32)
        cpu.mem_map(0x400000, 0x100000)
        cpu.mem_map(0x2000000, 0x10000)
        for offset, _, code in idle.replacements():
            cpu.mem_write(0x400000 + offset, code)
        cpu.mem_write(0x4290A8, struct.pack("<I", 0x480000))
        cpu.mem_write(0x4291F0, struct.pack("<I", 0x480100))
        cpu.mem_write(0x480000, b"\xc3")
        cpu.mem_write(0x480100, b"\xc2\x04\x00")
        registers = [UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                     UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP]
        for index, register in enumerate(registers):
            cpu.reg_write(register, 0x1100 + index)
        cpu.reg_write(UC_X86_REG_ESP, 0x2008000)
        cpu.reg_write(UC_X86_REG_EFLAGS, 0x246)
        cpu.mem_write(0x2008000, bytes(range(128)))
        calls = []

        def hooks(uc, address, size, user):
            if address not in (0x480000, 0x480100):
                return
            calls.append(address)
            if address == 0x480100:
                argument = struct.unpack("<I", uc.mem_read(uc.reg_read(UC_X86_REG_ESP) + 4, 4))[0]
                self.assertEqual(argument, 200)
            uc.reg_write(UC_X86_REG_EAX, now if address == 0x480000 else 0xFEFE)
            uc.reg_write(UC_X86_REG_ECX, 0xAAAA)
            uc.reg_write(UC_X86_REG_EDX, 0xBBBB)
            uc.reg_write(UC_X86_REG_EFLAGS, 0x293 if address == 0x480000 else 0x202)
        cpu.hook_add(UC_HOOK_CODE, hooks)
        return cpu, registers, calls

    def test_init_keeps_caller_saved_register_slots_and_flags(self):
        cpu, regs, calls = self.machine(0xABCDEF00)
        before = {r: cpu.reg_read(r) for r in regs}
        saved = bytes(cpu.mem_read(0x2008000, 12))
        cpu.mem_write(0x477E90, struct.pack("<I", 7))
        cpu.emu_start(0x404CE6, 0x404CEF, count=100)
        self.assertEqual(cpu.reg_read(UC_X86_REG_EAX), 7)
        for reg in regs[1:]:
            self.assertEqual(cpu.reg_read(reg), before[reg])
        self.assertEqual(cpu.reg_read(UC_X86_REG_ESP), 0x2008000)
        self.assertEqual(cpu.reg_read(UC_X86_REG_EFLAGS), 0x246)
        self.assertEqual(bytes(cpu.mem_read(0x2008000, 12)), saved)
        self.assertEqual(struct.unpack("<I", cpu.mem_read(0x2008078, 4))[0], 0xABCDEF00)
        self.assertEqual(struct.unpack("<I", cpu.mem_read(0x2008020, 4))[0], before[UC_X86_REG_EAX])
        self.assertEqual(calls, [0x480000])

    def test_timeout_boundary_rollover_and_register_stack_preservation(self):
        for start, elapsed in ((0, 0), (10, 4999), (10, 5000), (10, 5001),
                               (0xFFFFF800, 4999), (0xFFFFF800, 5000)):
            with self.subTest(start=start, elapsed=elapsed):
                cpu, regs, calls = self.machine((start + elapsed) & 0xFFFFFFFF)
                before = {r: cpu.reg_read(r) for r in regs}
                cpu.mem_write(0x2008078, struct.pack("<I", start))
                destination = 0x404E9C if elapsed >= 5000 else 0x404D86
                cpu.emu_start(0x404D7B, destination, count=100)
                self.assertEqual(cpu.reg_read(UC_X86_REG_EAX), 0xFEFE)
                self.assertEqual(cpu.reg_read(UC_X86_REG_ECX), 0xAAAA)
                self.assertEqual(cpu.reg_read(UC_X86_REG_EDX), 0xBBBB)
                for reg in regs:
                    if reg not in (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX):
                        self.assertEqual(cpu.reg_read(reg), before[reg])
                self.assertEqual(cpu.reg_read(UC_X86_REG_ESP), 0x2008000)
                self.assertEqual(cpu.reg_read(UC_X86_REG_EFLAGS), 0x202)
                self.assertEqual(calls, [0x480100, 0x480000])


if __name__ == "__main__":
    unittest.main()
