#!/usr/bin/env python3
"""Narrowly scoped COM3 bridge for the legacy video INIT packet.

QEMU owns the TCP listener.  This client connects, records all bytes emitted by
the guest.  In automatic mode it retries the reconstructed INITVIDEO frame
until the evidenced acknowledgement arrives, then sends only the evidenced
read-only startup discovery queries.  When QEMU creates a new TCP connection,
the sequence begins again.  It deliberately implements no acceptor, dispenser,
payout, jackpot, or other monetary peripheral protocol.
"""

from __future__ import annotations

import argparse
import datetime as dt
import enum
import msvcrt
import socket
import struct
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from vidcom_log_parser import parse_file
from validate_owner_database_set import inspect_set as inspect_owner_database_set


SOH = 0x01
STX = 0x02
EOT = 0x04
VID_COM_INITVIDEO = 34
VID_COM_COMMAND_VARIPARA = 64
VID_COM_STARTUPTXT = 70
VID_COM_GRAPHICCHECKSUM = 73
VID_COM_SYSTEMINFO = 79
GO_MENUE = 11327
GAME_MENUE_SETACTIVEGAMES = 102
INITVIDEO_ACK = b"\x06\x03\x00\x00"
NAK_PREFIX = b"\x15\x03\x00"
NAK_ERROR_NAMES = {
    1: "ERR_NOINITCOMMAND",
    2: "ERR_WAITFOR_EOTSTX",
    3: "ERR_UNKNOWN_COMID",
    4: "ERR_VARIPARABYTE0",
    5: "ERR_TIMEOUTRECEIVE",
}
DEFAULT_SERIAL_BAUD = 9600
DEFAULT_SERIAL_CHUNK_SIZE = 1
SERIAL_BITS_PER_BYTE = 10

# Exact payload recovered from VidComLog.5.part_01.txt.  It decodes to the
# original M90/Magie90 video configuration and contains no monetary-device
# state.  The emulated database clock is intentionally historical because the
# legacy software expects a matching deployment-era date.  The recorded 2012
# year is now retained instead of the earlier 2010 override.
RECORDED_INITVIDEO_PAYLOAD = bytes.fromhex(
    "7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C 01 00 00 00 00 05 "
    "DC 07 02 01 16 0E 13 11 00 00 01 00 02 FF"
)
EMULATED_DATABASE_TIME = dt.datetime(2012, 2, 1, 22, 14)


class AckTracker:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._count = 0
        self._nak_count = 0
        self._last_nak_error = None
        self._buffer = bytearray()

    def feed(self, data: bytes) -> int:
        added = 0
        with self._condition:
            self._buffer.extend(data)
            nak_added = 0
            while True:
                ack_offset = self._buffer.find(INITVIDEO_ACK)
                nak_offset = self._buffer.find(NAK_PREFIX)
                candidates = []
                if ack_offset >= 0:
                    candidates.append((ack_offset, "ack"))
                if nak_offset >= 0 and len(self._buffer) >= nak_offset + 4:
                    candidates.append((nak_offset, "nak"))
                if not candidates:
                    if len(self._buffer) > 3:
                        del self._buffer[:-3]
                    break
                offset, kind = min(candidates)
                if kind == "nak":
                    self._last_nak_error = self._buffer[offset + 3]
                    del self._buffer[: offset + 4]
                    self._nak_count += 1
                    nak_added += 1
                else:
                    del self._buffer[: offset + len(INITVIDEO_ACK)]
                    self._count += 1
                    added += 1
            if added or nak_added:
                self._condition.notify_all()
        return added

    def snapshot(self) -> int:
        with self._condition:
            return self._count

    def response_snapshot(self) -> tuple[int, int]:
        with self._condition:
            return self._count, self._nak_count

    def last_nak(self) -> tuple[int, int | None]:
        with self._condition:
            return self._nak_count, self._last_nak_error

    def wait_after(self, previous: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._count <= previous:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_response_after(
        self, previous_ack: int, previous_nak: int, timeout: float
    ) -> tuple[str, int | None] | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                # A coalesced NAK+ACK is a rejection, never a success.
                if self._nak_count > previous_nak:
                    return "nak", self._last_nak_error
                if self._count > previous_ack:
                    return "ack", None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)


