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
import re
import socket
import struct
import subprocess
import sys
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

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
HEX_TEXT = re.compile(r"[0-9a-fA-F]*")


class M68kRegisterSnapshot(NamedTuple):
    """Explicit halted-target context, retaining every byte of the RSP g reply."""

    raw_hex: str
    core_u32: tuple[int, ...]


def qemu_creation_flags() -> int:
    """Keep every m68k QEMU helper below normal priority on Windows."""
    if sys.platform == "win32":
        return subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return 0


class RspClient:
    """GDB remote client for QEMU's stub, tuned for many small exchanges.

    Replies are not acknowledged: QEMU drops a pending reply as soon as the
    next packet starts. Writes that only answer "OK" (M, G, P, Z, z) are
    queued and sent in one TCP write with the next exchange that needs a
    reply; QEMU executes packets in order, and every queued reply is checked
    before that reply is returned. While the CPU is halted, read memory and
    the register block are cached; any other command (continue, step,
    interrupt) may run the target and drops the cache.
    """

    POSTED_COMMANDS = frozenset("MGPZz")
    POSTED_REPLY_TIMEOUT = 5.0
    WRITE_MERGE_GAP = 256
    READ_MERGE_GAP = 1024
    MAX_READ = 4096
    MAX_WRITE = 4096

    def __init__(self, sock: socket.socket, timeout: float | None = 5.0) -> None:
        self.sock = sock
        self._timeout = timeout
        self.sock.settimeout(timeout)
        self._receive_buffer = b""
        self._receive_offset = 0
        self._posted: list[str] = []
        self._memory: list[tuple[int, bytearray]] = []
        self._registers: str | None = None
        self._last_registers: str | None = None
        self._decoded_registers: M68kRegisterSnapshot | None = None

    @staticmethod
    def _checksum(payload: bytes) -> bytes:
        return f"{sum(payload) & 0xff:02x}".encode("ascii")

    @classmethod
    def _packet(cls, text: str) -> bytes:
        payload = text.encode("ascii")
        return b"$" + payload + b"#" + cls._checksum(payload)

    def _receive_more(self) -> None:
        chunk = self.sock.recv(4096)
        if not chunk:
            raise ConnectionError("QEMU GDB stub closed")
        self._receive_buffer = self._receive_buffer[self._receive_offset:] + chunk
        self._receive_offset = 0

    def _read_response(self) -> str:
        # Search the buffered stream instead of reading byte by byte; a
        # register block or memory range is several hundred characters.
        while True:
            buffer, offset = self._receive_buffer, self._receive_offset
            start = buffer.find(b"$", offset)
            if start < 0:
                self._receive_offset = len(buffer)  # acks and noise before '$'
            else:
                self._receive_offset = start
                end = buffer.find(b"#", start + 1)
                if end >= 0 and len(buffer) >= end + 3:
                    break
            self._receive_more()
        response = buffer[start + 1:end]
        received_checksum = buffer[end + 1:end + 3]
        self._receive_offset = end + 3
        if received_checksum.lower() != self._checksum(response):
            self.sock.sendall(b"-")
            raise ValueError("invalid RSP response checksum")
        return response.decode("ascii")

    def _coalesce_posted(self, posted: list[str]) -> list[str]:
        """The fewest write packets that leave the same halted state.

        QEMU's cost is per packet, not per byte. Queued memory writes are
        merged into contiguous ranges (small gaps filled from the cache), and
        a register write followed by a complete G block is dropped. Memory,
        registers and breakpoints are independent, so memory goes first;
        register and breakpoint packets keep their order.
        """
        if len(posted) < 2:
            return posted
        last_block = max((index for index, text in enumerate(posted) if text[:1] == "G"), default=-1)
        runs: list[tuple[int, bytearray]] = []  # disjoint, in write order
        others = []
        for index, text in enumerate(posted):
            kind = text[:1]
            if kind in "GP" and index < last_block:
                continue
            arguments = self._memory_arguments(text) if kind == "M" else None
            if arguments is None:
                others.append(text)
                continue
            address, data = arguments[0], bytearray.fromhex(text.split(":", 1)[1])
            # Fold every earlier run this write overlaps or touches into it;
            # the later write wins on overlapping bytes.
            touching = [run for run in runs
                        if run[0] <= address + len(data) and address <= run[0] + len(run[1])]
            if len(touching) == 1 and touching[0][0] + len(touching[0][1]) == address:
                touching[0][1].extend(data)  # sequential load: append in place
                continue
            for start, earlier in touching:
                low = min(start, address)
                merged = bytearray(max(start + len(earlier), address + len(data)) - low)
                merged[start - low:start - low + len(earlier)] = earlier
                merged[address - low:address - low + len(data)] = data
                address, data = low, merged
            runs = [run for run in runs if all(run is not other for other in touching)]
            runs.append((address, data))
        packets = []
        runs.sort(key=lambda run: run[0])
        index = 0
        while index < len(runs):
            start, data = runs[index]
            data = bytearray(data)
            index += 1
            while index < len(runs):
                following, more = runs[index]
                gap = following - (start + len(data))
                if following + len(more) - start > self.MAX_WRITE or gap > self.WRITE_MERGE_GAP:
                    break
                filler = self._cached_memory(start + len(data), gap) if gap else b""
                if filler is None:
                    break
                data += filler + more
                index += 1
            # QEMU accepts packets up to about 20 KB.
            for offset in range(0, len(data), self.MAX_WRITE):
                chunk = data[offset:offset + self.MAX_WRITE]
                packets.append(f"M{start + offset:x},{len(chunk):x}:{chunk.hex()}")
        return packets + others

    def _exchange(self, texts: list[str]) -> list[str]:
        """Send queued writes plus these packets at once; return their replies."""
        posted, self._posted = self._coalesce_posted(self._posted), []
        outgoing = b"".join(self._packet(text) for text in posted + texts)
        if outgoing:
            self.sock.sendall(outgoing)
        if posted:
            # Write replies follow at once, even before a short run slice.
            short = self._timeout is not None and self._timeout < self.POSTED_REPLY_TIMEOUT
            if short:
                self.sock.settimeout(self.POSTED_REPLY_TIMEOUT)
            try:
                for text in posted:
                    reply = self._read_response()
                    if reply != "OK":
                        raise RuntimeError(f"RSP write {text[:32]!r} failed: {reply}")
            finally:
                if short:
                    self.sock.settimeout(self._timeout)
        return [self._read_response() for _ in texts]

    def _cached_memory(self, address: int, length: int) -> bytes | None:
        for start, data in self._memory:
            if start <= address and address + length <= start + len(data):
                return bytes(data[address - start:address - start + length])
        return None

    def _remember_memory(self, address: int, reply: str, length: int) -> None:
        try:
            data = bytes.fromhex(reply)
        except ValueError:
            return
        if len(data) == length:
            self._memory.append((address, bytearray(data)))

    def _remember_registers(self, reply: str) -> None:
        if len(reply) >= 18 * 8 and HEX_TEXT.fullmatch(reply, 0, 18 * 8):
            self._registers = self._last_registers = reply

    def last_known_register(self, register: int) -> int | None:
        """A core register as last read or written, possibly before a resume.

        Only a hint for choosing what to prefetch; never a current value.
        """
        if self._last_registers is None or not 0 <= register < 18:
            return None
        return int(self._last_registers[register * 8:register * 8 + 8], 16)

    def _apply_memory_write(self, address: int, data: bytes) -> None:
        for start, cached in self._memory:
            low = max(start, address)
            high = min(start + len(cached), address + len(data))
            if low < high:
                cached[low - start:high - start] = data[low - address:high - address]

    @staticmethod
    def _memory_arguments(text: str) -> tuple[int, int] | None:
        try:
            address, length = text[1:].split(":", 1)[0].split(",", 1)
            return int(address, 16), int(length, 16)
        except ValueError:
            return None

    def command(self, text: str) -> str:
        kind = text[:1]
        if kind in self.POSTED_COMMANDS:
            if kind == "M":
                arguments = self._memory_arguments(text)
                if arguments is None:
                    self._memory.clear()
                else:
                    self._apply_memory_write(arguments[0], bytes.fromhex(text.split(":", 1)[1]))
            elif kind == "G":
                self._registers = self._last_registers = text[1:]
            elif kind == "P":
                self._registers = None
            self._posted.append(text)
            return "OK"
        if kind == "g":
            if self._registers is not None:
                return self._registers
            reply = self._exchange([text])[0]
            self._remember_registers(reply)
            return reply
        if kind == "p" and self._registers is not None:
            try:
                register = int(text[1:], 16)
            except ValueError:
                register = -1
            if 0 <= register < 18 and len(self._registers) >= 18 * 8:
                return self._registers[register * 8:register * 8 + 8]
        if kind == "m":
            arguments = self._memory_arguments(text)
            if arguments is not None:
                cached = self._cached_memory(*arguments)
                if cached is not None:
                    return cached.hex()
                reply = self._exchange([text])[0]
                self._remember_memory(arguments[0], reply, arguments[1])
                return reply
        try:
            return self._exchange([text])[0]
        finally:
            if kind != "p":
                # Continue, step and every other request may change the
                # target. Queued writes were merged with the cache already.
                self._memory.clear()
                self._registers = None

    def prefetch(self, reads: list[tuple[int, int]], *, registers: bool = False) -> None:
        """Fetch several memory ranges (and the register block) in one round trip.

        Nearby ranges are read as one packet: extra bytes are cheap, packets
        are not.
        """
        merged: list[tuple[int, int]] = []
        for address, length in sorted(read for read in reads if self._cached_memory(*read) is None):
            if merged:
                start, size = merged[-1]
                end = max(start + size, address + length)
                if address <= start + size + self.READ_MERGE_GAP and end - start <= self.MAX_READ:
                    merged[-1] = (start, end - start)
                    continue
            merged.append((address, length))
        texts = ["g"] if registers and self._registers is None else []
        texts += [f"m{address:x},{length:x}" for address, length in merged]
        if not texts:
            return
        for text, reply in zip(texts, self._exchange(texts)):
            if text == "g":
                self._remember_registers(reply)
            else:
                address, length = self._memory_arguments(text)
                self._remember_memory(address, reply, length)

    def flush(self) -> None:
        """Send queued writes now and check their replies."""
        self._exchange([])

    def interrupt(self) -> str:
        """Interrupt a running target and read its unsolicited stop reply.

        A running QEMU ignores every packet except the interrupt byte, so
        queued writes stay queued for the next exchange.
        """
        self._memory.clear()
        self._registers = None
        self.sock.sendall(b"\x03")
        return self._read_response()

    def set_timeout(self, timeout: float | None) -> None:
        self._timeout = timeout
        self.sock.settimeout(timeout)

    def write_register_u32(self, register: int, value: int) -> None:
        response = self.command(f"P{register:x}={value:08x}")
        if response != "OK":
            raise RuntimeError(f"register write failed: {response}")

    def read_register_u32(self, register: int) -> int:
        response = self.command(f"p{register:x}")
        return int.from_bytes(bytes.fromhex(response), "big")

    @staticmethod
    def _decode_register_snapshot(response: str) -> M68kRegisterSnapshot:
        if len(response) < 18 * 8:
            raise RuntimeError(f"short m68k register reply: {response!r}")
        # G must contain the complete register block, not just the 18 core
        # words. Extra registers can have other widths and remain opaque.
        # Do not write an unavailable ('xx') register as a guessed value.
        if len(response) % 2 or not HEX_TEXT.fullmatch(response):
            raise RuntimeError("invalid or unavailable m68k register reply")
        core = struct.unpack(">18I", bytes.fromhex(response[:18 * 8]))
        return M68kRegisterSnapshot(response, core)

    def read_register_snapshot(self) -> M68kRegisterSnapshot:
        """Read an explicit complete g block while the target is halted."""
        raw = self.command("g")
        decoded = self._decoded_registers
        if decoded is None or decoded.raw_hex != raw:
            decoded = self._decoded_registers = self._decode_register_snapshot(raw)
        return decoded

    def read_registers_u32(self) -> tuple[int, ...]:
        """Read the m68020's D0-D7, A0-A7, SR and PC in one RSP exchange."""
        # Core-only readers need not reject unavailable optional registers.
        # A snapshot intended for a complete G write uses stricter validation.
        response = self.command("g")
        if len(response) < 18 * 8:
            raise RuntimeError(f"short m68k register reply: {response!r}")
        return tuple(
            int(response[index:index + 8], 16)
            for index in range(0, 18 * 8, 8)
        )

    def write_registers_u32(
        self, updates: Mapping[int, int], *, snapshot: M68kRegisterSnapshot
    ) -> M68kRegisterSnapshot:
        """Apply core-register updates with one complete G packet.

        The caller must supply a current snapshot of the halted target. Do
        not reuse it after a resume, step, interrupt, or unrelated register
        write. No implicit snapshot cache or extra g round trip is used.
        Memory writes while the target remains halted do not invalidate it.
        The returned snapshot may serve subsequent writes at that same stop.
        """
        if not isinstance(snapshot, M68kRegisterSnapshot):
            raise TypeError("an explicit m68k register snapshot is required")
        if snapshot is not self._decoded_registers:
            decoded = self._decode_register_snapshot(snapshot.raw_hex)
            if decoded.core_u32 != snapshot.core_u32:
                raise ValueError("m68k register snapshot core does not match its raw block")
        raw = snapshot.raw_hex
        for register, value in updates.items():
            if type(register) is not int or not 0 <= register < 18:
                raise ValueError("m68k bulk writes require a core register index 0..17")
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("m68k bulk register values must be unsigned 32-bit integers")
            start = register * 8
            raw = raw[:start] + f"{value:08x}" + raw[start + 8:]
        if not updates:
            return snapshot
        response = self.command("G" + raw)
        if response != "OK":
            raise RuntimeError(f"bulk register write failed: {response}")
        written = self._decode_register_snapshot(raw)
        self._decoded_registers = written
        return written

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
