"""Epson RTC-4543 three-wire calendar, as connected to the owner board port.

The firmware drives CE/WR/CLK on bits 6/5/4 of $FFF907 and samples DATA on
bit 7. The RTC shifts seven BCD fields (52 bits) least-significant bit first.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable


DATA = 0x80
CE = 0x40
WR = 0x20
CLK = 0x10
BITS = 52
DEFAULT_TIME = dt.datetime(2012, 2, 1, 22, 14)


def _bcd(value: int) -> int:
    return (value // 10 << 4) | value % 10


def _decimal(value: int) -> int:
    return (value >> 4) * 10 + (value & 0x0F)


def calendar_bytes(value: dt.datetime) -> bytes:
    # Epson weekday convention: Sunday=1, Monday=2, ..., Saturday=7.
    weekday = (value.weekday() + 1) % 7 + 1
    return bytes((
        _bcd(value.second), _bcd(value.minute), _bcd(value.hour), weekday,
        _bcd(value.day), _bcd(value.month), _bcd(value.year % 100),
    ))


def calendar_bits(value: dt.datetime) -> tuple[int, ...]:
    fields = calendar_bytes(value)
    widths = (8, 8, 8, 4, 8, 8, 8)
    return tuple((field >> bit) & 1 for field, width in zip(fields, widths)
                 for bit in range(width))


class Rtc4543:
    def __init__(
        self,
        initial_time: dt.datetime = DEFAULT_TIME,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_time = initial_time
        self._monotonic = monotonic
        self._base_tick = monotonic()
        self._previous_port = 0
        self._bits: tuple[int, ...] = ()
        self._read_calendar = b""
        self._received: list[int] = []
        self._index = 0
        self.completed_read: bytes | None = None
        self.completed_write: dt.datetime | None = None

    def now(self) -> dt.datetime:
        elapsed = max(0, int(self._monotonic() - self._base_tick))
        return self._base_time + dt.timedelta(seconds=elapsed)

    def _accept_write(self) -> None:
        if len(self._received) != BITS:
            return
        widths = (8, 8, 8, 4, 8, 8, 8)
        values = []
        cursor = 0
        for width in widths:
            values.append(sum(bit << offset for offset, bit in enumerate(
                self._received[cursor:cursor + width]
            )))
            cursor += width
        second, minute, hour, weekday, day, month, year = values
        try:
            value = dt.datetime(
                2000 + _decimal(year), _decimal(month), _decimal(day),
                _decimal(hour), _decimal(minute), _decimal(second),
            )
        except ValueError:
            return
        if weekday != (value.weekday() + 1) % 7 + 1:
            return
        self._base_time = value
        self._base_tick = self._monotonic()
        self.completed_write = value

    def port_write(self, port: int) -> int:
        """Observe one firmware write and return the board's pin feedback."""
        if not 0 <= port <= 0xFF:
            raise ValueError("port byte out of range")
        previous = self._previous_port
        enabled = bool(port & CE)
        was_enabled = bool(previous & CE)
        writing = bool(port & WR)
        if enabled and not was_enabled:
            self._index = 0
            when = self.now()
            self._read_calendar = calendar_bytes(when)
            self._bits = calendar_bits(when)
            self._received = []
            self.completed_read = None
            self.completed_write = None
        if enabled and not (previous & CLK) and (port & CLK):
            if writing:
                if len(self._received) < BITS:
                    self._received.append(1 if port & DATA else 0)
            elif self._index < BITS:
                port = (port & ~DATA) | (DATA if self._bits[self._index] else 0)
                self._index += 1
                if self._index == BITS:
                    self.completed_read = self._read_calendar
        if was_enabled and not enabled and bool(previous & WR):
            self._accept_write()
        self._previous_port = port
        return port
