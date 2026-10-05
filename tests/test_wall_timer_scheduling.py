"""Bounded timer phase includes hardware/debugger wall time without IRQ storms."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from duart_timer import (
    DuartTimerConfig, DuartWallTimer,
    MAX_WALL_TIMER_BATCH_TICKS, MAX_WALL_TIMER_PENDING_TICKS,
)


class WallTimerSchedulingTests(unittest.TestCase):
    def make_timer(self):
        timer = DuartWallTimer(DuartTimerConfig())
        timer.start(0.0)
        return timer

    def test_no_elapsed_time_before_hardware_enable(self):
        timer = DuartWallTimer(DuartTimerConfig())
        self.assertEqual(timer.due_ticks(100.0), 0)
        self.assertEqual(timer.pending_ticks, 0)
        timer.start(100.0)
        self.assertEqual(timer.due_ticks(100.0), 0)
        self.assertAlmostEqual(timer.next_due_in(100.0), timer.config.period_seconds)

    def test_target_stopped_time_is_part_of_hardware_phase(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        self.assertEqual(timer.due_ticks(period / 2), 0)
        # No add_run call is required for stopped hardware processing/ISR time.
        self.assertEqual(timer.due_ticks(3.5 * period), 3)
        self.assertAlmostEqual(timer.next_due_in(3.5 * period), period / 2)

    def test_long_host_pause_is_coalesced_to_bounded_pending_ticks(self):
        timer = self.make_timer()
        now = 100.0
        periods = math.floor(now / timer.config.period_seconds)
        self.assertEqual(timer.due_ticks(now), MAX_WALL_TIMER_BATCH_TICKS)
        self.assertEqual(timer.pending_ticks,
                         MAX_WALL_TIMER_PENDING_TICKS - MAX_WALL_TIMER_BATCH_TICKS)
        self.assertEqual(timer.coalesced_ticks, periods - MAX_WALL_TIMER_PENDING_TICKS)
        self.assertEqual(timer.due_ticks(now), MAX_WALL_TIMER_BATCH_TICKS)
        self.assertEqual(timer.due_ticks(now), 0)
        self.assertEqual(timer.pending_ticks, 0)
        self.assertGreater(timer.next_due_in(now), 0)

    def test_fractional_observations_do_not_drift(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        count = 0
        for index in range(1, 20_001):
            count += timer.due_ticks(index * period / 4)
        self.assertEqual(count, 5000)
        self.assertEqual(timer.coalesced_ticks, 0)
        self.assertAlmostEqual(timer.next_due_in(5000 * period), period)

    def test_deadlines_keep_original_phase_after_coalescing(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        now = 100.25 * period
        self.assertEqual(timer.due_ticks(now), 4)
        self.assertEqual(timer.next_due_in(now), 0)
        self.assertEqual(timer.due_ticks(now), 4)
        self.assertAlmostEqual(timer.next_due_in(now), 0.75 * period)
        self.assertEqual(timer.due_ticks(101 * period), 1)
        self.assertAlmostEqual(timer.next_due_in(101 * period), period)

    def test_pending_debt_is_included_when_new_periods_arrive(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        self.assertEqual(timer.due_ticks(8 * period, limit=1), 1)
        self.assertEqual(timer.pending_ticks, 7)
        self.assertEqual(timer.due_ticks(12 * period, limit=1), 1)
        self.assertEqual(timer.pending_ticks, 7)
        self.assertEqual(timer.coalesced_ticks, 3)

    def test_deferred_reservation_retains_original_timer_phase(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        self.assertEqual(timer.due_ticks(4.5 * period), 4)
        timer.defer_ticks(3)
        self.assertEqual(timer.pending_ticks, 3)
        self.assertEqual(timer.next_due_in(4.5 * period), 0)
        self.assertEqual(timer.due_ticks(4.5 * period), 3)
        self.assertAlmostEqual(timer.next_due_in(4.5 * period), period / 2)
        self.assertEqual(timer.coalesced_ticks, 0)

    def test_deferred_reservation_does_not_sample_clock(self):
        def forbidden_clock():
            raise AssertionError("deferring a reservation must not sample time")
        timer = DuartWallTimer(DuartTimerConfig(), clock=forbidden_clock)
        timer.start(0.0)
        timer.defer_ticks(2)
        self.assertEqual(timer.pending_ticks, 2)
        self.assertEqual(timer.due_ticks(0.0), 2)

    def test_deferred_reservation_overflow_is_coalesced(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        self.assertEqual(timer.due_ticks(8 * period, limit=1), 1)
        timer.defer_ticks(4)
        self.assertEqual(timer.pending_ticks, 8)
        self.assertEqual(timer.coalesced_ticks, 3)
        self.assertEqual(timer.due_ticks(8 * period), 4)
        self.assertEqual(timer.due_ticks(8 * period), 4)
        self.assertEqual(timer.due_ticks(8 * period), 0)

    def test_invalid_deferred_reservations_do_not_mutate_scheduler(self):
        timer = self.make_timer()
        timer.defer_ticks(1)
        for invalid in (-1, 5, 128, True, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                timer.defer_ticks(invalid)
            self.assertEqual(timer.pending_ticks, 1)
            self.assertEqual(timer.coalesced_ticks, 0)
        timer.defer_ticks(0)
        self.assertEqual(timer.pending_ticks, 1)

    def test_deferred_ticks_cannot_activate_hardware(self):
        timer = DuartWallTimer(DuartTimerConfig())
        timer.defer_ticks(0)
        with self.assertRaises(RuntimeError):
            timer.defer_ticks(1)
        self.assertEqual(timer.pending_ticks, 0)
        self.assertEqual(timer.due_ticks(100.0), 0)

    def test_reset_discards_old_phase_pending_and_diagnostic_count(self):
        timer = self.make_timer()
        timer.due_ticks(100.0)
        self.assertGreater(timer.coalesced_ticks, 0)
        timer.reset(200.0)
        self.assertEqual(timer.pending_ticks, 0)
        self.assertEqual(timer.coalesced_ticks, 0)
        self.assertEqual(timer.due_ticks(200.0), 0)
        self.assertEqual(timer.due_ticks(200.0 + timer.config.period_seconds), 1)

    def test_injected_clock_is_used_consistently(self):
        now = [10.0]
        timer = DuartWallTimer(DuartTimerConfig(), clock=lambda: now[0])
        timer.start()
        now[0] += 2.5 * timer.config.period_seconds
        self.assertEqual(timer.due_ticks(), 2)
        self.assertAlmostEqual(timer.next_due_in(), timer.config.period_seconds / 2)

    def test_hardware_reconfiguration_starts_a_fresh_phase(self):
        normal = self.make_timer()
        normal.due_ticks(100.0)
        alternate = DuartWallTimer(DuartTimerConfig(preset=0x3A, vector=65))
        alternate.start(100.0)
        self.assertEqual(alternate.due_ticks(100.0), 0)
        self.assertAlmostEqual(alternate.next_due_in(100.0),
                               normal.config.period_seconds / 2)

    def test_invalid_clock_samples_do_not_mutate_scheduler(self):
        timer = self.make_timer()
        period = timer.config.period_seconds
        timer.due_ticks(2 * period)
        for invalid in (-1, float("nan"), float("inf"), period):
            with self.assertRaises(ValueError):
                timer.due_ticks(invalid)
            self.assertEqual(timer.pending_ticks, 0)
            self.assertEqual(timer.coalesced_ticks, 0)
        self.assertEqual(timer.due_ticks(3 * period), 1)

    def test_explicit_reset_can_reanchor_a_clock(self):
        timer = self.make_timer()
        timer.due_ticks(100.0)
        timer.reset(0.0)
        self.assertEqual(timer.due_ticks(timer.config.period_seconds), 1)

    def test_batch_limit_is_small_and_validated_before_accrual(self):
        timer = self.make_timer()
        for invalid in (0, -1, 5, 128, True, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                timer.due_ticks(100.0, limit=invalid)
        self.assertEqual(timer.pending_ticks, 0)
        self.assertEqual(timer.coalesced_ticks, 0)
        self.assertEqual(timer.due_ticks(timer.config.period_seconds), 1)

    def test_invalid_reset_and_clock_rejected(self):
        timer = self.make_timer()
        for invalid in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                timer.reset(invalid)
        self.assertEqual(timer.due_ticks(timer.config.period_seconds), 1)
        with self.assertRaises(ValueError):
            DuartWallTimer(DuartTimerConfig(), clock=None)


if __name__ == "__main__":
    unittest.main()
