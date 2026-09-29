#!/usr/bin/env python3
"""Controlled QEMU/m68020 loader harness using the built-in GDB stub.

The owner loader is opened by QEMU read-only.  A flat 16 MiB RAM map covers
both normal memory and the observed 24-bit MMIO addresses.  This first-stage
harness deliberately does not forge the missing D3 transform seed or bypass
the loader validation path; it single-steps until a requested address/limit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from m68k_database_transform import transform_database


DEFAULT_QEMU = Path(r"C:\Program Files\qemu\qemu-system-m68k.exe")
MAX_LOADER_SIZE = 1024 * 1024
MAX_DATABASE_SIZE = 64 * 1024 * 1024
SYNC_WAIT = b"\x1bSYNCSYNCWAITGO\n"
REG_D3 = 3
REG_D6 = 6
REG_PC = 17


def qemu_creation_flags() -> int:
    """Keep every m68k QEMU helper below normal priority on Windows."""
    if sys.platform == "win32":
        return subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return 0


class RspClient:
    def __init__(self, sock: socket.socket, timeout: float | None = 5.0) -> None:
        self.sock = sock
        self.sock.settimeout(timeout)
        self._receive_buffer = b""
        self._receive_offset = 0

    @staticmethod
    def _checksum(payload: bytes) -> bytes:
        return f"{sum(payload) & 0xff:02x}".encode("ascii")

    def _read_byte(self) -> bytes:
        if self._receive_offset >= len(self._receive_buffer):
            self._receive_buffer = self.sock.recv(4096)
            self._receive_offset = 0
            if not self._receive_buffer:
                raise ConnectionError("QEMU GDB stub closed")
        offset = self._receive_offset
        self._receive_offset += 1
        return self._receive_buffer[offset:offset + 1]

    def _read_response(self) -> str:
        first = self._read_byte()
        if first == b"+":
            first = self._read_byte()
        while first != b"$":
            first = self._read_byte()
        response = bytearray()
        while True:
            byte = self._read_byte()
            if byte == b"#":
                break
            response.extend(byte)
        received_checksum = self._read_byte() + self._read_byte()
        if received_checksum.lower() != self._checksum(response):
            self.sock.sendall(b"-")
            raise ValueError("invalid RSP response checksum")
        self.sock.sendall(b"+")
        return response.decode("ascii")

    def command(self, text: str) -> str:
        payload = text.encode("ascii")
        self.sock.sendall(b"$" + payload + b"#" + self._checksum(payload))
        return self._read_response()

    def interrupt(self) -> str:
        """Interrupt a running target and read its unsolicited stop reply."""
        self.sock.sendall(b"\x03")
        return self._read_response()

    def set_timeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def write_register_u32(self, register: int, value: int) -> None:
        response = self.command(f"P{register:x}={value:08x}")
        if response != "OK":
            raise RuntimeError(f"register write failed: {response}")

    def read_register_u32(self, register: int) -> int:
        response = self.command(f"p{register:x}")
        return int.from_bytes(bytes.fromhex(response), "big")

    def read_registers_u32(self) -> tuple[int, ...]:
        """Read the m68020's D0-D7, A0-A7, SR and PC in one RSP exchange."""
        response = self.command("g")
        if len(response) < 18 * 8:
            raise RuntimeError(f"short m68k register reply: {response!r}")
        return tuple(
            int(response[index:index + 8], 16)
            for index in range(0, 18 * 8, 8)
        )

    def write_memory(self, address: int, data: bytes) -> None:
        response = self.command(f"M{address:x},{len(data):x}:{data.hex()}")
        if response != "OK":
            raise RuntimeError(f"memory write failed: {response}")

    def read_memory(self, address: int, length: int) -> bytes:
        return bytes.fromhex(self.command(f"m{address:x},{length:x}"))

    def step(self) -> str:
        return self.command("s")


