"""68681 timer-mode timing, independent of CPU speed and debugger slices."""
from __future__ import annotations

from dataclasses import dataclass
import math

DEFAULT_X1_HZ = 3_686_400
MAX_BATCH_TICKS = 128


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
