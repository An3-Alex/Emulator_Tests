"""Offline checks for the launcher's setup-duration display."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from emulator_launcher import Launcher, format_elapsed


class LauncherDurationTests(unittest.TestCase):
    def test_elapsed_clock_handles_zero_minutes_and_hours(self) -> None:
        self.assertEqual(format_elapsed(-5), "00:00:00")
        self.assertEqual(format_elapsed(9.9), "00:00:09")
        self.assertEqual(format_elapsed(65), "00:01:05")
        self.assertEqual(format_elapsed(3661), "01:01:01")

    def test_phase_and_final_duration_are_visible(self) -> None:
        timer = Mock()
        progress = Mock()
        launcher = SimpleNamespace(
            prepare_started_at=10.0,
            prepare_phase="QXL-Gastinstallation läuft",
            prepare_timer=timer,
            prepare_progress=progress,
        )
        with patch("emulator_launcher.time.monotonic", return_value=75.0):
            Launcher._show_prepare_elapsed(launcher)
            Launcher._finish_prepare_timer(launcher, "Fertig")
        timer.set.assert_any_call(
            "Image-Einrichtung · QXL-Gastinstallation läuft · 00:01:05 vergangen"
        )
        timer.set.assert_called_with("Image-Einrichtung · Fertig nach 00:01:05")
        self.assertIsNone(launcher.prepare_started_at)
        progress.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
