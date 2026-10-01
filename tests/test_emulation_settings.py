"""Settings persistence, real Tk widgets and full start plans, without VMs."""
from dataclasses import replace
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from portable_launcher_model import EMULATION_FIELDS, Selection, launch_command, validate_emulation
import emulator_launcher as ui
import m68k_database_bridge as bridge


class EmulationSettingsTests(unittest.TestCase):
    def test_old_settings_gain_defaults_and_unknown_fields_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"image": "owner.img", "swap_displays": True, "future": 123}))
            value = Selection.from_json(path)
            self.assertEqual(value.guest_ram_mib, 2048)
            self.assertEqual(value.db_icount_shift, 6)
            self.assertTrue(value.safe_tb)
            self.assertTrue(value.swap_displays)
            self.assertEqual(value.image, "owner.img")

    def test_all_options_round_trip(self):
        selection = Selection(image="owner.img", guest_ram_mib=1024, guest_vcpus=2,
            acceleration="tcg", qxl_vram_mib=128, usb_tablet=True, swap_displays=True,
            db_icount_shift=5, safe_tb=False, db_timer_interval=0.01, duart_x1_hz=4000000,
            db_connect_timeout=240.0, database_date="2012-06-03T11:12:13", door_open=True,
            trace_diagnostics=True, show_live_log=True, show_control_window=False, sound_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            selection.save(path)
            self.assertEqual(Selection.from_json(path), selection)
        result = subprocess.run([*launch_command(selection, ROOT), "-DryRun"],
                                capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        visible = plan["runtime"]["visible_qemu"]
        self.assertEqual(visible["guest_ram_mib"], 1024)
        self.assertEqual(visible["guest_vcpus"], 2)
        self.assertEqual(visible["acceleration"], "tcg")
        self.assertEqual(visible["qxl_vram_mib"], 128)
        self.assertIn("-accel tcg", visible["arguments"])
        self.assertIn("-smp 2 -m 1024", visible["arguments"])
        self.assertEqual(visible["arguments"].count("vgamem_mb=128"), 2)
        self.assertIn("-device usb-tablet", visible["arguments"])
        self.assertEqual(visible["audio_card"], "AC97")
        self.assertEqual(visible["audio_backend"], "none")
        self.assertTrue(visible["audio_muted"])
        self.assertIn("-device AC97,audiodev=audio0", visible["arguments"])
        self.assertTrue(visible["swap_displays"])
        runtime = plan["runtime"]["database_bridge"]
        self.assertEqual(runtime["m68k_tcg_mode"], "translation-block fast mode")
        for option, expected in (("--timer-interval", "0.01"), ("--icount-shift", "5"),
                ("--duart-x1-hz", "4000000"), ("--connect-timeout", "240"),
                ("--rtc-date", "2012-06-03T11:12:13")):
            self.assertEqual(runtime["arguments"][runtime["arguments"].index(option) + 1], expected)
        self.assertIn("--door-open", runtime["arguments"])
        self.assertIn("--trace-diagnostics", runtime["arguments"])
        self.assertTrue(plan["runtime"]["event_window"]["visible"])
        self.assertFalse(plan["runtime"]["control_window"]["visible"])
        self.assertEqual(plan["virtual_programming"]["date"], selection.database_date)
        source = (ROOT / "program-and-start-emulator.ps1").read_text()
        calls = [line for line in source.splitlines() if "& $runtimeLauncher" in line]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all("@runtimeOptions" in line for line in calls))

    def test_invalid_options_rejected_before_launch_or_save(self):
        for key, bad in (("guest_ram_mib", 100), ("guest_vcpus", 0), ("acceleration", "other"),
                ("qxl_vram_mib", 65), ("safe_tb", "false"), ("db_icount_shift", 0),
                ("duart_x1_hz", 0), ("db_timer_interval", float("nan")),
                ("db_connect_timeout", float("inf")), ("database_date", "2012-02-31T00:00:00"),
                ("database_date", "2012-02-01T22:14:00+01:00"), ("guest_ram_mib", 10**500)):
            with self.subTest(key=key):
                selection = replace(Selection(), **{key: bad})
                self.assertTrue(validate_emulation(selection))
                with self.assertRaises(ValueError):
                    launch_command(selection, ROOT)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "settings.json"
                    with self.assertRaises(ValueError):
                        selection.save(path)
                    self.assertFalse(path.exists())
        self.assertTrue(validate_emulation(replace(Selection(), duart_x1_hz=10000000)))
        self.assertEqual(validate_emulation(replace(Selection(), duart_x1_hz=10000000, db_timer_interval=0.005)), [])

    def test_selected_time_used_in_both_initvideo_paths(self):
        frame = bytes.fromhex("01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
                             "01 00 00 00 00 05 75 08 19 2D 2D 55 13 11 00 00 00 00 01 58 04")
        when = dt.datetime(2012, 6, 3, 11, 12, 13)
        expected = bytes.fromhex("DC 07 06 03 0B 0C")
        self.assertEqual(bridge.complete_initvideo_board_profile(frame, when)[24:30], expected)
        forwarder = bridge.InitvideoClockForwarder(lambda: when)
        data = b"".join(forwarder.feed(bytes([value]))[0] for value in frame)
        self.assertEqual(data[24:30], expected)
        self.assertEqual(data[:24], frame[:24])
        self.assertEqual(data[30:], frame[30:])


class SettingsWidgetTests(unittest.TestCase):
    def setUp(self):
        # No executable discovery, appdata writes, QEMU or image access.
        self.patchers = [patch.object(ui.Selection, "from_json", return_value=Selection(image="owner.img")),
                         patch.object(ui, "suggested_selection", side_effect=lambda value: value)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.app = ui.Launcher()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    def test_tab_fields_parse_save_and_reset_without_changing_paths(self):
        def widgets(root):
            yield root
            for child in root.winfo_children():
                yield from widgets(child)
        notebooks = [item for item in widgets(self.app) if isinstance(item, ttk.Notebook)]
        self.assertEqual(len(notebooks), 1)
        self.assertEqual([notebooks[0].tab(tab, "text") for tab in notebooks[0].tabs()],
                         ["Einrichtung und Start", "Emulationseinstellungen"])
        self.assertEqual(set(self.app.emulation_variables), {item[0] for item in EMULATION_FIELDS})
        self.app.emulation_variables["guest_ram_mib"].set("1024")
        self.app.emulation_variables["safe_tb"].set(False)
        self.assertEqual(self.app._selection().guest_ram_mib, 1024)
        self.assertFalse(self.app._selection().safe_tb)
        with tempfile.TemporaryDirectory() as directory, patch.object(ui, "SETTINGS", Path(directory) / "settings.json"):
            self.app._save_options()
            self.assertEqual(json.loads(ui.SETTINGS.read_text())["guest_ram_mib"], 1024)
        with patch.object(ui.messagebox, "askyesno", return_value=True):
            self.app._reset_options()
        self.assertEqual(self.app._selection(), Selection(image="owner.img"))

    def test_invalid_text_gets_dialog_without_worker_or_launch(self):
        self.app.emulation_variables["guest_ram_mib"].set("no number")
        with patch.object(ui.messagebox, "showerror") as error, patch.object(ui.threading, "Thread") as thread:
            self.app._check()
        error.assert_called_once()
        thread.assert_not_called()
        self.assertFalse(self.app.checking)


if __name__ == "__main__":
    unittest.main()