def connect_rsp(port: int, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            # RSP exchanges many tiny command/ack packets. Avoid Nagle
            # coalescing them with a delayed TCP acknowledgement.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def run_harness(
    qemu: Path,
    loader: Path,
    expected_sha256: str,
    port: int,
    steps: int,
    stop_pc: int | None,
    d3: int,
    database: Path | None = None,
    expected_database_sha256: str | None = None,
    probe_transform_bytes: int = 0,
) -> dict:
    data = loader.read_bytes()
    if len(data) > MAX_LOADER_SIZE:
        raise ValueError("loader exceeds safety size limit")
    digest = hashlib.sha256(data).hexdigest().upper()
    expected = expected_sha256.replace(" ", "").upper()
    if digest != expected:
        raise ValueError(f"loader SHA-256 mismatch: expected {expected}, got {digest}")

    database_digest = None
    serial_input = deque()
    if database is not None:
        database_data = database.read_bytes()
        if len(database_data) > MAX_DATABASE_SIZE:
            raise ValueError("database exceeds safety size limit")
        database_digest = hashlib.sha256(database_data).hexdigest().upper()
        database_expected = (expected_database_sha256 or "").replace(" ", "").upper()
        if database_digest != database_expected:
            raise ValueError(
                f"database SHA-256 mismatch: expected {database_expected}, "
                f"got {database_digest}"
            )
        if len(database_data) < 0x100:
            raise ValueError("database is shorter than the loader's raw header phase")
        # Eight zero transport bytes are a native valid header: the eighth
        # byte equals the additive checksum of the preceding seven, and the
        # loader intentionally skips the optional hardware setup when all are
        # zero.  Database bytes themselves are supplied unchanged.
        serial_input.extend(
            SYNC_WAIT + (b"\0" * 8)
            + database_data[:0x100 + probe_transform_bytes]
        )

    loader_device = (
        f"loader,file={loader.resolve()},addr=0x400,force-raw=on,cpu-num=0"
    )
    command = [
        str(qemu), "-machine", "none", "-cpu", "m68020", "-m", "16M",
        "-device", loader_device, "-display", "none", "-monitor", "none",
        "-serial", "none", "-S", "-gdb", f"tcp:127.0.0.1:{port}",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=qemu_creation_flags(),
    )
    try:
        with connect_rsp(port, 10.0) as sock:
            rsp = RspClient(sock)
            rsp.command("?")
            # QEMU maps a flat 16 MiB so the 24-bit MMIO window is reachable,
            # but the original 2MB loader must see 2 MiB physical RAM.  This
            # keeps its stack at 0x1FFF80 instead of overlapping 0xFFxxxx MMIO.
            rsp.write_memory(0, (2 * 1024 * 1024).to_bytes(4, "big"))
            # Observed UART channel A transmitter-ready status.  The loader
            # polls bit zero at 0xFFFC0C before writing a byte to 0xFFFC0F.
            rsp.write_memory(0xFFFC0C, b"\x01")
            rsp.write_register_u32(REG_D3, d3)
            rsp.write_register_u32(REG_PC, 0x040E)
            if rsp.read_memory(0x040E, 6) != bytes.fromhex("4E F9 00 00 0C C8"):
                raise RuntimeError("loader entry bytes do not match the validated JMP")

            trace = []
            uart_tx = []
            status_delays_shortened = 0
            uart_rx_count = 0
            rx_byte_armed = False
            transform_probe_complete = False
            status_read_pcs = {0x073C, 0x07AA, 0x0862, 0x0902, 0x09D8}
            after_data_read_pcs = {0x076A, 0x07D2, 0x088A, 0x092A, 0x0A00}
            for index in range(steps + 1):
                pc = rsp.read_register_u32(REG_PC)
                if index < 16 or pc in (0x040E, 0x0CC8, 0x06A4, 0x072C, 0x0738):
                    trace.append({"step": index, "pc": f"{pc:08X}"})
                if stop_pc is not None and pc == stop_pc:
                    break
                if (
                    probe_transform_bytes
                    and pc == 0x0A1E
                    and uart_rx_count >= len(SYNC_WAIT) + 8 + 0x100 + probe_transform_bytes
                ):
                    transform_probe_complete = True
                    break
                if pc == 0x0CAA:
                    uart_tx.append(rsp.read_memory(0xFFFC0F, 1)[0])
                if pc in status_read_pcs and not rx_byte_armed and serial_input:
                    rsp.write_memory(0xFFFC0F, bytes([serial_input[0]]))
                    rsp.write_memory(0xFFFC0D, b"\x40")
                    rx_byte_armed = True
                if pc in after_data_read_pcs and rx_byte_armed:
                    serial_input.popleft()
                    uart_rx_count += 1
                    rsp.write_memory(0xFFFC0D, b"\x00")
                    rx_byte_armed = False
                if pc == 0x0C50:
                    # c18 is a visible status/blink delay, not a validation
                    # decision. Shorten it while retaining every branch.
                    rsp.write_register_u32(REG_D6, 0x3A99)
                    status_delays_shortened += 1
                if index == steps:
                    break
                stop_reply = rsp.step()
                if not stop_reply.startswith(("S", "T")):
                    raise RuntimeError(f"unexpected single-step reply {stop_reply!r}")
            final_pc = rsp.read_register_u32(REG_PC)
            report = {
                "schema": "m90-m68k-loader-harness-v1",
                "loader": str(loader.resolve()),
                "loader_sha256": digest,
                "cpu": "m68020",
                "address_space_bytes": 16 * 1024 * 1024,
                "reported_ram_bytes": 2 * 1024 * 1024,
                "entry_pc": "0000040E",
                "d3": f"{d3:08X}",
                "steps_executed": index,
                "final_pc": f"{final_pc:08X}",
                "stop_pc_reached": stop_pc is not None and final_pc == stop_pc,
                "uart_tx_hex": bytes(uart_tx).hex(" ").upper(),
                "uart_rx_bytes": uart_rx_count,
                "uart_rx_remaining": len(serial_input),
                "status_delays_shortened": status_delays_shortened,
                "trace": trace,
                "safety": "No validation branch was patched and no transform seed was inferred; only the c18 status blink delay was shortened.",
            }
            if database is not None:
                report["database"] = str(database.resolve())
                report["database_sha256"] = database_digest
                report["database_header_bytes_supplied"] = 0x100
            if probe_transform_bytes:
                actual = rsp.read_memory(0x1100, probe_transform_bytes)
                expected_probe = transform_database(database_data, d3)[
                    0x100:0x100 + probe_transform_bytes
                ]
                report["transform_probe_bytes"] = probe_transform_bytes
                report["transform_probe_actual_hex"] = actual.hex(" ").upper()
                report["transform_probe_expected_hex"] = expected_probe.hex(" ").upper()
                report["transform_probe_matches"] = actual == expected_probe
                report["transform_probe_complete"] = transform_probe_complete
            report["goal_reached"] = report["stop_pc_reached"] or transform_probe_complete
            return report
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("loader", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--qemu", type=Path, default=DEFAULT_QEMU)
    parser.add_argument("--gdb-port", type=int, default=1235)
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument("--stop-pc", type=parse_int, default=0x072C)
    parser.add_argument("--d3", type=parse_int, default=0)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--expected-database-sha256")
    parser.add_argument("--probe-transform-bytes", type=int, default=0)
    args = parser.parse_args()
    if args.steps < 0 or args.steps > 100000:
        parser.error("--steps must be between 0 and 100000")
    if bool(args.database) != bool(args.expected_database_sha256):
        parser.error("--database and --expected-database-sha256 are required together")
    if not 0 <= args.probe_transform_bytes <= 4096:
        parser.error("--probe-transform-bytes must be between 0 and 4096")
    if args.probe_transform_bytes and not args.database:
        parser.error("--probe-transform-bytes requires --database")
    report = run_harness(
        args.qemu, args.loader, args.expected_sha256, args.gdb_port,
        args.steps, args.stop_pc, args.d3,
        args.database, args.expected_database_sha256,
        args.probe_transform_bytes,
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["goal_reached"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
