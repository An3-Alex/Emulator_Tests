"""Offline GPU routing, bundle and rollback checks; no QEMU process launched."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import struct
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qemu3dfx_image as image_module
import qemu3dfx_package as package
from cabinet_control_panel import video_touch_point


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SdlTouchTests(unittest.TestCase):
    def test_sdl_maps_only_the_lower_window_and_has_no_gtk_header(self):
        self.assertEqual(video_touch_point((400, 300), (800, 600), (800, 600),
                                           "sdl", "QEMU (M90-3dfx-0)"), (400, 300))
        self.assertEqual(video_touch_point((0, 0), (800, 600), (800, 600),
                                           "sdl", "QEMU (M90-3dfx-0) [Paused]"), (0, 0))
        for title in ("QEMU", "QEMU (M90-3dfx-1)", "QEMU (M90-3dfx-01)", "another app"):
            self.assertIsNone(video_touch_point((400, 300), (800, 600), (800, 600), "sdl", title))

    def test_sdl_letterboxing_is_centered_and_not_touchable(self):
        title = "QEMU (M90-3dfx-0)"
        self.assertIsNone(video_touch_point((100, 30), (800, 800), (800, 600), "sdl", title))
        self.assertEqual(video_touch_point((400, 100), (800, 800), (800, 600), "sdl", title), (400, 0))
        self.assertEqual(video_touch_point((400, 400), (800, 800), (800, 600), "sdl", title), (400, 300))

    def test_gtk_retains_the_existing_video_geometry(self):
        self.assertIsNone(video_touch_point((10, 10), (800, 645), (800, 600)))
        self.assertEqual(video_touch_point((400, 345), (800, 645), (800, 600)), (400, 300))
        self.assertIsNone(video_touch_point((0, 0), (0, 0), (800, 600)))


class ImageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "image"
        self.bundle = Path(self.temp.name) / "bundle"
        self.root.mkdir(); self.bundle.mkdir()
        self.original = {}
        def old(name, value):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            self.original[name] = value
        for directory in ("NVRAM", "WorkDir"):
            old(f"{directory}/d3d9.dll", b"standard-proxy")
            old(f"{directory}/game.exe", b"game")
            old(f"{directory}/swiftshader_d3d9.dll", b"swift")
        old("WINDOWS/system32/Cgos.dll", b"cgos")
        old(image_module.SYSTEM, b"original-registry")
        old("NVRAM/m90_sram.bin", b"persistent-sram-do-not-change")
        old("NVRAM/m90_setup_stage.txt", b"stage=ready\n")
        (self.root / "WINDOWS/system32/drivers").mkdir()
        files = {}
        for name in package.GUEST_FILES:
            path = self.bundle / "guest" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("new-" + name).encode())
            files[f"guest/{name}"] = digest(path.read_bytes())
        (self.bundle / "manifest.json").write_text("fixture")
        self.manifest = {"backend": "qemu3dfx-dual", "files": files}
        self.patchers = [
            patch.object(image_module, "PROXY_HASH", digest(b"standard-proxy")),
            patch.object(image_module, "GAME_HASH", digest(b"game")),
            patch.object(image_module, "SWIFTSHADER_HASH", digest(b"swift")),
            patch.object(image_module, "CGOS_HASH", digest(b"cgos")),
            patch.object(image_module, "validate", return_value=self.manifest),
            patch.object(image_module, "verify_driver"),
            patch.object(image_module, "register_driver",
                         side_effect=lambda source, target: target.write_bytes(source.read_bytes() + b"-MAPMEM")),
        ]
        for patcher in self.patchers:
            patcher.start(); self.addCleanup(patcher.stop)

    def assert_original(self):
        for name, value in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), value, name)

    def test_check_is_read_only_and_install_restore_preserve_sram(self):
        self.assertEqual(image_module.install(self.root, self.bundle, check_only=True), "QEMU3DFX_IMAGE_REQUIRED")
        self.assertFalse((self.root / image_module.BACKUP).exists())
        self.assert_original()
        self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_INSTALLED")
        self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_CURRENT")
        self.assertEqual((self.root / "NVRAM/m90_sram.bin").read_bytes(), self.original["NVRAM/m90_sram.bin"])
        self.assertEqual(image_module.restore(self.root), "QEMU3DFX_IMAGE_RESTORED")
        self.assert_original()
        self.assertFalse((self.root / image_module.DRIVER).exists())
        self.assertFalse((self.root / "WorkDir/wined3d.dll").exists())

    def test_unknown_existing_dll_is_never_overwritten(self):
        (self.root / "WorkDir/opengl32.dll").write_bytes(b"owned-by-somebody-else")
        with self.assertRaisesRegex(ValueError, "already exists"):
            image_module.install(self.root, self.bundle)
        self.assertFalse((self.root / image_module.BACKUP).exists())
        self.assert_original()

    def test_host_only_bundle_update_preserves_all_guest_files(self):
        image_module.install(self.root, self.bundle)
        marker = self.root / image_module.MARKER
        before_marker = marker.read_bytes()
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file() and path != marker}
        (self.bundle / "manifest.json").write_text("new-host-same-guest-dlls")
        self.assertEqual(image_module.install(self.root, self.bundle, check_only=True),
                         "QEMU3DFX_IMAGE_CURRENT")
        self.assertEqual(marker.read_bytes(), before_marker)
        self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_CURRENT")
        self.assertEqual(json.loads(marker.read_text())["bundle_sha256"],
                         digest((self.bundle / "manifest.json").read_bytes()))
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file() and path != marker}
        self.assertEqual(after, before)

    def test_copy_failure_rolls_back_already_replaced_files(self):
        real = image_module.durable_copy
        failed = False
        def copy(source, target):
            nonlocal failed
            if target.name == "wined3d.dll.gpu-new" and not failed:
                failed = True
                raise OSError("injected disk failure")
            return real(source, target)
        with patch.object(image_module, "durable_copy", side_effect=copy):
            with self.assertRaisesRegex(OSError, "injected"):
                image_module.install(self.root, self.bundle)
        self.assert_original()
        self.assertFalse((self.root / "NVRAM/wined3d_d3d9.dll").exists())
        self.assertFalse(list(self.root.rglob("*.gpu-new")))

    def test_restore_never_resets_a_registry_changed_after_installation(self):
        image_module.install(self.root, self.bundle)
        (self.root / image_module.SYSTEM).write_bytes(b"guest-changed-registry")
        with self.assertRaisesRegex(ValueError, "Unrecognized file"):
            image_module.restore(self.root)
        self.assertEqual((self.root / image_module.SYSTEM).read_bytes(), b"guest-changed-registry")
        self.assertEqual((self.root / "WorkDir/d3d9.dll").read_bytes(), b"new-d3d9.dll")

    def test_normal_xp_registry_writes_do_not_block_next_start(self):
        image_module.install(self.root, self.bundle)
        (self.root / image_module.SYSTEM).write_bytes(b"normal-xp-write")
        self.assertEqual(image_module.install(self.root, self.bundle, check_only=True), "QEMU3DFX_IMAGE_CURRENT")
        image_module.verify_driver.assert_called_once_with((self.root / image_module.SYSTEM).resolve(), allow_legacy_path=True)

    def test_legacy_driver_check_is_read_only_and_repair_preserves_backup(self):
        image_module.install(self.root, self.bundle)
        hive = self.root / image_module.SYSTEM
        before = hive.read_bytes()
        original_backup = (self.root / image_module.BACKUP / "SYSTEM").read_bytes()
        with patch.object(image_module, "verify_driver", return_value=True), patch.object(
                image_module, "repair_driver_path", side_effect=lambda source, target: target.write_bytes(source.read_bytes()+b"-path-fixed")) as repair:
            self.assertEqual(image_module.install(self.root, self.bundle, check_only=True), "QEMU3DFX_IMAGE_CURRENT")
            repair.assert_not_called()
            self.assertEqual(hive.read_bytes(), before)
            image_module.install(self.root, self.bundle)
        self.assertEqual(hive.read_bytes(), before+b"-path-fixed")
        self.assertEqual((self.root / image_module.BACKUP / "SYSTEM").read_bytes(), original_backup)
        self.assertEqual((self.root / image_module.BACKUP / "SYSTEM.before-driver-path-fix").read_bytes(), before)

    def test_changed_driver_settings_block_next_start(self):
        image_module.install(self.root, self.bundle)
        with patch.object(image_module, "verify_driver", side_effect=ValueError("MAPMEM changed")):
            with self.assertRaisesRegex(ValueError, "MAPMEM changed"):
                image_module.install(self.root, self.bundle, check_only=True)

    def prepare_wrapper_update(self):
        image_module.install(self.root, self.bundle)
        old = (self.bundle / "guest/opengl32.dll").read_bytes()
        (self.bundle / "guest/opengl32.dll").write_bytes(b"fixed-opengl")
        self.manifest["files"]["guest/opengl32.dll"] = digest(b"fixed-opengl")
        return old

    def test_known_wrapper_update_preserves_registry_sram_and_original_backup(self):
        old = self.prepare_wrapper_update()
        hive = self.root / image_module.SYSTEM
        hive.write_bytes(b"live-xp-settings")
        old_marker = (self.root / image_module.MARKER).read_bytes()
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(old)}):
            self.assertEqual(image_module.install(self.root, self.bundle, check_only=True), "QEMU3DFX_IMAGE_UPDATE_REQUIRED")
            self.assertEqual((self.root / image_module.MARKER).read_bytes(), old_marker)
            self.assertFalse((self.root / image_module.BACKUP / "before-graphics-runtime-fix").exists())
            self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_UPDATED")
        for directory in ("NVRAM", "WorkDir"):
            self.assertEqual((self.root / directory / "opengl32.dll").read_bytes(), b"fixed-opengl")
            self.assertEqual((self.root / image_module.BACKUP / "before-graphics-runtime-fix" / (directory + ".opengl32.dll")).read_bytes(), old)
        self.assertEqual(hive.read_bytes(), b"live-xp-settings")
        self.assertEqual((self.root / image_module.BACKUP / "SYSTEM").read_bytes(), b"original-registry")
        self.assertEqual((self.root / "NVRAM/m90_sram.bin").read_bytes(), self.original["NVRAM/m90_sram.bin"])
        self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_CURRENT")

    def test_unknown_old_wrapper_update_is_refused(self):
        self.prepare_wrapper_update()
        with self.assertRaisesRegex(ValueError, "Another guest GPU bundle"):
            image_module.install(self.root, self.bundle)

    def test_second_known_update_keeps_each_completed_backup(self):
        first = self.prepare_wrapper_update()
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(first)}):
            image_module.install(self.root, self.bundle)
        marker = self.root / image_module.MARKER
        generation = digest(marker.read_bytes())[:16]
        (self.bundle / "guest/opengl32.dll").write_bytes(b"second-fix")
        self.manifest["files"]["guest/opengl32.dll"] = digest(b"second-fix")
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(b"fixed-opengl")}):
            self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_UPDATED")
        recovery = self.root / image_module.BACKUP
        self.assertEqual((recovery / "before-graphics-runtime-fix/NVRAM.opengl32.dll").read_bytes(), first)
        self.assertEqual((recovery / ("before-graphics-runtime-fix-" + generation) / "NVRAM.opengl32.dll").read_bytes(), b"fixed-opengl")
        self.assertEqual((self.root / "NVRAM/opengl32.dll").read_bytes(), b"second-fix")

    def test_update_rejects_changed_source_after_manifest_validation(self):
        old = self.prepare_wrapper_update()
        (self.bundle / "guest/opengl32.dll").write_bytes(b"changed-after-validation")
        before = (self.root / image_module.MARKER).read_bytes()
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(old)}):
            with self.assertRaisesRegex(ValueError, "Unrecognized file"):
                image_module.install(self.root, self.bundle)
        self.assertEqual((self.root / image_module.MARKER).read_bytes(), before)
        for directory in ("NVRAM", "WorkDir"):
            self.assertEqual((self.root / directory / "opengl32.dll").read_bytes(), old)

    def test_known_wine_and_wrapper_are_updated_together(self):
        image_module.install(self.root, self.bundle)
        names = ("opengl32.dll", "wined3d.dll", "wined3d_d3d9.dll")
        old = {name: (self.bundle / "guest" / name).read_bytes() for name in names}
        for name in names:
            (self.bundle / "guest" / name).write_bytes(("fixed-" + name).encode())
            self.manifest["files"]["guest/" + name] = digest(("fixed-" + name).encode())
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(old["opengl32.dll"])}), patch.object(
                image_module, "LEGACY_WINE_HASHES", {name: {digest(old[name])} for name in names[1:]}):
            self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_UPDATED")
        for directory in ("NVRAM", "WorkDir"):
            for name in names:
                self.assertEqual((self.root / directory / name).read_bytes(), ("fixed-" + name).encode())
                self.assertEqual((self.root / image_module.BACKUP / "before-graphics-runtime-fix" / (directory + "." + name)).read_bytes(), old[name])
        self.assertEqual(image_module.install(self.root, self.bundle), "QEMU3DFX_IMAGE_CURRENT")

    def test_wrapper_update_copy_failure_rolls_back_and_retains_marker(self):
        old = self.prepare_wrapper_update()
        before = (self.root / image_module.MARKER).read_bytes()
        real = image_module.durable_copy
        def copy(source, target):
            if target.name == "opengl32.dll.gpu-new" and target.parent.name == "WorkDir":
                raise OSError("injected wrapper update failure")
            return real(source, target)
        with patch.object(image_module, "LEGACY_OPENGL_HASHES", {digest(old)}), patch.object(
                image_module, "durable_copy", side_effect=copy):
            with self.assertRaisesRegex(OSError, "injected wrapper"):
                image_module.install(self.root, self.bundle)
        for directory in ("NVRAM", "WorkDir"):
            self.assertEqual((self.root / directory / "opengl32.dll").read_bytes(), old)
        self.assertEqual((self.root / image_module.MARKER).read_bytes(), before)
        self.assertFalse(list(self.root.rglob("*.gpu-new")))


class DriverPathTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "Type": (4, struct.pack("<I", 1)),
            "Start": (4, struct.pack("<I", 2)),
            "ErrorControl": (4, struct.pack("<I", 1)),
            "ImagePath": (2, (image_module.DRIVER_PATH+"\0").encode("utf-16le")),
            "OtherValue": (4, struct.pack("<I", 42)),
        }
        def child(node, name):
            return name if name in ("Services", "MAPMEM") else None
        self.hive = SimpleNamespace(
            root=lambda: "root", node_children=lambda node: ["ControlSet001"],
            node_name=lambda node: node, node_get_child=child,
            node_get_value=lambda node, name: self.values[name],
            value_value=lambda value: value,
            node_set_value=lambda node, entry: self.values.__setitem__(entry["key"], (entry["t"],entry["value"])),
            commit=lambda destination: Path(destination).write_bytes(b"committed"),
        )
        self.mock = patch.dict(sys.modules, {"hivex": SimpleNamespace(Hivex=lambda *args, **kwargs: self.hive)})
        self.mock.start(); self.addCleanup(self.mock.stop)

    def test_native_systemroot_path_is_accepted(self):
        self.assertFalse(image_module.verify_driver(Path("SYSTEM")))

    def test_legacy_path_only_accepted_for_owned_migration(self):
        self.values["ImagePath"] = (2,(image_module.LEGACY_DRIVER_PATH+"\0").encode("utf-16le"))
        with self.assertRaisesRegex(ValueError,"path changed"):
            image_module.verify_driver(Path("SYSTEM"))
        self.assertTrue(image_module.verify_driver(Path("SYSTEM"),allow_legacy_path=True))

    def test_foreign_driver_path_is_never_repaired(self):
        self.values["ImagePath"] = (2,"C:\\other.sys\0".encode("utf-16le"))
        with self.assertRaisesRegex(ValueError,"path changed"):
            image_module.verify_driver(Path("SYSTEM"),allow_legacy_path=True)

    def test_legacy_repair_preserves_other_values(self):
        self.values["ImagePath"] = (2,(image_module.LEGACY_DRIVER_PATH+"\0").encode("utf-16le"))
        before=dict(self.values)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"SYSTEM.new"
            image_module.repair_driver_path(Path("SYSTEM"),destination)
            self.assertEqual(destination.read_bytes(),b"committed")
        self.assertFalse(image_module.verify_driver(Path("SYSTEM")))
        for key,value in before.items():
            if key!="ImagePath": self.assertEqual(self.values[key],value)


class PackageValidationTests(unittest.TestCase):
    def test_modern_package_uses_host_bios_not_the_last_guest_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout, wine, host, runtime, destination = [root / n for n in ("gpu", "wine", "host-source", "ucrt/bin", "bundle")]
            files = [checkout / "wrappers/mesa/build/opengl32.dll",
                checkout / "wrappers/3dfx/build/fxptl.sys", wine / "output/1.8.7/d3d9.dll",
                wine / "output/1.8.7/wined3d.dll", wine / "wine-1.8.7/COPYING.LIB",
                host / "build-m90/qemu-system-x86_64.exe", host / "COPYING", root / "proxy.dll",
                *(host / "pc-bios" / n for n in package.MODERN_BIOS_FILES)]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.name.encode())
            (host / "VERSION").write_text(package.HOST_VERSION)
            (host / "m90-gpu-port.json").write_text(json.dumps(dict(
                host_revision=package.HOST_REVISION, gpu_revision=package.REVISION, files={})))
            def run(args, **kwargs):
                if args[0] == "git":
                    value = package.REVISION if args[2] == str(checkout) else package.WINE_REVISION
                elif args[-1] == "--version":
                    value = "QEMU emulator version 11.1.0\n  featuring qemu-3dfx@920661f-build\n"
                else:
                    value = ""
                return subprocess.CompletedProcess(args, 0, stdout=value)
            with patch.object(package.subprocess, "run", side_effect=run), \
                 patch.object(package.subprocess, "check_output", return_value=package.HOST_REVISION), \
                 patch.object(package, "collect_dependencies", return_value=[]), \
                 patch.object(package, "pe_imports", return_value=[]), \
                 patch.object(package, "require_hash"), patch.object(package, "validate"):
                package.build(checkout, runtime, wine, root / "proxy.dll", destination, host)
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(manifest["host_version"], package.HOST_VERSION)
            self.assertEqual((destination / "host/pc-bios/bios.bin").read_bytes(), b"bios.bin")
            self.assertNotIn("host/pc-bios/linuxboot.bin", manifest["files"])
            self.assertEqual((destination / "guest/fxptl.sys").read_bytes(), b"fxptl.sys")

    def test_modern_host_metadata_is_pinned_and_legacy_bundles_still_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {name: "0" * 64 for name in [
                "host/qemu-system-x86_64.exe", "licenses/QEMU-COPYING",
                *(f"host/pc-bios/{n}" for n in package.BIOS_FILES),
                *(f"guest/{n}" for n in package.GUEST_FILES)]}
            files["guest/fxptl.sys"] = package.DRIVER_HASH
            manifest = dict(version=1, backend="qemu3dfx-hybrid", qemu_revision=package.REVISION,
                wine_revision=package.WINE_REVISION, gpu_adapter=0, cpu_adapter=1,
                memory_layout="m90-dual-qxl-v1", files=files)
            path = root / "manifest.json"
            with patch.object(package, "require_hash"):
                path.write_text(json.dumps(manifest))
                package.validate(root)
                manifest.update(host_revision=package.HOST_REVISION, host_version=package.HOST_VERSION)
                # QEMU 11 uses the DMA ROMs instead of the retired non-DMA ones.
                for name in ("linuxboot.bin", "multiboot.bin"):
                    del files[f"host/pc-bios/{name}"]
                path.write_text(json.dumps(manifest))
                package.validate(root)
                for change in ({"host_revision": "main"}, {"host_version": "11.1.50"}, {"host_revision": None}):
                    path.write_text(json.dumps({**manifest, **change}))
                    with self.assertRaisesRegex(ValueError, "Unknown modern"):
                        package.validate(root)
                del manifest["host_version"]
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "Unknown modern"):
                    package.validate(root)

    def test_gpu_buffers_require_ram_below_the_reserved_layout(self):
        from portable_launcher_model import Selection, validate_emulation
        self.assertFalse(validate_emulation(Selection(graphics_backend="qemu3dfx", swap_displays=True, guest_ram_mib=2048)))
        self.assertTrue(any("2048" in issue for issue in validate_emulation(
            Selection(graphics_backend="qemu3dfx", swap_displays=True, guest_ram_mib=3072))))
        self.assertFalse(validate_emulation(Selection(guest_ram_mib=3072)))
    def test_gpu_selection_uses_contained_host_and_primary_display(self):
        from portable_launcher_model import Selection, graphics_selection
        root = Path("project")
        selection = Selection(graphics_backend="qemu3dfx", qemu_x86="stock.exe", swap_displays=False)
        with patch.object(package, "validate") as validate:
            resolved = graphics_selection(selection, root)
        self.assertEqual(resolved.qemu_x86, str(root / "build/qemu3dfx-runtime/host/qemu-system-x86_64.exe"))
        self.assertTrue(resolved.swap_displays)
        validate.assert_called_once_with(root / "build/qemu3dfx-runtime")
        self.assertEqual(selection.qemu_x86, "stock.exe")

    def test_gpu_setup_uses_sdl_bios_and_no_sound_card(self):
        from qxl_setup_runner import qemu_command
        command = qemu_command(Path("gpu/host/qemu.exe"), Path("work.img"), 4554, 4446,
                               gpu_runtime=True, swap_displays=True)
        self.assertEqual(command[command.index("-display") + 1], "sdl,gl=off")
        self.assertEqual(command[command.index("-accel") + 1], "whpx,kernel-irqchip=off")
        self.assertIn(str(Path("gpu/host/pc-bios")), command)
        self.assertIn("qxl-vga,id=lower,revision=2,vgamem_mb=64,xres=640,yres=480", command)
        self.assertFalse(any("ac97" in arg.lower() for arg in command))

    def test_stock_qemu_does_not_trigger_a_manifest_read_in_program_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Program Files"
            qemu = root / "qemu/qemu-system-x86_64.exe"
            qemu.parent.mkdir(parents=True)
            qemu.write_bytes(b"stock-qemu")
            with patch.object(package, "validate") as validate:
                with self.assertRaisesRegex(ValueError, "normale QEMU-Installation"):
                    package.verify_launch(root / "image.img", qemu)
                validate.assert_not_called()
            self.assertFalse((root / "manifest.json").exists())

    def test_missing_gpu_manifest_has_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "aktuelle Starter-EXE"):
                package.validate(root)

    def test_missing_gpu_image_receipt_is_not_a_raw_file_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            qemu = root / "gpu/host/qemu-system-x86_64.exe"
            qemu.parent.mkdir(parents=True)
            qemu.write_bytes(b"gpu-qemu")
            image = root / "work.img"
            image.write_bytes(b"image")
            with patch.object(package, "validate"):
                with self.assertRaisesRegex(ValueError, "Frisches Image einrichten"):
                    package.verify_launch(image, qemu)

    def test_manifest_rejects_traversal_before_reading_external_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {name: "0" * 64 for name in
                     ["host/qemu-system-x86_64.exe", "licenses/QEMU-COPYING",
                      *(f"host/pc-bios/{n}" for n in package.BIOS_FILES),
                      *(f"guest/{n}" for n in package.GUEST_FILES)]}
            files = {"../escape": "0" * 64, **files}
            (root / "manifest.json").write_text(json.dumps(dict(
                version=1, backend="qemu3dfx-hybrid", qemu_revision=package.REVISION,
                wine_revision=package.WINE_REVISION, gpu_adapter=0, cpu_adapter=1,
                memory_layout="m90-dual-qxl-v1", files=files)))
            with self.assertRaisesRegex(ValueError, "Invalid runtime file"):
                package.validate(root)

    def test_missing_dependency_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(package, "pe_imports", return_value=["missing.dll"]):
                with self.assertRaisesRegex(ValueError, "Missing host dependency"):
                    package.collect_dependencies(root / "qemu.exe", root, root / "objdump", root)


if __name__ == "__main__":
    unittest.main()
