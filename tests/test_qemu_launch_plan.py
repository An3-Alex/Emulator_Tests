import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "test-swiftshader.ps1"
REAL_DATABASE_LAUNCHER = ROOT / "start-real-database.ps1"
PROGRAM_AND_START_LAUNCHER = ROOT / "program-and-start-emulator.ps1"


class QemuLaunchPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = shutil.which("pwsh") or shutil.which("powershell")
        if cls.powershell is None:
            raise unittest.SkipTest("PowerShell is required for launcher tests")

    def script_plan(self, script: Path, *extra_args: str) -> dict:
        result = subprocess.run(
            [
                self.powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script),
                "-DryRun",
                *extra_args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def launch_plan(self) -> dict:
        return self.script_plan(LAUNCHER)

    def test_launchers_do_not_embed_a_developer_profile(self) -> None:
        for script in (
            LAUNCHER, REAL_DATABASE_LAUNCHER, PROGRAM_AND_START_LAUNCHER,
            ROOT / "start-emulator.ps1", ROOT / "start-esp32-database.ps1",
        ):
            with self.subTest(script=script.name):
                self.assertNotIn("C:\\Users\\", script.read_text(encoding="utf-8"))

    def test_dry_run_keeps_qemu_visible_and_rebootable(self) -> None:
        plan = self.launch_plan()
        arguments = plan["arguments"]
        self.assertTrue(plan["visible"])
        self.assertTrue(plan["guest_reboots_allowed"])
        self.assertEqual(plan["display_tabs"], ["upper", "lower"])
        self.assertIn("-display gtk,show-tabs=on", arguments)
        self.assertNotIn("-no-reboot", arguments)

    def test_dry_run_does_not_throttle_the_host_qemu_process(self) -> None:
        plan = self.launch_plan()
        self.assertEqual(plan["guest_vcpus"], 1)
        self.assertEqual(plan["guest_ram_mib"], 2048)
        self.assertEqual(plan["host_priority"], "Normal")
        self.assertIsNone(plan["host_affinity_mask"])
        self.assertIn("-smp 1", plan["arguments"])
        self.assertIn("-m 2048", plan["arguments"])

    def test_swapped_outputs_reach_both_programming_branches(self) -> None:
        plan = self.script_plan(PROGRAM_AND_START_LAUNCHER, "-SwapDisplays")
        visible = plan["runtime"]["visible_qemu"]
        self.assertEqual(visible["display_tabs"], ["lower", "upper"])
        self.assertTrue(visible["swap_displays"])
        self.assertEqual(visible["cabinet_lower_device"], "lower")
        self.assertIn("qxl-vga,id=lower", visible["arguments"])
        self.assertIn("qxl,id=upper", visible["arguments"])
        self.assertNotIn("qxl-vga,id=upper", visible["arguments"])
        source = PROGRAM_AND_START_LAUNCHER.read_text(encoding="utf-8")
        calls = [line for line in source.splitlines() if "& $runtimeLauncher" in line]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all("-SwapDisplays:$SwapDisplays" in line for line in calls))

    def test_dry_run_keeps_expected_devices_and_restricted_network(self) -> None:
        arguments = self.launch_plan()["arguments"]
        self.assertIn("qxl-vga,id=upper", arguments)
        self.assertIn("qxl,id=lower", arguments)
        self.assertNotIn("-device usb-tablet", arguments)
        self.assertEqual(self.launch_plan()["guest_pointer"], "PS/2 mouse")
        self.assertIn("restrict=on", arguments)
        self.assertIn("tcp:127.0.0.1:4553,server=on,wait=off", arguments)

    def test_audio_device_stays_present_even_when_host_output_is_muted(self) -> None:
        for script in (LAUNCHER, REAL_DATABASE_LAUNCHER, PROGRAM_AND_START_LAUNCHER):
            for mute in (False, True):
                with self.subTest(script=script.name, mute=mute):
                    plan = self.script_plan(script, *(["-MuteAudio"] if mute else []))
                    visible = plan.get("visible_qemu") or plan.get("runtime", {}).get("visible_qemu") or plan
                    self.assertEqual(visible["audio_backend"], "none" if mute else "sdl")
                    self.assertEqual(visible["audio_muted"], mute)
                    self.assertFalse(visible["audio_recording"])
                    self.assertEqual(visible["audio_output_channels"], 1)
                    self.assertIn(
                        f'-audiodev {visible["audio_backend"]},id=audio0,in.voices=0,out.channels=1',
                        visible["arguments"],
                    )
                    self.assertNotIn("dsound", visible["arguments"])
                    self.assertIn("-device AC97,audiodev=audio0", visible["arguments"])

    def test_cf_image_path_with_spaces_stays_quoted_for_qemu(self) -> None:
        image = r"D:\CF Images\m90img - Kopie (2).img"
        plan = self.script_plan(LAUNCHER, "-Image", image)
        self.assertEqual(plan["image"], image)
        self.assertIn('file="D:/CF Images/m90img - Kopie (2).img",format=raw',
                      plan["arguments"])

    def test_usb_tablet_requires_explicit_experiment_flag(self) -> None:
        plan = self.script_plan(LAUNCHER, "-UsbTablet")
        self.assertIn("-usb -device usb-tablet", plan["arguments"])
        self.assertIn("USB tablet", plan["guest_pointer"])
        integrated = self.script_plan(REAL_DATABASE_LAUNCHER, "-UsbTablet")
        self.assertIn("-usb -device usb-tablet", integrated["visible_qemu"]["arguments"])

    def test_integrated_database_plan_caps_only_emulated_database(self) -> None:
        plan = self.script_plan(REAL_DATABASE_LAUNCHER)
        bridge = plan["database_bridge"]
        self.assertEqual(bridge["timer_run_seconds"], 0.05)
        self.assertEqual(bridge["host_pause_seconds"], 0.0)
        self.assertEqual(bridge["emulated_db_icount_shift"], 6)
        self.assertEqual(
            bridge["emulated_db_max_instructions_per_second"], 15_625_000
        )
        self.assertEqual(bridge["windows_priority"], "Normal")
        self.assertEqual(bridge["m68k_tcg_mode"], "single-instruction fallback")
        self.assertNotIn("--fast-tb", bridge["arguments"])
        self.assertFalse(bridge["diagnostic_watchpoints"])
        self.assertEqual(bridge["door_switch"], "closed")
        self.assertNotIn("--door-open", bridge["arguments"])
        self.assertEqual(bridge["arguments"][-2:], ["--d3", "0xD27B7159"])
        self.assertIn("--timer-interval", bridge["arguments"])
        self.assertNotIn("--timer-sleep", bridge["arguments"])
        self.assertIn("0xD27B7159", bridge["arguments"])
        self.assertTrue(plan["visible_qemu"]["guest_reboots_allowed"])
        self.assertTrue(plan["event_window"]["visible"])
        self.assertEqual(plan["event_window"]["viewer"], "scripts/event_log_viewer.py")

    def test_database_timing_can_be_compared_at_half_rate(self) -> None:
        plan = self.script_plan(
            REAL_DATABASE_LAUNCHER, "-SafeTb", "-DbIcountShift", "6"
        )
        bridge = plan["database_bridge"]
        self.assertEqual(bridge["emulated_db_icount_shift"], 6)
        self.assertEqual(
            bridge["emulated_db_max_instructions_per_second"], 15_625_000
        )
        self.assertEqual(bridge["m68k_tcg_mode"], "single-instruction fallback")
        self.assertIn("--icount-shift", bridge["arguments"])
        self.assertIn("6", bridge["arguments"])

    def test_m90_admission_eeprom_can_be_selected_without_launching(self) -> None:
        image = r"C:\cards\ergoline-m90.eeprom.bin"
        plan = self.script_plan(
            REAL_DATABASE_LAUNCHER, "-AdmissionEeprom", image
        )
        arguments = plan["database_bridge"]["arguments"]
        position = arguments.index("--admission-eeprom")
        self.assertEqual(arguments[position + 1], image)

    def test_selected_qemu_and_cf_image_reach_visible_launch_plan(self) -> None:
        qemu = r"D:\Portable QEMU\qemu-system-x86_64.exe"
        image = r"D:\CF Images\M90 clean.img"
        for launcher in (REAL_DATABASE_LAUNCHER, PROGRAM_AND_START_LAUNCHER):
            plan = self.script_plan(launcher, "-Qemu", qemu, "-Image", image)
            visible = plan.get("visible_qemu") or plan["runtime"]["visible_qemu"]
            self.assertEqual(visible["qemu"], qemu)
            self.assertEqual(visible["image"], image)

    def test_selected_database_qemu_and_optional_live_window(self) -> None:
        qemu = r"D:\Portable QEMU\qemu-system-m68k.exe"
        plan = self.script_plan(
            PROGRAM_AND_START_LAUNCHER,
            "-QemuM68k", qemu, "-NoEventWindow",
        )["runtime"]
        arguments = plan["database_bridge"]["arguments"]
        self.assertEqual(arguments[arguments.index("--qemu") + 1], qemu)
        self.assertFalse(plan["event_window"]["visible"])
        self.assertTrue(plan["control_window"]["visible"])

    def test_programming_plan_pins_owner_files_before_runtime(self) -> None:
        plan = self.script_plan(PROGRAM_AND_START_LAUNCHER)
        programming = plan["virtual_programming"]
        arguments = programming["arguments"]
        self.assertEqual(programming["date"], "2012-02-01T22:14:00")
        self.assertIn(
            "B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109",
            arguments,
        )
        self.assertIn(
            "593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8",
            arguments,
        )
        self.assertIn(
            "4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4",
            arguments,
        )
        self.assertIn(
            "DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783",
            arguments,
        )
        self.assertEqual(
            plan["runtime"]["database_bridge"]["host_pause_seconds"], 0.0
        )

    def test_programming_plan_forwards_safe_database_timing(self) -> None:
        plan = self.script_plan(
            PROGRAM_AND_START_LAUNCHER, "-SafeTb", "-DbIcountShift", "6"
        )
        bridge = plan["runtime"]["database_bridge"]
        self.assertEqual(bridge["m68k_tcg_mode"], "single-instruction fallback")
        self.assertEqual(bridge["emulated_db_icount_shift"], 6)
        self.assertNotIn("--fast-tb", bridge["arguments"])


if __name__ == "__main__":
    unittest.main()
