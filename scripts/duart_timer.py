"""68681 timer-mode timing, independent of CPU speed and debugger slices."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

DEFAULT_X1_HZ = 3_686_400
MAX_BATCH_TICKS = 128
MAX_WALL_TIMER_BATCH_TICKS = 4
MAX_WALL_TIMER_PENDING_TICKS = 8


class CpuRunBudget:
    """Carry one bounded CPU slice across debugger watchpoint stops.

    Only continue-command durations count; stopped-target work is excluded.
    No accumulated host-time catch-up backlog is created.
    """
    def __init__(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("CPU slice must be finite and positive")
        self.seconds = seconds
        self.reset()

    def reset(self) -> None:
        self.elapsed = 0.0
        self.segments = 0

    def add_run(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("CPU run duration must be finite and nonnegative")
        self.elapsed = min(self.seconds, self.elapsed + seconds)
        self.segments += 1

    @property
    def due(self) -> bool:
        return self.elapsed >= self.seconds - 1e-10

    @property
    def remaining(self) -> float:
        return max(0.0001, self.seconds - self.elapsed)


@dataclass(frozen=True)
class DuartTimerConfig:
    acr: int = 0xF0
    preset: int = 0x74
    vector: int = 64
    x1_hz: int = DEFAULT_X1_HZ

    def __post_init__(self) -> None:
        if not 0 <= self.acr <= 255 or not 2 <= self.preset <= 65535:
            raise ValueError("invalid DUART timer register value")
        if (self.acr >> 4) & 7 != 7:
            raise ValueError("unsupported DUART timer source: expected timer X1/16")
        if self.vector not in (64, 65):
            raise ValueError("unsupported CC4 DUART interrupt vector")
        if not isinstance(self.x1_hz, int) or not 1 <= self.x1_hz <= 10_000_000:
            raise ValueError("DUART X1 frequency must be 1..10000000 Hz")

    @property
    def period_seconds(self) -> float:
        # Timer mode has two half periods, each preset counts at X1/16.
        # The documented minimum preset is two. Undefined zero/one values
        # must not generate a made-up interrupt rate.
        return 2 * 16 * self.preset / self.x1_hz

    @classmethod
    def from_registers(cls, acr: int, ctur: int, ctlr: int, ivr: int,
                       *, x1_hz: int = DEFAULT_X1_HZ) -> DuartTimerConfig:
        if not 0 <= ctur <= 255 or not 0 <= ctlr <= 255:
            raise ValueError("invalid DUART counter preset byte")
        return cls(acr, ctur << 8 | ctlr, ivr, x1_hz)


class DuartTimerBudget:
    """Accumulate fractional ticks without an unbounded host catch-up burst.

    Only the explicitly budgeted CPU run slice counts. Debugger processing
    time never creates a growing backlog of synthetic IRQs.
    """
    def __init__(self, config: DuartTimerConfig) -> None:
        self.config = config
        self.fraction = 0.0

    def take_slice(self, seconds: float) -> int:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("timer slice must be finite and positive")
        exact = self.fraction + seconds / self.config.period_seconds
        ticks = math.floor(exact + 1e-10)
        if ticks > MAX_BATCH_TICKS:
            raise ValueError(f"host throttle interval requires too many virtual timer ticks: {ticks}")
        self.fraction = max(0.0, exact - ticks)
        return ticks


class DuartWallTimer:
    """Schedule the hardware timer on a bounded monotonic wall-time phase.

    Target-stopped work and ISR handling count once the timer is started.
    Missed periods are coalesced rather than replayed without bound. The
    caller must allow foreground CPU progress between batches; a bounded
    pending count alone cannot prevent an IRQ-only loop on a slow host.
    No guest register, timeout counter, CPU rate or crystal rate is changed.
    """

    def __init__(self, config: DuartTimerConfig, *, clock=time.monotonic) -> None:
        if not callable(clock):
            raise ValueError("timer clock must be callable")
        self.config = config
        self.clock = clock
        self._started_at: float | None = None
        self._last_observed: float | None = None
        self._accounted_ticks = 0
        self._pending_ticks = 0
        self.coalesced_ticks = 0

    def _now(self, now: float | None) -> float:
        result = self.clock() if now is None else now
        if not math.isfinite(result) or result < 0:
            raise ValueError("timer clock must be finite and nonnegative")
        return result

    def reset(self, now: float | None = None) -> None:
        """Activate/reanchor after enable or reconfiguration, without catch-up."""
        now = self._now(now)
        self._started_at = now
        self._last_observed = now
        self._accounted_ticks = 0
        self._pending_ticks = 0
        self.coalesced_ticks = 0

    start = reset

    @property
    def pending_ticks(self) -> int:
        return self._pending_ticks

    def _accrue(self, now: float | None) -> float:
        now = self._now(now)
        if self._started_at is None:
            return now
        if now < self._last_observed:
            raise ValueError("timer clock moved backwards; reset is required")
        self._last_observed = now
        elapsed_periods = (now - self._started_at) / self.config.period_seconds
        # Keep the phase anchored to the enable time, not to each observation.
        # The tolerance only corrects float round-off at an exact deadline.
        total_ticks = math.floor(elapsed_periods + 1e-9)
        new_ticks = max(0, total_ticks - self._accounted_ticks)
        self._accounted_ticks = total_ticks
        available = self._pending_ticks + new_ticks
        self._pending_ticks = min(available, MAX_WALL_TIMER_PENDING_TICKS)
        self.coalesced_ticks += available - self._pending_ticks
        return now

    def due_ticks(self, now: float | None = None, *, limit: int = MAX_WALL_TIMER_BATCH_TICKS) -> int:
        """Consume one small batch; excess periods never form a large backlog."""
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or not 1 <= limit <= MAX_WALL_TIMER_BATCH_TICKS):
            raise ValueError(f"timer batch limit must be 1..{MAX_WALL_TIMER_BATCH_TICKS}")
        self._accrue(now)
        ticks = min(self._pending_ticks, limit)
        self._pending_ticks -= ticks
        return ticks

    def defer_ticks(self, count: int) -> None:
        """Return unused reserved ticks when the CPU masks an interrupt.

        This is only for a reservation from this timer/configuration epoch.
        It neither samples the clock nor starts or resets the timer phase.
        """
        if (isinstance(count, bool) or not isinstance(count, int)
                or not 0 <= count <= MAX_WALL_TIMER_BATCH_TICKS):
            raise ValueError(f"deferred timer ticks must be 0..{MAX_WALL_TIMER_BATCH_TICKS}")
        if count and self._started_at is None:
            raise RuntimeError("cannot defer ticks before the timer is started")
        available = self._pending_ticks + count
        self._pending_ticks = min(available, MAX_WALL_TIMER_PENDING_TICKS)
        self.coalesced_ticks += available - self._pending_ticks

    def next_due_in(self, now: float | None = None) -> float:
        """Seconds until a timer period, or zero for already pending work."""
        now = self._accrue(now)
        if self._started_at is None:
            return self.config.period_seconds
        if self._pending_ticks:
            return 0.0
        deadline = self._started_at + (
            self._accounted_ticks + 1
        ) * self.config.period_seconds
        return max(0.0, deadline - now)
