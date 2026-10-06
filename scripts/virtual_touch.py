"""Virtual 3M controller with two-point calibration and native CC4 geometry.

CX acknowledges its start, then each calibration touch at liftoff. Calibration
belongs to the controller's nonvolatile storage, not the PC SRAM compatibility
file. Input coordinates always describe the 800x600 cabinet input surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cabinet_controls import TOUCH_WIDTH, TOUCH_HEIGHT, validate_command, validate_calibration_points


ACK = b"\x010\r"
POINT_ACK = b"\x011\r"
TARGETS = ((100, 525), (700, 75))


def native_tablet_packet(x: int, y: int, down: bool, *, wide: bool) -> bytes:
    """Invert FUN_7FD46 and the optional 80-pixel crop in FUN_7FE30.

    The firmware keeps ten coordinate bits, then floors their pixel scaling.
    Choose the first ten-bit value mapping to the requested pixel. Using a
    960-wide encoder without including the crop would shift actual hit testing.
    """
    validate_command({"type": "touch", "x": x, "y": y, "down": down})
    width, origin = (960, 80) if wide else (800, 0)
    raw_x = (((x + origin) * 1024 + width - 1) // width) << 4
    raw_y = ((y * 1024 + TOUCH_HEIGHT - 1) // TOUCH_HEIGHT) << 4
    return bytes((0xC0 if down else 0x80,
                  raw_x & 0x7F, (raw_x >> 7) & 0x7F,
                  raw_y & 0x7F, (raw_y >> 7) & 0x7F))


class VirtualTouchController:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path
        self.points: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.calibrating = False
        self.calibration_session = False
        self.first_point: tuple[int, int] | None = None
        self.contact: tuple[int, int] | None = None
        self.wide: bool | None = None
        self.events: list[str] = []
        if state_path is not None and state_path.exists():
            try:
                if state_path.stat().st_size > 2048:
                    raise ValueError("calibration file exceeds limit")
                data = json.loads(state_path.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("version") != 1
                        or data.get("surface") != [800, 600]):
                    raise ValueError("unsupported calibration format")
                self.points = self._validate_points(data["points"])
                self.events.append("DB_TOUCH_CALIBRATION_LOADED")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.events.append(f"DB_TOUCH_CALIBRATION_STORAGE_WARNING reason={exc}")

    @staticmethod
    def _validate_points(points) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if points is None:
            return None
        return validate_calibration_points(points)

    def reset_calibration(self) -> None:
        """Return to the exact 1:1 mapping of the 800x600 input surface.

        Host input is already geometrically exact; only the native service
        calibration (CX) stores points, like the original controller.
        """
        if self.calibration_session:
            raise ValueError("native service calibration is active")
        self.points = None
        self.events.append("DB_TOUCH_CALIBRATION_RESET source=control_panel")
        self._save()

    def _save(self) -> None:
        if self.state_path is None:
            return
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        created = False
        try:
            # Exclusive create: do not overwrite a leftover or foreign file.
            with temporary.open("x", encoding="utf-8") as stream:
                created = True
                json.dump({"version": 1, "surface": [800, 600], "points": self.points}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.state_path)
            self.events.append("DB_TOUCH_CALIBRATION_SAVED")
        except OSError as exc:
            if created:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            self.events.append(f"DB_TOUCH_CALIBRATION_STORAGE_WARNING reason={exc}")

    @staticmethod
    def initial_response(request: bytes) -> bytes | None:
        if request == b"\x01OI\r":
            return b"\x01A30000\r"
        if request in {b"\x01Z\r", b"\x01R\r", b"\x01AD\r", b"\x01PN814\r",
                       b"\x01FT\r", b"\x01MS\r", b"\x01PL\r", b"\x01RD\r"}:
            return ACK
        # CX needs a controller instance; a stateless acknowledgment is unsafe.
        return None

    def command(self, request: bytes) -> bytes | None:
        if request == b"\x01CX\r":
            self.calibrating = True
            self.calibration_session = True
            self.first_point = self.contact = None
            self.events.append("DB_TOUCH_CALIBRATION_STARTED targets=100,525;700,75")
            return ACK
        if request in (b"\x01R\r", b"\x01RD\r"):
            self.finish()
            if request == b"\x01RD\r":
                self.points = None
                self._save()
        return self.initial_response(request)

    def finish(self) -> None:
        if self.calibrating:
            self.events.append("DB_TOUCH_CALIBRATION_CANCELLED")
        self.calibrating = self.calibration_session = False
        self.first_point = self.contact = None

    def screen_point(self, x: int, y: int) -> tuple[int, int]:
        validate_command({"type": "touch", "x": x, "y": y, "down": True})
        if self.points is None:
            return x, y
        first, second = self.points
        result = tuple(round(TARGETS[0][axis] +
                             (value - first[axis]) * (TARGETS[1][axis] - TARGETS[0][axis]) /
                             (second[axis] - first[axis])) for axis, value in enumerate((x, y)))
        return max(0, min(799, result[0])), max(0, min(599, result[1]))

    def touch(self, x: int, y: int, down: bool, *, wide: bool) -> bytes | None:
        validate_command({"type": "touch", "x": x, "y": y, "down": down})
        if self.wide != wide:
            self.wide = wide
            self.events.append(f"DB_TOUCH_GEOMETRY width={960 if wide else 800} crop={80 if wide else 0}")
        if not self.calibrating:
            return native_tablet_packet(*self.screen_point(x, y), down, wide=wide)
        if down:
            self.contact = (x, y)
            return None
        if self.contact is None:
            return None
        # 3M registers the last held position at liftoff, not a second press.
        point, self.contact = self.contact, None
        if self.first_point is None:
            valid = point[0] < 400 and point[1] > 300
            if valid:
                self.first_point = point
                self.events.append(f"DB_TOUCH_CALIBRATION_POINT index=1 x={point[0]} y={point[1]}")
                return POINT_ACK
        else:
            try:
                calibrated = self._validate_points((self.first_point, point))
            except ValueError:
                pass
            else:
                self.points = calibrated
                self.calibrating = False
                self.first_point = None
                self.events.append(f"DB_TOUCH_CALIBRATION_POINT index=2 x={point[0]} y={point[1]}")
                self.events.append("DB_TOUCH_CALIBRATION_COMPLETED")
                self._save()
                return POINT_ACK
        self.calibrating = False
        self.first_point = None
        self.events.append("DB_TOUCH_CALIBRATION_REJECTED previous_values_retained=True")
        return ACK
