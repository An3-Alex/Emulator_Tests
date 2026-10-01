import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from duart_timer import DuartTimerConfig, DuartTimerBudget


class DuartTimerTests(unittest.TestCase):
    def test_cc4_preset_and_alternate_half_rate(self):
        normal = DuartTimerConfig.from_registers(0xF0, 0, 0x74, 64)
        alternate = DuartTimerConfig.from_registers(0xF0, 0, 0x3A, 65)
        self.assertAlmostEqual(normal.period_seconds, 0.0010069444444444445)
        self.assertAlmostEqual(alternate.period_seconds * 2, normal.period_seconds)

    def test_fractional_ticks_do_not_drift(self):
        budget = DuartTimerBudget(DuartTimerConfig())
        ticks = sum(budget.take_slice(0.05) for _ in range(2000))
        self.assertEqual(ticks, int(100 / budget.config.period_seconds))
        self.assertLess(budget.fraction, 1)

    def test_alternate_irq_count_keeps_same_service_time(self):
        ordinary = DuartTimerBudget(DuartTimerConfig())
        alternate = DuartTimerBudget(DuartTimerConfig(preset=0x3A, vector=65))
        a = sum(ordinary.take_slice(0.05) for _ in range(100))
        b = sum(alternate.take_slice(0.05) for _ in range(100))
        self.assertLessEqual(abs(a - b // 2), 1)

    def test_undefined_preset_is_rejected_and_slow_timer_can_yield_zero(self):
        for preset in (0, 1):
            with self.assertRaises(ValueError):
                DuartTimerConfig(preset=preset)
        self.assertEqual(DuartTimerBudget(DuartTimerConfig(preset=65535)).take_slice(0.05), 0)

    def test_batch_overflow_does_not_mutate_phase(self):
        budget = DuartTimerBudget(DuartTimerConfig())
        with self.assertRaisesRegex(ValueError, "too many"):
            budget.take_slice(1)
        self.assertEqual(budget.fraction, 0)

    def test_bad_hardware_config_fails_loudly(self):
        for kwargs in ({"acr": 0x60}, {"vector": 0}, {"x1_hz": 0}, {"preset": -1}):
            with self.assertRaises(ValueError):
                DuartTimerConfig(**kwargs)
        for seconds in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                DuartTimerBudget(DuartTimerConfig()).take_slice(seconds)

    def test_crystal_not_cpu_or_host_clock(self):
        config = DuartTimerConfig(x1_hz=4_000_000)
        self.assertAlmostEqual(config.period_seconds, 116 * 32 / 4_000_000)


if __name__ == "__main__":
    unittest.main()