class DataFrameTracker:
    """Decode guest replies framed as 07, one-byte length, command, payload."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._buffer = bytearray()
        self._counts: dict[int, int] = {}

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        frames = []
        with self._condition:
            self._buffer.extend(data)
            while True:
                start = self._buffer.find(0x07)
                if start < 0:
                    self._buffer.clear()
                    break
                if start:
                    del self._buffer[:start]
                if len(self._buffer) < 2:
                    break
                body_length = self._buffer[1]
                if body_length < 2:
                    del self._buffer[0]
                    continue
                total_length = body_length + 2
                if len(self._buffer) < total_length:
                    break
                body = bytes(self._buffer[2:total_length])
                del self._buffer[:total_length]
                command = struct.unpack_from("<H", body, 0)[0]
                payload = body[2:]
                frames.append((command, payload))
                self._counts[command] = self._counts.get(command, 0) + 1
            if frames:
                self._condition.notify_all()
        return frames

    def snapshot(self, command: int) -> int:
        with self._condition:
            return self._counts.get(command, 0)

    def wait_after(self, command: int, previous: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._counts.get(command, 0) <= previous:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


class EmulatorPhase(enum.Enum):
    CONNECTED = "connected"
    WAITING_FOR_INIT = "waiting_for_init"
    INITIALIZED = "initialized"
    DISCOVERY = "discovery"
    STARTUP_TEXT = "startup_text"
    RENDERING = "rendering"
    INTERACTIVE = "interactive"
    REJECTED = "rejected"


class EmulatorSession:
    """Small explicit state model for the COM3-side database surrogate."""

    _ALLOWED = {
        EmulatorPhase.CONNECTED: {EmulatorPhase.WAITING_FOR_INIT},
        EmulatorPhase.WAITING_FOR_INIT: {EmulatorPhase.INITIALIZED, EmulatorPhase.REJECTED},
        EmulatorPhase.INITIALIZED: {EmulatorPhase.DISCOVERY, EmulatorPhase.REJECTED},
        EmulatorPhase.DISCOVERY: {EmulatorPhase.STARTUP_TEXT, EmulatorPhase.REJECTED},
        EmulatorPhase.STARTUP_TEXT: {EmulatorPhase.RENDERING, EmulatorPhase.REJECTED},
        EmulatorPhase.RENDERING: {EmulatorPhase.INTERACTIVE, EmulatorPhase.REJECTED},
        EmulatorPhase.INTERACTIVE: {EmulatorPhase.REJECTED},
        EmulatorPhase.REJECTED: set(),
    }

    def __init__(self) -> None:
        self.phase = EmulatorPhase.CONNECTED

    def transition(self, phase: EmulatorPhase) -> None:
        if phase not in self._ALLOWED[self.phase]:
            raise RuntimeError(
                f"invalid emulator transition {self.phase.value} -> {phase.value}"
            )
        self.phase = phase


def build_initvideo_payload(when: dt.datetime | None = None) -> bytes:
    """Return the evidenced payload with the selected historical DB time."""
    database_time = when or EMULATED_DATABASE_TIME
    payload = bytearray(RECORDED_INITVIDEO_PAYLOAD)
    payload[20:26] = struct.pack(
        "<HBBBB",
        database_time.year,
        database_time.month,
        database_time.day,
        database_time.hour,
        database_time.minute,
    )
    return bytes(payload)


def build_initvideo_frame(when: dt.datetime | None = None) -> bytes:
    return build_fixed_frame(VID_COM_INITVIDEO, build_initvideo_payload(when))


def build_fixed_frame(command: int, payload: bytes) -> bytes:
    return bytes([SOH, STX]) + struct.pack("<H", command) + payload + bytes([EOT])


def send_serial_frame(
    sock: socket.socket, frame: bytes, *, baud: int, chunk_size: int
) -> None:
    """Clock a frame like the original 8-N-1 serial link.

    QEMU's socket character backend otherwise accepts an entire large frame at
    once.  The guest is configured for 9600 baud and its emulated UART cannot
    drain a 496-byte VARIPARA frame at TCP speed without losing bytes.
    """
    seconds_per_byte = SERIAL_BITS_PER_BYTE / baud
    for offset in range(0, len(frame), chunk_size):
        chunk = frame[offset : offset + chunk_size]
        sock.sendall(chunk)
        if offset + len(chunk) < len(frame):
            time.sleep(len(chunk) * seconds_per_byte)


# Chronological non-mutating discovery sequence recovered from the original
# VidCom log immediately after INITVIDEO.  The SYSTEMINFO IDs are the complete
# 0..9 set defined by commandointerpreter.h; command 73 requests the graphics
# checksum.  This sequence deliberately contains neither REQUEST_KEY (69) nor
# any money/peripheral command.
SAFE_DISCOVERY_SEQUENCE = (
    (VID_COM_SYSTEMINFO, b"\x09\x00", "systeminfo cf-version"),
    (VID_COM_GRAPHICCHECKSUM, b"\x00", "graphics checksum"),
    *((VID_COM_SYSTEMINFO, bytes([info_id, 0]), f"systeminfo {info_id}") for info_id in range(9)),
)


def build_startup_text_payload(
    text: str, *, clear: int = 0, red: int = 0, green: int = 255, blue: int = 0
) -> bytes:
    encoded = text.encode("utf-16le")
    body = bytes([clear, red, green, blue]) + encoded
    return struct.pack("<H", len(body)) + body


# Text, color and relative delays are taken from the same chronological log
# segment as INIT/discovery.  The last two records merely display that the
# acceptor and dispenser are absent; they do not implement either peripheral.
SAFE_STARTUP_TEXT_SEQUENCE = (
    (0.062, "ADP1486             ", 0, 0, 255, 0),
    (0.063, "Ergoline  Mode: 3", 0, 0, 255, 0),
    (0.640, "TOUCH<B8N1>", 0, 0, 255, 0),
    (8.032, "MP WHM9A01.216 Wck921.13 v5", 0, 0, 255, 0),
    (2.703, "KEIN AKZEPTOR", 0, 255, 0, 0),
    (0.047, "KEIN DISPENSER", 0, 255, 0, 0),
)

# The first post-status graphics burst in the supplied owner log contains only
# these display/audio command families.  Payloads are never bundled here: they
# are loaded from a separately supplied, hash-pinned VidCom log at runtime.
SAFE_RENDER_COMMANDS = frozenset({54, 55, 56, 57, 58, 60, 62, 63, 64, 66})
SAFE_RENDER_START_TICK = 66343
SAFE_RENDER_END_TICK = 74718

# The next complete, interaction-free display phase available in part_00.  It
# ends before the first recorded TOUCHCLICKDOWN command at tick 375515.
SAFE_CONTINUATION_COMMANDS = frozenset({48, 54, 55, 56, 57, 58, 60, 62, 64, 66, 67})
SAFE_CONTINUATION_START_TICK = 314890
SAFE_CONTINUATION_END_TICK = 373156


def validate_evidenced_payload(command: int, payload: bytes) -> None:
    """Validate layouts that are explicit in the matching owner-image header."""
    if command == VID_COM_COMMAND_VARIPARA:
        if len(payload) < 5:
            raise ValueError("command 64 payload is shorter than its packed header")
        num_values = struct.unpack_from("<H", payload, 0)[0]
        expected_size = num_values + 5  # length + objId + secComId + values
        if len(payload) != expected_size:
            raise ValueError(
                f"command 64 payload size mismatch: numWerte={num_values}, "
                f"expected={expected_size}, actual={len(payload)}"
            )
        object_id = struct.unpack_from("<H", payload, 2)[0]
        subcommand_id = payload[4]
        if (
            object_id == GO_MENUE
            and subcommand_id == GAME_MENUE_SETACTIVEGAMES
            and num_values % 3 != 0
        ):
            raise ValueError(
                "Menue SETACTIVEGAMES value bytes are not a whole number of "
                "three-byte entries"
            )


def describe_evidenced_payload(command: int, payload: bytes) -> str:
    if command != VID_COM_COMMAND_VARIPARA or len(payload) < 5:
        return ""
    num_values, object_id = struct.unpack_from("<HH", payload, 0)
    subcommand_id = payload[4]
    description = (
        f"numWerte={num_values} objId={object_id} secComId={subcommand_id}"
    )
    if object_id == GO_MENUE and subcommand_id == GAME_MENUE_SETACTIVEGAMES:
        description += f" Menue.SETACTIVEGAMES entries={num_values // 3}"
    return description


def load_pinned_command_range(
    path: Path,
    expected_sha256: str,
    start_tick: int,
    end_tick: int,
    allowed_commands: frozenset[int],
    label: str,
):
    digest, records = parse_file(path)
    normalized_expected = expected_sha256.replace(" ", "").upper()
    if digest != normalized_expected:
        raise ValueError(f"owner log SHA-256 mismatch: expected {normalized_expected}, got {digest}")
    selected = [
        record for record in reversed(records) if start_tick <= record.tick <= end_tick
    ]
    if not selected:
        raise ValueError(f"owner log contains no records in the evidenced {label} range")
    if selected[0].tick != start_tick or selected[-1].tick != end_tick:
        raise ValueError(f"owner log {label} boundaries do not match the evidenced range")
    forbidden = sorted({record.command for record in selected} - allowed_commands)
    if forbidden:
        raise ValueError(f"{label} contains non-allowlisted commands: {forbidden}")
    for record in selected:
        validate_evidenced_payload(record.command, bytes.fromhex(record.payload_hex))
    return digest, selected


def load_safe_render_prefix(path: Path, expected_sha256: str):
    return load_pinned_command_range(
        path,
        expected_sha256,
        SAFE_RENDER_START_TICK,
        SAFE_RENDER_END_TICK,
        SAFE_RENDER_COMMANDS,
        "render prefix",
    )


def load_safe_display_continuation(path: Path, expected_sha256: str):
    return load_pinned_command_range(
        path,
        expected_sha256,
        SAFE_CONTINUATION_START_TICK,
        SAFE_CONTINUATION_END_TICK,
        SAFE_CONTINUATION_COMMANDS,
        "display continuation",
    )


def stamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def connect(host: str, port: int, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.settimeout(0.5)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def acquire_single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise RuntimeError(f"another VidCom bridge owns lock {path.resolve()}")
    return stream


def owner_records_through_tick(records, stop_after_tick):
    """Return the safe prefix ending at an exact diagnostic record boundary."""
    if stop_after_tick is None:
        return list(records), False
    selected = []
    for record_item in records:
        selected.append(record_item)
        if record_item.tick == stop_after_tick:
            return selected, True
    return selected, False


def owner_records_without_ticks(records, skip_ticks):
    """Exclude only explicitly selected, already validated owner-log records."""
    skipped = set(skip_ticks)
    return [record_item for record_item in records if record_item.tick not in skipped]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4553)
    parser.add_argument("--connect-timeout", type=float, default=120.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(__file__).parents[1] / "logs" / "vidcom-bridge.lock",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--auto",
        action="store_true",
        help="retry INIT a bounded number of times, then send the safe startup sequence",
    )
    mode.add_argument(
        "--resume-safe",
        action="store_true",
        help="reconnect to an already initialized guest and send only safe discovery queries",
    )
    mode.add_argument(
        "--resume-owner-render",
        action="store_true",
        help="reconnect to an initialized guest and immediately replay the hash-pinned safe startup/render prefix",
    )
    mode.add_argument(
        "--resume-owner-continuation-at",
        type=int,
        metavar="TICK",
        help="reconnect and resume the validated display continuation at an exact record tick",
    )
    parser.add_argument("--init-retry-seconds", type=float, default=30.0)
    parser.add_argument("--initial-init-delay", type=float, default=165.0)
    parser.add_argument("--max-init-attempts", type=int, default=3)
    parser.add_argument("--command-ack-timeout", type=float, default=30.0)
    parser.add_argument("--serial-baud", type=int, default=DEFAULT_SERIAL_BAUD)
    parser.add_argument(
        "--serial-chunk-size", type=int, default=DEFAULT_SERIAL_CHUNK_SIZE
    )
    parser.add_argument("--owner-vidcom-log", type=Path)
    parser.add_argument("--owner-vidcom-sha256")
    parser.add_argument("--owner-vidcom-continuation", type=Path)
    parser.add_argument("--owner-vidcom-continuation-sha256")
    parser.add_argument("--owner-database", type=Path)
    parser.add_argument("--owner-loader", type=Path)
    parser.add_argument("--owner-database-sha256")
    parser.add_argument("--owner-loader-sha256")
    parser.add_argument("--owner-module", type=Path, action="append", default=[])
    parser.add_argument("--owner-module-sha256", action="append", default=[])
    parser.add_argument(
        "--pause-after-tick",
        type=int,
        action="append",
        default=[],
        help="diagnostic checkpoint: wait for Enter after ACK of this owner-log tick",
    )
    parser.add_argument(
        "--pause-before-tick",
        type=int,
        action="append",
        default=[],
        help="diagnostic checkpoint: wait for Enter before sending this owner-log tick",
    )
    parser.add_argument(
        "--stop-after-tick",
        type=int,
        help=(
            "diagnostic boundary: after this exact owner-log tick is ACKed, "
            "send no later owner records and only monitor the guest"
        ),
    )
    parser.add_argument(
        "--skip-owner-tick",
        type=int,
        action="append",
        default=[],
        help=(
            "diagnostic compatibility filter: omit this exact record boundary "
            "from otherwise hash-validated owner render data"
        ),
    )
    args = parser.parse_args()

    if bool(args.owner_vidcom_log) != bool(args.owner_vidcom_sha256):
        parser.error("--owner-vidcom-log and --owner-vidcom-sha256 must be supplied together")
    if bool(args.owner_vidcom_continuation) != bool(args.owner_vidcom_continuation_sha256):
        parser.error(
            "--owner-vidcom-continuation and --owner-vidcom-continuation-sha256 "
            "must be supplied together"
        )
    if args.resume_owner_render and not args.owner_vidcom_log:
        parser.error("--resume-owner-render requires a hash-pinned --owner-vidcom-log")
    if args.resume_owner_continuation_at is not None and not args.owner_vidcom_continuation:
        parser.error(
            "--resume-owner-continuation-at requires a hash-pinned owner continuation"
        )
    owner_database_options = (
        args.owner_database,
        args.owner_loader,
        args.owner_database_sha256,
        args.owner_loader_sha256,
    )
    if any(owner_database_options) and not all(owner_database_options):
        parser.error(
            "--owner-database, --owner-loader and both owner SHA-256 pins "
            "must be supplied together"
        )
    if len(args.owner_module) != len(args.owner_module_sha256):
        parser.error("each --owner-module requires one --owner-module-sha256")
    if args.owner_module and not args.owner_database:
        parser.error("auxiliary owner modules require the owner database/loader set")
    if args.max_init_attempts < 1:
        parser.error("--max-init-attempts must be at least 1")
    if args.initial_init_delay < 0:
        parser.error("--initial-init-delay cannot be negative")
    if args.command_ack_timeout <= 0:
        parser.error("--command-ack-timeout must be positive")
    if args.serial_baud <= 0:
        parser.error("--serial-baud must be positive")
    if args.serial_chunk_size <= 0:
        parser.error("--serial-chunk-size must be positive")
    try:
        instance_lock = acquire_single_instance_lock(args.lock_file)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.owner_database:
        owner_database_set = inspect_owner_database_set(
            args.owner_database,
            args.owner_loader,
            args.owner_database_sha256,
            args.owner_loader_sha256,
            args.owner_module,
            args.owner_module_sha256,
        )
        if not owner_database_set["recognized_pair"]:
            failed = [
                name
                for name, passed in owner_database_set["pair_validations"].items()
                if not passed
            ]
            parser.error(f"owner database/loader validation failed: {failed}")
        print(
            "OWNER_DATABASE_SET_VALIDATED "
            f"database_sha256={owner_database_set['database']['sha256']} "
            f"loader_sha256={owner_database_set['loader']['sha256']} "
            f"module_id={owner_database_set['database']['header']['module_id']} "
            f"auxiliary_modules={len(owner_database_set['modules'])}",
            flush=True,
        )
    render_prefix = None
    if args.owner_vidcom_log:
        digest, render_prefix = load_safe_render_prefix(
            args.owner_vidcom_log, args.owner_vidcom_sha256
        )
        print(
            f"OWNER_RENDER_PREFIX_VALIDATED sha256={digest} records={len(render_prefix)}",
            flush=True,
        )
    display_continuation = None
    if args.owner_vidcom_continuation:
        continuation_digest, display_continuation = load_safe_display_continuation(
            args.owner_vidcom_continuation,
            args.owner_vidcom_continuation_sha256,
        )
        print(
            f"OWNER_DISPLAY_CONTINUATION_VALIDATED sha256={continuation_digest} "
            f"records={len(display_continuation)}",
            flush=True,
        )
    available_ticks = {
        item.tick
        for item in (render_prefix or []) + (display_continuation or [])
    }
    if args.stop_after_tick is not None:
        if args.stop_after_tick not in available_ticks:
            parser.error(
                "--stop-after-tick must match an exact record boundary in the "
                "validated owner render data"
            )
    if args.skip_owner_tick:
        unavailable_skip_ticks = sorted(
            set(args.skip_owner_tick) - available_ticks
        )
        if unavailable_skip_ticks:
            parser.error(
                "--skip-owner-tick values must match exact record boundaries in "
                f"the validated owner render data: {unavailable_skip_ticks}"
            )
        if args.stop_after_tick in set(args.skip_owner_tick):
            parser.error("--stop-after-tick cannot also be skipped")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()

    def record(direction: str, data: bytes) -> None:
        line = f"{stamp()} {direction} {len(data)} {data.hex(' ').upper()}\n"
        with log_lock:
            with args.log.open("a", encoding="ascii", newline="") as stream:
                stream.write(line)
        print(line, end="", flush=True)

    def send_safe_discovery(sock: socket.socket, tracker: DataFrameTracker) -> None:
        for command, command_payload, description in SAFE_DISCOVERY_SEQUENCE:
            discovery_frame = build_fixed_frame(command, command_payload)
            previous = tracker.snapshot(command)
            send_serial_frame(
                sock, discovery_frame,
                baud=args.serial_baud, chunk_size=args.serial_chunk_size,
            )
            direction = f"TX_TO_GUEST_{description.replace(' ', '_').upper()}"
            record(direction, discovery_frame)
            if not tracker.wait_after(command, previous, args.command_ack_timeout):
                raise TimeoutError(f"data-frame timeout after {direction}")
        print("SAFE_DISCOVERY_SENT; monitoring guest replies", flush=True)

    def send_with_ack(
        sock: socket.socket, tracker: AckTracker, frame: bytes, direction: str
    ) -> None:
        previous_ack, previous_nak = tracker.response_snapshot()
        send_serial_frame(
            sock, frame, baud=args.serial_baud, chunk_size=args.serial_chunk_size
        )
        record(direction, frame)
        response = tracker.wait_response_after(
            previous_ack, previous_nak, args.command_ack_timeout
        )
        if response is None:
            raise TimeoutError(f"ACK timeout after {direction}")
        response_kind, error_id = response
        if response_kind == "nak":
            error_name = NAK_ERROR_NAMES.get(error_id, "UNKNOWN_ERROR")
            raise RuntimeError(
                f"guest NAK after {direction}: error={error_id} {error_name}"
            )

    def send_safe_startup_texts(sock: socket.socket, tracker: AckTracker) -> None:
        for delay, message, clear, red, green, blue in SAFE_STARTUP_TEXT_SEQUENCE:
            time.sleep(delay)
            text_payload = build_startup_text_payload(
                message, clear=clear, red=red, green=green, blue=blue
            )
            text_frame = build_fixed_frame(VID_COM_STARTUPTXT, text_payload)
            send_with_ack(sock, tracker, text_frame, "TX_TO_GUEST_STARTUPTXT")
        print("SAFE_STARTUP_TEXTS_SENT; monitoring guest replies", flush=True)

    def send_owner_records(
        sock: socket.socket,
        tracker: AckTracker,
        records,
        start_tick: int,
        direction: str,
    ) -> bool:
        if not records:
            return False
        selected_records, stops_here = owner_records_through_tick(
            records, args.stop_after_tick
        )
        for skipped_record in selected_records:
            if skipped_record.tick in set(args.skip_owner_tick):
                print(
                    f"OWNER_RECORD_SKIPPED tick={skipped_record.tick} "
                    f"command={skipped_record.command}",
                    flush=True,
                )
        selected_records = owner_records_without_ticks(
            selected_records, args.skip_owner_tick
        )
        prior_tick = start_tick
        for record_item in selected_records:
            if record_item.tick in args.pause_before_tick:
                print(
                    f"PAUSED_BEFORE_TICK tick={record_item.tick} command={record_item.command}; "
                    "press Enter to send",
                    flush=True,
                )
                input()
            delay = max(0.0, min((record_item.tick - prior_tick) / 1000.0, 5.0))
            if delay:
                time.sleep(delay)
            payload = bytes.fromhex(record_item.payload_hex)
            frame = build_fixed_frame(record_item.command, payload)
            payload_description = describe_evidenced_payload(
                record_item.command, payload
            )
            if payload_description:
                print(
                    f"OWNER_RECORD tick={record_item.tick} command={record_item.command} "
                    f"{payload_description}",
                    flush=True,
                )
            send_with_ack(
                sock,
                tracker,
                frame,
                f"TX_TO_GUEST_{direction}_CMD_{record_item.command}",
            )
            if record_item.tick in args.pause_after_tick:
                print(
                    f"PAUSED_AFTER_TICK tick={record_item.tick} command={record_item.command}; "
                    "press Enter to continue",
                    flush=True,
                )
                input()
            prior_tick = record_item.tick
        print(f"{direction}_SENT records={len(selected_records)}", flush=True)
        if stops_here:
            print(
                f"STOPPED_AFTER_TICK tick={args.stop_after_tick}; "
                "monitoring guest without later owner records",
                flush=True,
            )
        return stops_here

    def send_owner_render_data(sock: socket.socket, tracker: AckTracker) -> None:
        stopped = send_owner_records(
            sock,
            tracker,
            render_prefix,
            SAFE_RENDER_START_TICK,
            "OWNER_RENDER_PREFIX",
        )
        if stopped:
            return
        send_owner_records(
            sock,
            tracker,
            display_continuation,
            SAFE_CONTINUATION_START_TICK,
            "OWNER_DISPLAY_CONTINUATION",
        )

    def run_session(sock: socket.socket, stop: threading.Event) -> None:
        init_ack = threading.Event()
        ack_tracker = AckTracker()
        data_frame_tracker = DataFrameTracker()
        session = EmulatorSession()
        session.transition(EmulatorPhase.WAITING_FOR_INIT)

        def receiver() -> None:
            while not stop.is_set():
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if not stop.is_set():
                        print(f"RX_ERROR {exc}", flush=True)
                    return
                if not data:
                    print("COM3_DISCONNECTED", flush=True)
                    return
                record("RX_FROM_GUEST", data)
                for command, payload in data_frame_tracker.feed(data):
                    print(
                        f"GUEST_DATA_FRAME command={command} payload_length={len(payload)}",
                        flush=True,
                    )
                previous_nak, _ = ack_tracker.last_nak()
                if ack_tracker.feed(data):
                    init_ack.set()
                nak_count, nak_error = ack_tracker.last_nak()
                if nak_count > previous_nak:
                    if session.phase != EmulatorPhase.REJECTED:
                        session.transition(EmulatorPhase.REJECTED)
                    print(
                        f"GUEST_NAK error={nak_error} "
                        f"name={NAK_ERROR_NAMES.get(nak_error, 'UNKNOWN_ERROR')}",
                        flush=True,
                    )

        thread = threading.Thread(target=receiver, name="vidcom-rx", daemon=True)
        thread.start()
        print(f"COM3_CONNECTED {args.host}:{args.port}", flush=True)
        print(
            f"SERIAL_PACING baud={args.serial_baud} chunk={args.serial_chunk_size} "
            f"format=8N1",
            flush=True,
        )
        try:
            if args.auto:
                frame = build_initvideo_frame()
                payload = frame[4:-1]
                year, month, day, hour, minute = struct.unpack("<HBBBB", payload[20:26])
                attempt = 0
                print(
                    f"AUTO_INIT_ARMED database_time={year:04d}-{month:02d}-{day:02d}T"
                    f"{hour:02d}:{minute:02d} initial_delay={args.initial_init_delay:g}s "
                    f"retry={args.init_retry_seconds:g}s",
                    flush=True,
                )
                if args.initial_init_delay:
                    print("AUTO_INIT_WAITING_FOR_GUEST_BOOT", flush=True)
                    init_ack.wait(args.initial_init_delay)
                while (
                    thread.is_alive()
                    and not init_ack.is_set()
                    and attempt < args.max_init_attempts
                ):
                    attempt += 1
                    send_serial_frame(
                        sock,
                        frame,
                        baud=args.serial_baud,
                        chunk_size=args.serial_chunk_size,
                    )
                    record(f"TX_TO_GUEST_INIT_ATTEMPT_{attempt}", frame)
                    init_ack.wait(args.init_retry_seconds)
                if not init_ack.is_set():
                    print(
                        f"INITVIDEO_ACK_TIMEOUT attempts={attempt}; stopping without "
                        "sending post-init commands",
                        flush=True,
                    )
                    return
                print(f"INITVIDEO_ACK_CONFIRMED attempt={attempt}", flush=True)
                session.transition(EmulatorPhase.INITIALIZED)
                time.sleep(0.25)
                session.transition(EmulatorPhase.DISCOVERY)
                send_safe_discovery(sock, data_frame_tracker)
                session.transition(EmulatorPhase.STARTUP_TEXT)
                send_safe_startup_texts(sock, ack_tracker)
                session.transition(EmulatorPhase.RENDERING)
                send_owner_render_data(sock, ack_tracker)
                session.transition(EmulatorPhase.INTERACTIVE)
                print("EMULATOR_PHASE interactive; monitoring guest input", flush=True)
                while thread.is_alive():
                    thread.join(1.0)
                return

            if args.resume_owner_render:
                print(
                    "RESUME_OWNER_RENDER: sending evidenced safe discovery, startup "
                    "text and owner render prefix",
                    flush=True,
                )
                # Resume mode starts after INIT, so reconstruct the same explicit
                # database-side state before emitting any command.
                session.transition(EmulatorPhase.INITIALIZED)
                session.transition(EmulatorPhase.DISCOVERY)
                send_safe_discovery(sock, data_frame_tracker)
                session.transition(EmulatorPhase.STARTUP_TEXT)
                send_safe_startup_texts(sock, ack_tracker)
                session.transition(EmulatorPhase.RENDERING)
                send_owner_render_data(sock, ack_tracker)
                session.transition(EmulatorPhase.INTERACTIVE)
                while thread.is_alive():
                    thread.join(1.0)
                return

            if args.resume_owner_continuation_at is not None:
                selected = [
                    item for item in display_continuation
                    if item.tick >= args.resume_owner_continuation_at
                ]
                if not selected or selected[0].tick != args.resume_owner_continuation_at:
                    raise ValueError(
                        "resume tick is not an exact record boundary in the validated continuation"
                    )
                session.transition(EmulatorPhase.INITIALIZED)
                session.transition(EmulatorPhase.DISCOVERY)
                session.transition(EmulatorPhase.STARTUP_TEXT)
                session.transition(EmulatorPhase.RENDERING)
                print(
                    f"RESUME_OWNER_CONTINUATION tick={selected[0].tick} "
                    f"records={len(selected)}",
                    flush=True,
                )
                send_owner_records(
                    sock,
                    ack_tracker,
                    selected,
                    selected[0].tick,
                    "OWNER_DISPLAY_CONTINUATION_RESUME",
                )
                session.transition(EmulatorPhase.INTERACTIVE)
                print("EMULATOR_PHASE interactive; monitoring guest input", flush=True)
                while thread.is_alive():
                    thread.join(1.0)
                return

            if args.resume_safe:
                print(
                    "RESUME_SAFE_READY: press Enter to send only evidenced "
                    "SYSTEMINFO/GRAPHICCHECKSUM queries",
                    flush=True,
                )
                input()
                session.transition(EmulatorPhase.INITIALIZED)
                session.transition(EmulatorPhase.DISCOVERY)
                send_safe_discovery(sock, data_frame_tracker)
                while thread.is_alive():
                    thread.join(1.0)
                return

            print("READY: press Enter once the guest shows 'Waiting for init video'", flush=True)
            input()
            frame = build_initvideo_frame()
            send_serial_frame(
                sock, frame, baud=args.serial_baud, chunk_size=args.serial_chunk_size
            )
            record("TX_TO_GUEST", frame)
            payload = frame[4:-1]
            year, month, day, hour, minute = struct.unpack("<HBBBB", payload[20:26])
            print(
                f"INITVIDEO_SENT database_time={year:04d}-{month:02d}-{day:02d}T"
                f"{hour:02d}:{minute:02d}; monitoring guest replies",
                flush=True,
            )
            print(
                "SAFE_DISCOVERY_READY: type 'safe' and Enter to send only the "
                "evidenced SYSTEMINFO/GRAPHICCHECKSUM queries, or leave it waiting",
                flush=True,
            )
            choice = input().strip().lower()
            if choice == "safe":
                session.transition(EmulatorPhase.INITIALIZED)
                session.transition(EmulatorPhase.DISCOVERY)
                send_safe_discovery(sock, data_frame_tracker)
            elif choice:
                print(f"IGNORED_UNKNOWN_OPERATOR_COMMAND {choice!r}", flush=True)
            while thread.is_alive():
                thread.join(1.0)
        except EOFError:
            raise KeyboardInterrupt
        finally:
            stop.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    try:
        while True:
            print("WAITING_FOR_QEMU_COM3 (Ctrl+C to stop)", flush=True)
            sock = connect(args.host, args.port, args.connect_timeout)
            run_session(sock, threading.Event())
            print("RECONNECTING_AFTER_GUEST_CLOSE", flush=True)
    except (KeyboardInterrupt, OSError) as exc:
        if isinstance(exc, OSError):
            print(f"CONNECT_ERROR {exc}", flush=True)
        pass
    instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
