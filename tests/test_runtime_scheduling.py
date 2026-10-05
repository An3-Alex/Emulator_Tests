"""Foreground I/O and serial traffic must not starve board timer service."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from duart_timer import CpuRunBudget, DuartTimerConfig
from m68k_database_bridge import (
    should_drain_uart_before_board, MAX_UART_SERVICE_BURST,
    may_fast_forward_timer_wait,
    advance_button_pulses, BUTTON_PULSE_BOARD_SCANS,
    foreground_timer_quantum, uart_work_pending,
    interrupts_unmasked,
)


class RuntimeSchedulingTests(unittest.TestCase):
    def test_watchpoints_carry_remaining_cpu_time_forward(self):
        budget = CpuRunBudget(0.01)
        for _ in range(9):
            budget.add_run(0.001)
            self.assertFalse(budget.due)
        self.assertAlmostEqual(budget.remaining, 0.001)
        budget.add_run(0.001)
        self.assertTrue(budget.due)
        self.assertEqual(budget.segments, 10)

    def test_stopped_target_time_does_not_create_timer_debt(self):
        budget = CpuRunBudget(0.01)
        budget.add_run(0.002)
        for _ in range(1000):
            self.assertFalse(budget.due)
            self.assertAlmostEqual(budget.remaining, 0.008)
        budget.add_run(100)
        self.assertEqual(budget.elapsed, 0.01)
        budget.reset()
        self.assertFalse(budget.due)
        self.assertEqual(budget.segments, 0)

    def test_invalid_cpu_durations_rejected(self):
        for seconds in (-1, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CpuRunBudget(seconds)
        budget = CpuRunBudget(0.03)
        for seconds in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                budget.add_run(seconds)

    def test_serial_burst_yields_to_due_board_tick(self):
        for burst in range(MAX_UART_SERVICE_BURST):
            self.assertTrue(should_drain_uart_before_board(True, 10, burst))
        self.assertFalse(should_drain_uart_before_board(True, 10, MAX_UART_SERVICE_BURST))
        self.assertFalse(should_drain_uart_before_board(True, 0, MAX_UART_SERVICE_BURST))
        self.assertFalse(should_drain_uart_before_board(False, 10, 0))

    def test_large_cpu_slice_does_not_delay_foreground_callback_poll(self):
        config = DuartTimerConfig()
        self.assertAlmostEqual(foreground_timer_quantum(0.04, config), config.period_seconds)
        self.assertEqual(foreground_timer_quantum(0.0005, config), 0.0005)
        self.assertEqual(foreground_timer_quantum(0.01, DuartTimerConfig(preset=65535)), 0.01)

    def test_idle_uart_uses_one_pointer_read_and_no_interrupt(self):
        class Rsp:
            def __init__(self, pointers):
                self.pointers, self.reads = pointers, []
            def read_memory(self, address, length):
                self.reads.append((address, length))
                return self.pointers
        rsp = Rsp(bytes.fromhex("001EBBBC001EBBBC"))
        self.assertFalse(uart_work_pending(rsp, False))
        self.assertEqual(rsp.reads, [(0x1EBBB4, 8)])
        rsp = Rsp(bytes.fromhex("001EBBBC001EBBBD"))
        self.assertTrue(uart_work_pending(rsp, False))

    def test_received_byte_and_tx_ready_transition_never_skip_uart(self):
        class Rsp:
            def read_memory(self, *args):
                raise AssertionError("pending work already proves IRQ needed")
        self.assertTrue(uart_work_pending(Rsp(), True))
        self.assertTrue(uart_work_pending(Rsp(), False, True))

    def test_original_interrupt_mask_blocks_host_irq_injection(self):
        self.assertTrue(interrupts_unmasked(0x2000))
        self.assertTrue(interrupts_unmasked(0x201F))
        for mask in range(1, 8):
            self.assertFalse(interrupts_unmasked(0x2000 | mask << 8))

    def test_pending_cabinet_work_keeps_real_board_ticks(self):
        self.assertTrue(may_fast_forward_timer_wait(0, 0))
        self.assertFalse(may_fast_forward_timer_wait(0, 0, cabinet_work_pending=True))

    def test_service_press_cannot_expire_in_interrupt_only_batch(self):
        pressed, edges, released = {"service"}, set(), set()
        scans = {"service": BUTTON_PULSE_BOARD_SCANS}
        budget = CpuRunBudget(0.04)
        for _ in range(128):
            completed = advance_button_pulses(
                pressed, edges, released, scans,
                foreground_pending={"service"} if not budget.due else set(),
            )
            self.assertEqual(completed, set())
            self.assertEqual(pressed, {"service"})
        self.assertEqual(scans, {"service": 1})
        for _ in range(4):
            budget.add_run(0.01)
        self.assertEqual(advance_button_pulses(
            pressed, edges, released, scans,
            foreground_pending={"service"} if not budget.due else set(),
        ), {"service"})
        self.assertEqual(released, {"service"})
        self.assertFalse(pressed)

    def test_each_key_has_its_own_foreground_hold(self):
        pressed, edges, released = {"start", "service"}, set(), set()
        scans = {"start": 1, "service": 1}
        self.assertEqual(advance_button_pulses(
            pressed, edges, released, scans, foreground_pending={"service"},
        ), {"start"})
        self.assertEqual(pressed, {"service"})
        self.assertEqual(scans, {"service": 1})


if __name__ == "__main__":
    unittest.main()
