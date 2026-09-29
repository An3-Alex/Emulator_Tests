"""Read-only serial log stream for the USB flasher's monitor pane."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event


def stream_serial_log(
    port: str,
    stop: Event,
    emit: Callable[[str], None],
    serial_factory: Callable[..., object],
) -> None:
    """Read bytes only. Never send commands to the database or ESP32."""
    with serial_factory(port, baudrate=115200, timeout=0.25) as connection:
        while not stop.is_set():
            data = connection.read(256)
            if data:
                emit(data.decode("utf-8", errors="replace"))
