#!/usr/bin/env python3
"""Run the owner database firmware on QEMU/m68020 and bridge its UART to COM3.

The XP QEMU instance owns a TCP server for guest COM3. This process connects as
its peer, runs the checksum-valid database firmware on QEMU's m68020 CPU and
maps the firmware's original UART MMIO accesses to that socket. It does not
replay recorded game commands.
"""

from __future__ import annotations

import argparse
import datetime as dt
import struct
import hashlib
import math
import queue
import select
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from m68k_qemu_harness import DEFAULT_QEMU, REG_D3, REG_PC, RspClient, M68kRegisterSnapshot, connect_rsp
from cabinet_controls import (
    CabinetControlServer, KEY_IDS, KEY_TABLE_BASE, KEY_TABLE_PROFILES,
    KEY_TABLE_STRIDE, KEY_CURRENT_BASE, KEY_EVENT_BASE, format_tablet_packet, key_location,
)
from owner_config_runtime import CONFIG_CLEAR_START, prepare_config_writes, prepare_factory_runtime
from owner_database_runtime import prepare_runtime
from rtc4543 import DATA as RTC_DATA, DEFAULT_TIME as RTC_DEFAULT_TIME, Rtc4543
from admission_card import inspect_eeprom
from virtual_touch import VirtualTouchController
from duart_timer import DEFAULT_X1_HZ, MAX_BATCH_TICKS, DuartTimerConfig, DuartWallTimer, CpuRunBudget
from duart_timer import MAX_WALL_TIMER_BATCH_TICKS, MAX_WALL_TIMER_PENDING_TICKS


class Com3Disconnected(ConnectionError):
    """The x86 guest closed its serial peer; the bridge must stop cleanly."""


def stop_database_process(process: subprocess.Popen, trigger: str) -> None:
    """Distinguish an emulator exit from the bridge's own cleanup termination."""
    exit_before_cleanup = process.poll()
    stopped_by_bridge = exit_before_cleanup is None
    if stopped_by_bridge:
        print(f"DB_M68K_QEMU_STOP_REQUEST reason=bridge-cleanup trigger={trigger}", flush=True)
        process.terminate()
    try:
        _, qemu_stderr = process.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        _, qemu_stderr = process.communicate(timeout=5.0)
    if qemu_stderr:
        print(
            "DB_M68K_QEMU_STDERR "
            + qemu_stderr[-16384:].decode("utf-8", errors="replace")
            .strip().replace("\r", " ").replace("\n", " | "),
            flush=True,
        )
    print(
        f"DB_M68K_QEMU_EXIT code={process.returncode} "
        f"stopped_by_bridge={stopped_by_bridge} exit_before_cleanup={exit_before_cleanup}",
        flush=True,
    )


REG_A0 = 8
REG_A7 = 15
REG_A3 = 11
REG_SR = 16
REG_D0 = 0
REG_D2 = 2
REG_D6 = 6
REG_D7 = 7
MAIN_RX_STATUS = 0xFFFC0D
MAIN_TX_STATUS = 0xFFFC0C
MAIN_DATA = 0xFFFC0F
RX_READY = 0x40
TX_READY = 0x01
SERIAL_BITS_PER_BYTE = 10
UART_BAUD = 9600
UART_FRAME_SECONDS = 10 / UART_BAUD
INITIAL_FRAME_RETRY_SECONDS = 10.0
# The proven replay waits 165 seconds for the XP video application before its
# first INIT. The real database can produce INIT much earlier, so keep repeating
# only that captured frame long enough to span the same guest-ready window.
INITIAL_FRAME_MAX_ATTEMPTS = 60
# The programming loader occupies 0x0400..0x0FFF. Its 1B32 status and the FF
# sequence at 0x0C6E belong to the SerialLoader/boot protocol, which completes
# long before the XP game opens its operating-mode COM3 connection. A physical
# UART does not retain those bytes until then, but QEMU's socket backend does.
# Keep all loader traffic inside the controller and expose only bytes produced
# by the database runtime at 0x1000 and above.
DATABASE_RUNTIME_START = 0x00001000
LOADER_RUNTIME_COOKIE = 0x5F72D920
LOADER_IDLE_TX_STOP_PC = 0x00000C76
LOADER_IDLE_RETURN_PC = 0x00000C8A
# A byte watch on FFFC0D also fires when a 16-bit access begins at FFFC0C.
# These post-instruction PCs are statically proven TX-ready checks, not RX
# polls. They must never block waiting for socket input.
TX_STATUS_OVERLAP_STOP_PCS = frozenset(
    (0x00018228, 0x00018240, 0x00018258, 0x00019CB0, 0x00019CC2, 0x000C5F2C)
)
RUNTIME_UART_CLEAR_STOP_PCS = frozenset((0x00019CBC, 0x00019CCE))
# The runtime probes the database-board latch during start-up. On real
# hardware, writes select and clock the latch; a read from the same address
# returns board feedback rather than the last byte written. QEMU machine=none
# provides only RAM here, so reproduce the two statically identified replies.
BOARD_LATCH = 0x00800181
BOARD_LATCH_FEEDBACK_BY_STOP_PC = {
    0x00018324: 0x13,
    0x0001837A: 0x02,
}
# The board exposes an input byte plus write-only set/clear aliases.  The
# runtime uses bit 3 as the clock/data handshake at 0x6D0F4..0x6D11E and uses
# the same register triplet for other board signals.  machine=none otherwise
# leaves the input byte disconnected from both strobe aliases.
BOARD_PORT_INPUT = 0x0080019B
BOARD_PORT_SET = 0x0080019D
BOARD_PORT_CLEAR = 0x0080019F
BOARD_PORT_STROBES = frozenset((BOARD_PORT_SET, BOARD_PORT_CLEAR))
AUX_PROFILE_SELECTOR = 0x001E2A96
MP_STATE_ADDRESS = 0x001F9988
AUX_PROFILE_STATUS = 0x001E2A95
AUX_TRANSACTION_ACTIVE = 0x001E2B05
AUX_TRANSACTION_COMMAND = 0x001E2B04
AUX_TRANSACTION_DATA = 0x001E2B06
AUX_TRANSACTION_LENGTH = 0x001E2B13
AUX_RESPONSE_CHECK_PC = 0x0006D7DC
AUX_PROFILE_RESPONSE_CHECK_PC = 0x0006D9BA
AUX_VALUE_RESPONSE_CHECK_PC = 0x0006DB5A
COIN_ENTRY_PORT = 0x00800141
COIN_ENTRY_READ_PC = 0x0001678E
COIN_ENTRY_MASK = 0x40

# 0x6D5DE applies these two ten-byte ROM masks around 50 rounds of a
# three-bit right rotation and the affine byte map 0xAD*x+0x9F.  The inverse
# lets the virtual board send bytes through the firmware's own decoder.
AUX_DECODE_OUTER_KEY = bytes.fromhex("61 4F 9A 72 5B 89 ED C3 AD 8F")
AUX_DECODE_INNER_KEY = bytes.fromhex("55 14 E5 BD 8C 67 3D A5 77 54")


def rotate_aux_bits(value: bytes, amount: int) -> bytes:
    if not value:
        return value
    width = len(value) * 8
    bits = int.from_bytes(value, "big")
    amount %= width
    mask = (1 << width) - 1
    return (((bits << amount) | (bits >> (width - amount))) & mask).to_bytes(
        len(value), "big"
    )


def decode_aux_packet(value: bytes) -> bytes:
    data = bytes(byte ^ AUX_DECODE_INNER_KEY[i % 10]
                 for i, byte in enumerate(value))
    for _ in range(50):
        data = rotate_aux_bits(data, -3)
        data = bytes((0xAD * byte + 0x9F) & 0xFF for byte in data)
    return bytes(byte ^ AUX_DECODE_OUTER_KEY[i % 10]
                 for i, byte in enumerate(data))


def encode_aux_packet(value: bytes) -> bytes:
    data = bytes(byte ^ AUX_DECODE_OUTER_KEY[i % 10]
                 for i, byte in enumerate(value))
    for _ in range(50):
        data = bytes(((byte - 0x9F) * 0x25) & 0xFF for byte in data)
        data = rotate_aux_bits(data, 3)
    return bytes(byte ^ AUX_DECODE_INNER_KEY[i % 10]
                 for i, byte in enumerate(data))


def virtual_aux_identification_reply(
    challenge: int, card_eeprom: bytes | None = None
) -> bytes:
    # Command 0x31 at 0x6D750 expects 11 decoded bytes.  0x6D816..0x6D83E
    # checks the four challenge bytes, then 0x6D848..0x6D906 decodes a
    # nine-digit BCD number plus the first two model bytes.  Without a card
    # image, retain the earlier synthetic reply for regression comparison.
    identity = (
        card_eeprom[64:71] if card_eeprom is not None
        else bytes.fromhex("00 00 01 19 00 12 02")
    )
    plain = challenge.to_bytes(4, "big") + identity
    return encode_aux_packet(plain)


def virtual_aux_profile_reply(
    challenge: int, card_eeprom: bytes | None = None
) -> bytes:
    # 0x6DA1E..0x6DA7E checks the same challenge, then decodes bytes 4/5
    # as four BCD profile digits. 1155 selects the owner's ADP1486 profile
    # at 0x5F6F2; 1190 instead selects ADP1465 at 0x5F7A1. The remaining
    # five decoded bytes are still unverified and kept at their old value.
    profile = (
        card_eeprom[71:73] if card_eeprom is not None
        else bytes.fromhex("11 55")
    )
    plain = challenge.to_bytes(4, "big") + profile + bytes(5)
    return encode_aux_packet(plain)


def virtual_aux_value_reply(
    challenge: int, index: int, card_eeprom: bytes | None = None
) -> bytes | None:
    # 0x6DB94..0x6DBE4 checks four challenge bytes and returns byte 4.
    # A configured card supplies its actual EEPROM byte. The fallback keeps
    # the previous synthetic slots for regression comparison.
    values = {0x0D: 11, 0x0E: 55, 0x0F: 11, 0x10: 55}
    value = (
        card_eeprom[index]
        if card_eeprom is not None and 0 <= index < len(card_eeprom)
        else values.get(index)
    )
    if value is None:
        return None
    return encode_aux_packet(challenge.to_bytes(4, "big") + bytes([value]))
# The cabinet door switch is the external input on bit 4.  The original
# accessor at 0x603F0 returns active when this bit is clear; its callers enter
# the service handling path in that state.  A closed/pressed door switch is
# therefore represented by a high bit.  Unlike the output/strobe feedback,
# this bit is owned by the cabinet input model and must be re-applied after a
# set/clear-alias write.
BOARD_DOOR_CLOSED_MASK = 0x10
# The original board scanner at 0x73506 copies its multiplexed port samples
# into 0x1E246B onward. On machine=none, a read of 0x800101 merely returns
# the preceding output byte, leaving both hopper sensor lines low (0x08).
# The original hopper self-test modulates its sensor illumination/output at
# 0xE0654..0xE0682. With the output phase clear, an idle sensor must read high;
# with it set, the same sensor must read low. A constant high level passes the
# first phase but triggers a manipulation fault in the second. Overlay the
# missing board input after the scan, before the firmware tests it; never
# overwrite the firmware's payout control or fault flag.
BOARD_SCAN_COMPLETE_PC = 0x00074D26
HOPPER_SENSOR_SHADOW = 0x001E2473
HOPPER_SENSOR_IDLE_MASK = 0x01
HOPPER_MODULATION_COUNTER = 0x001EFE5C
HOPPER_MODULATION_PHASE = 0x001EFE5E
# The original logical-key table maps Rueckgabe (key id 9) to 0x1023 in every
# cabinet profile present in this runtime.  The accessor at 0x6010C resolves
# that mapping to 0x1E247B + 0x23 and treats bit 0x10 as active-low.  The edge
# accessor at 0x60186 uses the matching event bank at 0x1E2526 and consumes a
# set bit.  Keep the absent cabinet button released and discard only a stale
# edge generated by the floating machine=none input scanner.
RETURN_BUTTON_KEY_ID = 9
RETURN_BUTTON_MAPPING = 0x1023
RETURN_BUTTON_CURRENT = 0x001E249E
RETURN_BUTTON_EVENT = 0x001E2526
RETURN_BUTTON_MASK = 0x10
RUNTIME_IO_INIT_RETURN_PC = 0x00018482
# Vector 64 is the original board-timer interrupt.  Its handler acknowledges
# bit 3 at 0x80018B and calls the base timer service at 0x74CF2, which in
# turn advances the command state machine at 0x6D120.  Vector 134 only services
# the UART and cannot advance database start-up on its own.
BOARD_TIMER_VECTOR = 64
BOARD_TIMER_HANDLER = 0x000750DC
BOARD_TIMER_RTE_PC = 0x0007511C
BOARD_SCC_A_VECTOR = 65
BOARD_SCC_A_HANDLER = 0x0007511E
BOARD_SCC_A_RTE_PC = 0x000751BA
BOARD_TIMER_STATUS = 0x0080018B
BOARD_TIMER_PENDING = 0x08
# Bit 1 is the SCC-A receive interrupt.  The original RX handler loops while
# it remains set; TX-ready is signaled separately by bit 2 of 0x800183.
BOARD_SCC_A_RX_PENDING = 0x02
# The original vector-65 ISR services the first SCC channel's transmitter
# when status bit 2 is set, then calls the firmware command dispatcher at
# 0x70516.  Its control port is write-only during configuration and returns
# RR0 (TX ready, RX empty) when read.  No receive bytes are fabricated.
BOARD_SCC_CONTROL_A = 0x00800183
BOARD_SCC_A_TX_READY = 0x04
BOARD_SCC_A_COMMAND = 0x001E1D12
BOARD_SCC_A_TX_POINTER = 0x001E2120
# The second on-board serial channel is a Zilog-SCC-style interface used for
# optional cabinet peripherals.  Its control register is write-only for setup,
# but reads return RR0 instead of the last command byte.  machine=none models
# the address as RAM, so the bridge publishes SCC receive status and data from
# a bounded virtual cabinet peripheral rather than echoing write-only commands.
BOARD_SCC_CONTROL_B = 0x00800193
BOARD_SCC_DATA_B = 0x00800197
BOARD_SCC_IDLE_STATUS = 0x04
BOARD_SCC_RX_READY = 0x01
BOARD_SCC_END_OF_FRAME = 0x20
BOARD_SCC_STATE = 0x001E22B0
BOARD_SCC_TX_ACTIVE = 0x01
BOARD_SCC_REPLY_WAITING = 0x04
BOARD_SCC_PENDING = 0x10
MP_REQUIRED_TYPE_STATE = 0x001F8228
MP_DETECTED_TYPE_STATE = 0x001F16CD


def pairing_gap_log_transition(
    previous: tuple[int, int] | None, current: tuple[int, int] | None
) -> str | None:
    """Describe SCC gap changes without dereferencing a cleared gap."""
    if current == previous:
        return None
    if current is None:
        return "DB_VIRTUAL_MP_TX_GAP_CLEARED"
    return (
        "DB_VIRTUAL_MP_TX_GAP "
        f"expected_remaining={current[0]:02X} "
        f"observed_remaining={current[1]:02X}"
    )


class VirtualCoinValidator:
    """Virtual validator on the firmware's DUART-B multidrop channel.

    Firmware at 0x1C2A6 sends 78/78, then 7F/00/7F and accepts an
    additive-checksum reply whose first three data bytes are NRI or WHM
    (0x1C35A..0x1C3D6). RR1 bit 5 marks the checksum byte as end-of-frame.
    Coin telegrams enter the original receive/authentication/booking path;
    the peer never writes a credit balance or initiates a physical payout.
    """

    IDENTITY_PROBE = bytes.fromhex("7F 00 7F")
    SECONDARY_IDENTITY_PROBE = bytes.fromhex("C7 00 C7")
    HOPPER_READY_PROBE = bytes.fromhex("C0 C0")
    HOPPER_READY_FOLLOWUP = bytes.fromhex("C3 C3")
    HOPPER_READY_SEQUENCE = (0x0F, 0x0E, 0x01)
    SECONDARY_DATA_PROBE = bytes.fromhex("C7 25 EC")
    HOPPER_STATUS_PROBE = bytes.fromhex("C7 26 ED")
    HOPPER_RELEASE_COMMAND = bytes.fromhex("C2 FF C1")
    HOPPER_DETAIL_PROBE = bytes.fromhex("C7 27 EE")
    HOPPER_ACTIVATION_PREFIX = bytes.fromhex("C5 00 00 01")
    PAIRING_CHALLENGE_COMMAND = bytes.fromhex("7F 27")
    VALIDATOR_STATUS_COMMAND = bytes.fromhex("7F 26")
    VALIDATOR_TYPE_PROBES = (
        bytes.fromhex("7F 04 83"),
        bytes.fromhex("7F 02 01 FF 81"),
    )
    VALIDATOR_SESSION_COMMAND = 0x7B
    COIN_CHANNEL_MAP = 0x001F16CE
    COIN_QUEUE_LIMIT = 16
    COIN_REQUEST_TIMEOUT = 30.0
    COIN_EURO_UNITS = 20  # CC4 euro denomination table: 5-cent accounting units.
    # Virtual euro validator: 10c, 20c, 50c, 1 euro, 2 euro; unused channels 0.
    # B1C8 copies each authenticated first reply byte into the native map.
    COIN_CHANNEL_VALUES = bytes((2, 4, 10, 20, 40)) + bytes(11)
    PAIRING_COMPARE_PC = 0x0000B730
    PAIRING_READ_PC = 0x0000B6E0
    PAIRING_FAILURE_PC = 0x0000B74A
    PAIRING_RECEIVE_BUFFER = 0x001E223C
    PAIRING_EXPECTED_WORD = 0x001F7876
    PAIRING_HISTORY = 0x001F786F
    PAIRING_COUNTER = 0x001F7874
    PAIRING_SESSION = 0x001F8222
    PAIRING_TABLE_SELECTOR = 0x001F1D3C
    PAIRING_DYNAMIC_TABLE_FLAG = 0x001F957A
    PAIRING_DYNAMIC_TABLE = 0x001F9580
    PAIRING_STATIC_TABLE_A = 0x000774AA
    PAIRING_STATIC_TABLE_B = 0x000775AA
    # Diagnostic virtual validator profile: the original firmware explicitly
    # accepts NRI at 0x1C35A..0x1C3D6 and recognizes eagle and FT30 at
    # 0x21B0A..0x21B06. This is not a claim about the owner's installed WH
    # 921.13V5; its encryption variant is not known from the model alone.
    IDENTITY_DATA = (
        b"NRI" + bytes(0x0A - 3) + b"eagle"
        + bytes(0x14 - 0x0F) + b"FT30"
        + bytes(0x1B - 0x18) + b"\x05\x00" + bytes(33 - 0x1D)
    )

    def __init__(self) -> None:
        self._tx = bytearray()
        self._next_remaining = 0
        self.last_tx_frame: bytes | None = None
        self._rx: deque[int] = deque()
        self.challenge_pending = False
        self.last_pairing_reply: bytes | None = None
        self.pairing_frame_gap = False
        self.last_pairing_gap: tuple[int, int] | None = None
        self.last_pairing_count_lag = False
        self.type_pending = False
        self._hopper_ready_phase = 0
        self.coin_window = False
        self._coin_routes = bytes(8)
        self._coin_routes_known = False
        self._coin_enabled_mask = 0
        self._coin_wait_log_at = 0.0
        self._coins: deque[tuple[int, float]] = deque()
        self._coin_sequence = 0
        self._coin_inflight: tuple[int, float, int, int] | None = None
        self._entry_sensor_sequence = 0
        self._entry_sensor_samples = 0

    def stage_entry_sensor(self, rsp: RspClient) -> bool:
        """Model the physical entry input for cabinets with no permanent gate.

        Original 16780 samples AY port A bit 6. Four asserted samples open
        E26CE's receive window; 61 asserted samples signal a stuck input.
        Drive only the device input, never that window or a credit variable.
        """
        if self._entry_sensor_samples:
            return True
        if (not self._coins or self._coin_inflight is not None
                or self._coins[0][0] == self._entry_sensor_sequence
                or not self._coin_routes_known or not self._coin_enabled_mask):
            return False
        if (rsp.read_memory(0x001F777D, 1)[0] == 1
                or any(rsp.read_memory(0x001E2A02, 1))
                or any(rsp.read_memory(0x001E26CE, 2))):
            return False
        if (any(rsp.read_memory(0x001E2CE8, 1))
                or any(rsp.read_memory(0x001F80FC, 1))
                or not (any(rsp.read_memory(0x001E26B6, 2))
                        or any(rsp.read_memory(0x001E26BA, 1)))):
            return False
        channels = rsp.read_memory(self.COIN_CHANNEL_MAP, 16)
        enabled_euro = any(
            value & 0x7F == self.COIN_EURO_UNITS
            and self._coin_enabled_mask & (1 << channel)
            and ((self._coin_routes[channel // 2] >> (4 if channel % 2 == 0 else 0)) & 0xF) != 0xF
            for channel, value in enumerate(channels)
        )
        if not enabled_euro:
            return False
        self._entry_sensor_sequence = self._coins[0][0]
        self._entry_sensor_samples = 5  # four active samples, one explicit idle
        return True

    def sample_entry_sensor(self, port_value: int) -> int:
        if not self._entry_sensor_samples:
            return port_value & ~COIN_ENTRY_MASK
        active = self._entry_sensor_samples > 1
        self._entry_sensor_samples -= 1
        return (port_value & ~COIN_ENTRY_MASK) | (COIN_ENTRY_MASK if active else 0)

    def queue_coin(self, cents: int, now: float) -> int:
        if type(cents) is not int or cents != 100:
            raise ValueError("only a virtual 1-euro coin is supported")
        if len(self._coins) >= self.COIN_QUEUE_LIMIT:
            raise ValueError("virtual coin queue is full")
        self._coin_sequence += 1
        self._coins.append((self._coin_sequence, now))
        return self._coin_sequence

    def expire_coins(self, now: float) -> list[str]:
        events = []
        while self._coins and now - self._coins[0][1] >= self.COIN_REQUEST_TIMEOUT:
            sequence, _ = self._coins.popleft()
            events.append(f"DB_VIRTUAL_MP_COIN_EXPIRED id={sequence} cents=100 reason=acceptance_unavailable")
        return events

    def prepare_coin_reply(self, rsp: RspClient, now: float) -> tuple[bytes | None, list[str]]:
        """Replace only an unpublished status reply, or answer a 7B coin window.

        CC4 19E2C accepts 8n/route/auth-hi/auth-lo/checksum, B838 checks
        AEA6's transform, and 1B1DC books the mapped value (20 = 1 euro).
        All firmware access here is read-only. No register override is used.
        """
        if not self.coin_window:
            return None, []
        self.coin_window = False
        events = self.expire_coins(now)
        if not self._coins and self._coin_inflight is None:
            return None, events
        balances = rsp.read_memory(0x001F1280, 4)
        balance = int.from_bytes(balances[:2], "big") + int.from_bytes(balances[2:], "big")
        failures = rsp.read_memory(0x001F787C, 1)[0]
        if self._coin_inflight is not None:
            sequence, sent_at, previous, previous_failures = self._coin_inflight
            if failures != previous_failures:
                outcome = "REJECTED"
            elif balance - previous >= self.COIN_EURO_UNITS:
                outcome = "CREDITED"
            elif now - sent_at >= self.COIN_REQUEST_TIMEOUT:
                outcome = "UNCONFIRMED"
            else:
                return None, events
            events.append(f"DB_VIRTUAL_MP_COIN_{outcome} id={sequence} cents=100")
            self._coin_inflight = None
        if not self._coins or self.challenge_pending or self.type_pending:
            return None, events
        # Mirror the original money-frame acceptance gate and its debounce.
        phase = int.from_bytes(rsp.read_memory(0x001E2300, 2), "big")
        if phase not in (0, 1):
            return self._coin_wait(now, events, f"mp_phase phase={phase:04X}")
        # Keep the actual blockers visible, not just a generic 'not ready'.
        # Only read state here; acceptance remains owned by the firmware.
        gates = {
            "inhibit": rsp.read_memory(0x001E2CE8, 1)[0],
            "fault": rsp.read_memory(0x001F80FC, 1)[0],
            "acceptance": int.from_bytes(rsp.read_memory(0x001E26B6, 2), "big"),
            "alternate_acceptance": rsp.read_memory(0x001E26BA, 1)[0],
            "debounce": rsp.read_memory(0x001E2B7D, 1)[0],
            "authentication_pending": rsp.read_memory(0x001F787A, 1)[0],
            "booking_pending": rsp.read_memory(0x001E22B6, 1)[0],
        }
        if (gates["inhibit"] or gates["fault"]
                or not (gates["acceptance"] or gates["alternate_acceptance"])
                or gates["debounce"] or gates["authentication_pending"]
                or gates["booking_pending"]):
            details = " ".join(f"{name}={value:04X}" for name, value in gates.items())
            return self._coin_wait(now, events,
                f"firmware_busy_or_inhibited {details} enabled={self._coin_enabled_mask:04X}")
        permanent_gate = rsp.read_memory(0x001F777D, 1)[0]
        service_gate = rsp.read_memory(0x001E2A02, 1)[0]
        entry_window = int.from_bytes(rsp.read_memory(0x001E26CE, 2), "big")
        if permanent_gate != 1 and not service_gate and not entry_window:
            return self._coin_wait(now, events,
                f"firmware_acceptance_disabled permanent={permanent_gate:02X} "
                f"service={service_gate:02X} entry_window={entry_window:04X}")
        channels = rsp.read_memory(self.COIN_CHANNEL_MAP, 16)
        channel_route = None
        for channel, value in enumerate(channels):
            # Do not guess a channel number: use the live denomination map
            # and separate 7E routing / 7C enable masks sent to the MP.
            routes = (self._coin_routes[channel // 2] >> (4 if channel % 2 == 0 else 0)) & 0xF
            if (value & 0x7F == self.COIN_EURO_UNITS
                    and self._coin_routes_known
                    and self._coin_enabled_mask & (1 << channel)
                    and routes != 0xF):
                # Zero is an actual route, accepted by 19E2C and 1B1DC;
                # it is not an acceptance mask. Never invent a routing bit.
                route = next((bit for bit in (8, 4, 2, 1) if routes & bit), 0)
                channel_route = channel, route
                break
        if channel_route is None:
            return self._coin_wait(now, events,
                f"channel_not_enabled enabled={self._coin_enabled_mask:04X} "
                f"channels={channels.hex().upper()} routes={self._coin_routes.hex().upper()}")
        channel, route = channel_route
        code = 0x80 | channel
        expected = 0
        if rsp.read_memory(0x001F8221, 1)[0] and not rsp.read_memory(0x001EACE0, 1)[0]:
            seed = (bytes([code]) + rsp.read_memory(self.PAIRING_HISTORY, 5)
                    + rsp.read_memory(self.PAIRING_COUNTER, 2)
                    + rsp.read_memory(self.PAIRING_SESSION, 4))
            expected = self.pairing_expected_word(seed, self._pairing_table(rsp))
        data = bytes((code, route)) + expected.to_bytes(2, "big")
        reply = data + bytes([sum(data) & 0xFF])
        self._rx.clear()
        self._rx.extend(reply)
        sequence, _ = self._coins.popleft()
        self._coin_inflight = sequence, now, balance, failures
        events.append(f"DB_VIRTUAL_MP_COIN_SENT id={sequence} cents=100 channel={channel} route={route} wire={reply.hex(' ').upper()}")
        return reply, events

    def _coin_wait(self, now: float, events: list[str], reason: str):
        if now >= self._coin_wait_log_at:
            events.append(f"DB_VIRTUAL_MP_COIN_WAITING id={self._coins[0][0]} cents=100 reason={reason}")
            self._coin_wait_log_at = now + 5.0
        return None, events

    def _pairing_table(self, rsp: RspClient) -> bytes:
        if any(rsp.read_memory(self.PAIRING_DYNAMIC_TABLE_FLAG, 2)):
            address = self.PAIRING_DYNAMIC_TABLE
        elif any(rsp.read_memory(self.PAIRING_TABLE_SELECTOR, 2)):
            address = self.PAIRING_STATIC_TABLE_A
        else:
            address = self.PAIRING_STATIC_TABLE_B
        return rsp.read_memory(address, 256)

    def observe_tx(self, value: int, remaining: int) -> bytes | None:
        self.coin_window = False
        if (
            remaining == 0
            and self._next_remaining == 1
            and self._tx.startswith(self.PAIRING_CHALLENGE_COMMAND)
            and (
                (len(self._tx) == 8 and value == sum(self._tx) & 0xFF)
                or (len(self._tx) == 7 and self.pairing_frame_gap)
            )
        ):
            # The SCC state counter can reach zero by the time a watchpoint
            # handler reads it for the checksum byte. The eight observed
            # prefix bytes and additive checksum still prove a full frame.
            remaining = 1
            self.last_pairing_count_lag = True
        if not 0 < remaining <= 0x45:
            self._tx.clear()
            self._next_remaining = 0
            self.last_tx_frame = None
            return None
        if remaining == 9 and value == self.PAIRING_CHALLENGE_COMMAND[0]:
            self.pairing_frame_gap = False
            self.last_pairing_gap = None
            self.last_pairing_count_lag = False
        if remaining != self._next_remaining:
            if (
                (self._tx.startswith(self.PAIRING_CHALLENGE_COMMAND)
                 # The counter can also run ahead on the command's second byte.
                 or (self._tx == self.PAIRING_CHALLENGE_COMMAND[:1] and self._next_remaining == 8))
                and remaining == self._next_remaining - 1
                and not self.pairing_frame_gap
            ):
                self.pairing_frame_gap = True
                self.last_pairing_gap = (self._next_remaining, remaining)
            elif (
                self.pairing_frame_gap
                and self.last_pairing_gap
                == (self._next_remaining + 2, remaining)
            ):
                # The first of two consecutive writes reported the *next*
                # SCC counter value. Both data writes were observed, so the
                # apparent one-byte gap was only a stale/read-ahead counter.
                self.pairing_frame_gap = False
                self.last_pairing_gap = None
            else:
                self._tx.clear()
                self.last_tx_frame = None
                self.pairing_frame_gap = False
        self._tx.append(value & 0xFF)
        self._next_remaining = remaining - 1
        if remaining != 1:
            return None
        frame = bytes(self._tx)
        if (
            self.pairing_frame_gap
            and self.last_pairing_gap is not None
            and len(frame) == 8
            and frame[:1] == self.PAIRING_CHALLENGE_COMMAND[:1]
        ):
            # One SCC data-write watchpoint was missed. The final byte is an
            # additive checksum, so recover the one missing payload byte and
            # answer the actual complete challenge instead of dropping it.
            missing_at = 9 - self.last_pairing_gap[0]
            missing_value = (frame[-1] - sum(frame[:-1])) & 0xFF
            repaired = (
                frame[:missing_at] + bytes([missing_value])
                + frame[missing_at:]
            )
            if repaired.startswith(self.PAIRING_CHALLENGE_COMMAND):
                frame = repaired
        self.last_tx_frame = frame
        self._tx.clear()
        self._next_remaining = 0
        if self._rx:
            return None
        if len(frame) == 11 and frame[:2] == b"\x7E\x00" and frame[-1] == sum(frame[:-1]) & 0xFF:
            self._coin_routes = frame[2:10]
            self._coin_routes_known = True
            # Native transmit enters RX-wait even for routing setup. The
            # zero ACK also clears the pending route-update flag in 19E2C.
            data = b"\x00"
        elif len(frame) == 6 and frame[0] == 0x7C and frame[-1] == sum(frame[:-1]) & 0xFF:
            # 1C8DE emits ~F163E/~F163F (one bit enables a channel),
            # followed by F1640/F1641's additional inhibit bits. 19E2C
            # accepts zero ACK in phases 4/5 (enable) and 6/7 (disable).
            self._coin_enabled_mask = int.from_bytes(frame[1:3], "big") & ~int.from_bytes(frame[3:5], "big") & 0xFFFF
            data = b"\x00"
        elif frame in (self.IDENTITY_PROBE, self.SECONDARY_IDENTITY_PROBE):
            data = self.IDENTITY_DATA
        elif frame in (self.HOPPER_READY_PROBE, self.HOPPER_READY_FOLLOWUP):
            # 0xE22C2, 0xE22C6 and 0xE22D0 advance the boot state
            # machine on 0F -> 0E -> 01. The first probe uses C0/C0;
            # subsequent requests at 0xE222A use C3/C3 for hopper 0.
            data = bytes([self.HOPPER_READY_SEQUENCE[self._hopper_ready_phase]])
            self._hopper_ready_phase = (
                self._hopper_ready_phase + 1
            ) % len(self.HOPPER_READY_SEQUENCE)
        elif frame == self.SECONDARY_DATA_PROBE:
            # 0xE4A72..0xE4A88 consumes four response data bytes after
            # C7/25 and waits for the SCC receive-complete flag at 0x1EB6E2.
            data = bytes(4)
        elif frame == self.HOPPER_STATUS_PROBE:
            # 0xE413C..0xE4158 copies two response bytes to the first
            # release-state pair after C7/26. Zero is a neutral placeholder.
            data = bytes(2)
        elif frame == self.HOPPER_RELEASE_COMMAND:
            # 0xE419A..0xE41A4 waits only for receive completion after C2/FF;
            # the reply payload is not inspected in this branch.
            data = b"\x00"
        elif frame == self.HOPPER_DETAIL_PROBE:
            # 0xE41FE..0xE4236 copies five returned bytes into the hopper
            # state for this C7/27 poll. They remain a neutral placeholder.
            data = bytes(5)
        elif (
            len(frame) == 7
            and frame.startswith(self.HOPPER_ACTIVATION_PREFIX)
            and frame[-1] == sum(frame[:-1]) & 0xFF
        ):
            # 0xE43F0 waits for receive completion; 0xE443A..0xE4446
            # checks the first response byte against 0x5A for hopper 0.
            # This virtual ACK does not initiate a physical payout.
            data = b"\x5A"
        elif (
            len(frame) == 9
            and frame.startswith(self.PAIRING_CHALLENGE_COMMAND)
            and frame[-1] == sum(frame[:-1]) & 0xFF
        ):
            # A provisional reply is replaced from the running firmware's
            # pairing state before it is exposed on SCC-B.
            data = b"\x00\x00\x00"
            self.challenge_pending = True
        elif frame in self.VALIDATOR_TYPE_PROBES:
            # 0x21994/0x21A18 stores the first received byte at 0x1F16CD;
            # 0x1C690..0x1C6C2 compares it with the selected type at
            # 0x1F8228. Replace this provisional byte before publishing.
            data = b"\x00"
            self.type_pending = True
        elif (
            len(frame) == 5
            and frame.startswith(self.VALIDATOR_STATUS_COMMAND)
            and frame[-1] == sum(frame[:-1]) & 0xFF
        ):
            # 0xB08E..0xB0FE sends this probe when the original firmware's
            # validator watchdog advances. At 0x1A204 a valid SCC reply
            # resets that watchdog; no coin or payout event is generated.
            data = b"\x00"
            self.coin_window = True
        elif (
            len(frame) == 7
            and frame[0] == self.VALIDATOR_SESSION_COMMAND
            and frame[-1] == sum(frame[:-1]) & 0xFF
        ):
            # B08E does not compare a payload, but its shared 1C184 sender
            # still enters RX-wait. Leaving this unacknowledged holds the
            # bus busy until 19E2C's 100-tick timeout and can starve 7C.
            # A neutral status ACK completes the transaction without credit.
            data = b"\x00"
            self.coin_window = True
        else:
            return None
        reply = data + bytes([sum(data) & 0xFF])
        self._rx.extend(reply)
        return reply

    def status(self) -> int:
        status = BOARD_SCC_IDLE_STATUS
        if self._rx:
            status |= BOARD_SCC_RX_READY
            if len(self._rx) == 1:
                status |= BOARD_SCC_END_OF_FRAME
        return status

    def data(self) -> int | None:
        return self._rx[0] if self._rx else None

    def pending_count(self) -> int:
        return len(self._rx)

    def consume_rx(self) -> int | None:
        return self._rx.popleft() if self._rx else None

    @staticmethod
    def pairing_expected_word(seed: bytes, table: bytes) -> int:
        """Mirror the 7-round transform at firmware 0xAEA6..0xB082."""
        if len(seed) != 12 or len(table) != 256:
            raise ValueError("pairing requires 12 seed and 256 table bytes")
        state = list(seed)
        for _ in range(7):
            state = [table[value] for value in state]
            rotated = [
                ((value << 3) & 0xFF) | (state[(index + 1) % 12] >> 5)
                for index, value in enumerate(state)
            ]
            for left, right in ((0, 6), (2, 8), (4, 10)):
                rotated[left], rotated[right] = rotated[right], rotated[left]
            state = rotated
        return sum(
            (state[index] << 8) | state[index + 1]
            for index in range(0, 12, 2)
        ) & 0xFFFF

    def prepare_pairing_reply(self, rsp: RspClient) -> tuple[int, bytes]:
        """Answer the pending 7F/27 challenge with the actual expected word."""
        if not self.challenge_pending or len(self._rx) != 4:
            raise RuntimeError("no pending, unpublished pairing challenge")
        if self.last_tx_frame is None or not 0 <= self.last_tx_frame[2] < 16:
            raise RuntimeError("pairing challenge has no valid channel index")
        denomination = self.COIN_CHANNEL_VALUES[self.last_tx_frame[2]]
        # 0xAEA6 reads the *old* history before it shifts that history itself.
        seed = (
            bytes([denomination])
            + rsp.read_memory(self.PAIRING_HISTORY, 5)
            + rsp.read_memory(self.PAIRING_COUNTER, 2)
            + rsp.read_memory(self.PAIRING_SESSION, 4)
        )
        if int.from_bytes(
            rsp.read_memory(self.PAIRING_DYNAMIC_TABLE_FLAG, 2), "big"
        ):
            table_address = self.PAIRING_DYNAMIC_TABLE
        elif any(rsp.read_memory(self.PAIRING_TABLE_SELECTOR, 2)):
            table_address = self.PAIRING_STATIC_TABLE_A
        else:
            table_address = self.PAIRING_STATIC_TABLE_B
        expected = self.pairing_expected_word(
            seed, rsp.read_memory(table_address, 256)
        )
        data = bytes([denomination]) + expected.to_bytes(2, "big")
        reply = data + bytes([sum(data) & 0xFF])
        self._rx.clear()
        self._rx.extend(reply)
        self.last_pairing_reply = reply
        return expected, reply

    def reconcile_pairing_receive_buffer(
        self, rsp: RspClient
    ) -> tuple[bytes, bytes] | None:
        """Repair a dropped/duplicated SCC byte before firmware consumes it.

        The bridge has observed all four register reads at this point. The
        owner's code at B6E0 reads the copied four-byte reply from A3 next.
        A QEMU/watchpoint interleaving can leave that buffer one byte behind
        even though the four values were published in order on SCC-B.
        """
        if (not self.challenge_pending or self._rx
                or self.last_pairing_reply is None):
            return None
        address = rsp.read_register_u32(REG_A3)
        if address != self.PAIRING_RECEIVE_BUFFER:
            return None
        observed = rsp.read_memory(address, len(self.last_pairing_reply))
        if observed != self.last_pairing_reply:
            rsp.write_memory(address, self.last_pairing_reply)
        return observed, self.last_pairing_reply

    def prepare_type_reply(self, rsp: RspClient) -> tuple[int, bytes]:
        """Return the detected type required by the 0x1C690..0x1C6C2 check."""
        if not self.type_pending or len(self._rx) != 2:
            raise RuntimeError("no pending, unpublished validator type probe")
        selected = rsp.read_memory(MP_REQUIRED_TYPE_STATE, 1)[0]
        # The selected operating mode is not the validator's returned type:
        # modes 1 and 2 both require reported type 2, mode 3 requires type 3.
        detected = 2 if selected in (1, 2) else 3 if selected == 3 else 0
        reply = bytes((detected, detected))
        self._rx.clear()
        self._rx.extend(reply)
        self.type_pending = False
        return detected, reply

    def match_challenge(self, rsp: RspClient) -> tuple[int, int, bool] | None:
        if not self.challenge_pending and not self.pairing_frame_gap:
            return None
        expected = int.from_bytes(
            rsp.read_memory(self.PAIRING_EXPECTED_WORD, 2), "big"
        )
        actual = rsp.read_register_u32(REG_D6)
        overridden = (actual & 0xFFFF) != expected
        if overridden:
            rsp.write_register_u32(REG_D6, (actual & 0xFFFF0000) | expected)
        self.challenge_pending = False
        self.pairing_frame_gap = False
        return actual & 0xFFFF, expected, overridden

    def publish(self, rsp: RspClient) -> None:
        current = self.data()
        if current is not None:
            rsp.write_memory(BOARD_SCC_DATA_B, bytes([current]))
        rsp.write_memory(BOARD_SCC_CONTROL_B, bytes([self.status()]))


def consume_coin_validator_byte(
    device: VirtualCoinValidator, rsp: RspClient,
) -> tuple[int | None, int | None]:
    """A DUART data-register read must also refresh an empty FIFO's status.

    SRB and CSRB share address 0x800193. The flat RAM backing may retain a
    firmware write (e.g. baud selector BB) instead of the read-side status.
    The original drain loop at 1C21A..1C222 then spins on RX-ready even
    though our receive queue is empty. Never leave that stale flag behind
    on an empty read, and never discard bytes that really are queued.
    """
    stale_status = None
    if not device.pending_count():
        status = rsp.read_memory(BOARD_SCC_CONTROL_B, 1)[0]
        if status & BOARD_SCC_RX_READY:
            stale_status = status
    received = device.consume_rx()
    device.publish(rsp)
    return received, stale_status

# The timer ISR deliberately executes ``move.w #$2000,sr`` at 0x74E36 before
# running deferred work.  That re-enables board interrupts while the outer ISR
# is still active. Deferred routines wait at 0x60442 for the 10 ms queue and
# at 0xC5D66/0xC5D6E for UART acknowledgement or timer timeout, then at
# 0xC5DCE/0xC5DDC for the 100-tick queue at 0x1E29E4. These waits can only
# make progress when the appropriate original ISR nests once.
SR_INTERRUPT_MASK = 0x0700
TIMER_QUEUE_WAIT_PC = 0x00060442
TIMER_QUEUE_WAIT_PCS = frozenset((TIMER_QUEUE_WAIT_PC, 0x00060444))
# 0x9DB70 waits at 0x9DBBC until the board tick counter at 0x1E2B24
# advances by 0x32. It can run inside the unmasked board timer ISR;
# without one nested board tick, the second initialization cannot finish.
BOARD_TICK_COUNTER_WAIT_PC = 0x0009DBBC
SCC_A_COMMAND_WAIT_PC = 0x0007075C
UART_REPLY_WAIT_PCS = frozenset((0x000C5D66, 0x000C5D6E))
HOPPER_QUEUE_WAIT_PCS = frozenset((0x000C5DCE, 0x000C5DDC))
HOPPER_FAULT_ADDRESS = 0x001F2A5B
HOPPER_DIAGNOSTIC_FIELDS = (
    ("mode_70", 0x001EFE70, 1),
    ("mode_71", 0x001EFE71, 1),
    ("mode_7d", 0x001EFE7D, 1),
    ("mode_a1", 0x001EFEA1, 1),
    ("sensor_6d", 0x001E246D, 1),
    ("sensor_73", 0x001E2473, 1),
    ("sensor_74", 0x001E2474, 1),
    ("modulation_counter", HOPPER_MODULATION_COUNTER, 1),
    ("modulation_phase", HOPPER_MODULATION_PHASE, 1),
    ("sensor_counter_73", 0x001EFE56, 2),
    ("sensor_counter_74", 0x001EFE58, 2),
    ("counter_c2", 0x001EFEC2, 2),
    ("counter_c4", 0x001EFEC4, 2),
    ("counter_c6", 0x001EFEC6, 2),
    ("counter_c8", 0x001EFEC8, 2),
)
TIMER_QUEUE_BASE = 0x001E29E4
TIMER_QUEUE_SLOTS = 6
# The absent-device scan at 0x75866 loads 0x3C ticks into timer slot zero,
# then polls that exact slot through 0x60418 at 0x75A66/0x75A68.  On hardware
# this is a 600 ms timeout.  Replaying every 10 ms board IRQ through GDB turns
# each absent MP/acceptor/dispenser probe into about a minute of host time.
# After one bounded run slice, expire only this proven poll and preserve its
# original "device absent" result.
DEVICE_DISCOVERY_TIMER_WAIT_PCS = frozenset((0x00075A66, 0x00075A68))
DEVICE_DISCOVERY_TIMER_MAX_TICKS = 0x3C
MAX_BOARD_TIMER_DEPTH = 2
# The firmware contains a 3M MicroTouch serial driver and initialization state
# machine.  On the cabinet this controller is another peripheral of the board,
# not the XP COM3 peer.  machine=none has no physical controller, so provide the
# documented command responses through the firmware's own 32-byte RX ring.
TOUCH_UART_BASE = 0x001E2AB0
TOUCH_UART_RX_COUNT = TOUCH_UART_BASE + 0x11
TOUCH_UART_RX_INDEX = TOUCH_UART_BASE + 0x12
TOUCH_UART_RX_BUFFER = TOUCH_UART_BASE + 0x13
TOUCH_UART_RX_BUFFER_SIZE = 0x20
TOUCH_TRANSACTION_BASE = 0x001E3018
TOUCH_TRANSACTION_STATE = TOUCH_TRANSACTION_BASE + 0x0C
TOUCH_TRANSACTION_REQUEST = TOUCH_TRANSACTION_BASE + 0x1D
TOUCH_TRANSACTION_REQUEST_SIZE = 0x10
TOUCH_WAITING_FOR_REPLY = 7
TOUCH_IDENTITY = b"A30000"
TOUCH_CLICK_MENUE_PREFIX = bytes.fromhex("01 02 41 00 3F 2C")
TOUCH_CLICK_FRAME_LENGTH = 11
TOUCH_WIDE_MODE_ADDRESS = 0x001F01E4
# Return from native direct CX transmission (FUN_7F8C8), before state 6
# consumes its acknowledgment. Calibration does not use TOUCH_TRANSACTION_STATE.
TOUCH_CALIBRATION_CX_RETURN_PC = 0x0008114C
TOUCH_CALIBRATION_FINISH_PC = 0x00081340
TIMER_VECTOR = 134
TIMER_HANDLER = 0x000C6BD0
UART_TIMER_RTE_PC = 0x000C6C0A
UART_STATE_ADDRESS = 0x001EBBB0
UART_STATE_READY = 0x58A9
UART_INIT_ENTRY = 0x000C5F82
UART_INIT_RETURN_SENTINEL = 0x00000300
UART_INIT_CALL_TIMEOUT = 10.0
# The owner log D58DF8B...4480 proves the immutable part of PARA_INITVIDEO.
# machine=none supplies neither an RTC nor the cabinet profile EEPROM, so only
# those missing fields are completed below.  Identity and content fields must
# still match the bytes produced by the owner firmware or the bridge fails.
INITVIDEO_FRAME_LENGTH = 39
INITVIDEO_PREFIX = bytes.fromhex("01 02 22 00")
INITVIDEO_OWNER_FIELDS = bytes.fromhex(
    "7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C 01 00 00 00 00 05"
)
INITVIDEO_DEVICE_FIELDS = bytes.fromhex("01 00 02 FF")
MAX_LOADER_SIZE = 1024 * 1024
MIN_TIMER_RUN_SLICE = 0.005
BOARD_TIMER_PERIOD_SECONDS = DuartTimerConfig().period_seconds
MAX_BOARD_TIMER_TICKS_PER_CYCLE = MAX_BATCH_TICKS
DUART_TIMER_ACR = 0x00800189
DUART_TIMER_CTUR = 0x0080018D
DUART_TIMER_CTLR = 0x0080018F
DUART_TIMER_IVR = 0x00800199
# QEMU icount is instruction-based, not MC68331 cycle-accurate. The requested
# 2x setting assigns 32 ns per guest instruction, capping execution at
# 15,625,000 instructions/second. The faster shift=5 mode repeatedly made
# QEMU abort inside a translated board-ISR block; shift=6 completed a live
# boot and entered a game. This is still not cycle-accurate MC68331 timing.
GUEST_ICOUNT_SHIFT = 6
GUEST_ICOUNT_NS_PER_INSTRUCTION = 1 << GUEST_ICOUNT_SHIFT
GUEST_MAX_INSTRUCTIONS_PER_SECOND = 1_000_000_000 // GUEST_ICOUNT_NS_PER_INSTRUCTION
# The owner firmware bit-bangs the Epson R4543 RTC at $FFF907. $FFF906 is a
# word configuration write that also resets the adjacent port byte. Bit 7 is
# bidirectional serial DATA, not a free-running clock. See $6047A/$604BE and
# $609A6 in the unmodified runtime.
RTC_CONTROL_REGISTER = 0x00FFF906
RTC_PORT_REGISTER = 0x00FFF907
RTC_FAULT_ENTRY = 0x00079AA4
RTC_CONTROL_WATCH_LENGTH = 1
# During heavy firmware phases Windows can defer a GDB stop reply well beyond
# five seconds even though the CPU has already been interrupted. This is only
# the control-reply deadline after a run slice; it neither lengthens the
# slice nor relaxes the icount cap. Keep enough headroom to avoid killing a
# healthy bridge while QEMU unwinds a watchpoint-heavy board scan.
RSP_INTERRUPT_REPLY_TIMEOUT = 30.0
RSP_CONTROL_REPLY_TIMEOUT = 5.0
IDLE_DIAGNOSTIC_SECONDS = 10.0


def validate_timer_interval(run_slice: float) -> None:
    """Validate a debugger run slice; QEMU icount limits guest speed."""
    if not math.isfinite(run_slice) or run_slice < MIN_TIMER_RUN_SLICE:
        raise ValueError(
            f"timer run slice must be at least {MIN_TIMER_RUN_SLICE:.3f} seconds"
        )


def board_timer_ticks_per_cycle(run_slice: float, config: DuartTimerConfig | None = None) -> int:
    """Validate a bounded batch using the programmed DUART timer period."""
    validate_timer_interval(run_slice)
    period = (config or DuartTimerConfig()).period_seconds
    ticks = max(
        1,
        round(run_slice / period),
    )
    if ticks > MAX_BOARD_TIMER_TICKS_PER_CYCLE:
        raise ValueError(
            "host throttle interval requires too many virtual timer ticks: "
            f"{ticks}"
        )
    return ticks


def read_duart_timer_config(rsp: RspClient, x1_hz: int = DEFAULT_X1_HZ) -> DuartTimerConfig:
    """Read firmware-written ACR, preset and IVR from the machine=none backing."""
    registers = rsp.read_memory(DUART_TIMER_ACR, DUART_TIMER_IVR - DUART_TIMER_ACR + 1)
    return DuartTimerConfig.from_registers(
        registers[0], registers[DUART_TIMER_CTUR - DUART_TIMER_ACR],
        registers[DUART_TIMER_CTLR - DUART_TIMER_ACR], registers[-1], x1_hz=x1_hz,
    )


def qemu_tcg_accelerator(fast_tb: bool) -> str:
    """Use normal translation blocks unless precise single-insn fallback is requested."""
    return "tcg" if fast_tb else "tcg,one-insn-per-tb=on"


def foreground_timer_quantum(run_slice: float, config: DuartTimerConfig) -> float:
    """Give the main program a turn between small hardware IRQ batches.

    The configured slice stays an upper bound, not a reason to replay 40
    interrupts before the callback consumer or physical key reader runs.
    QEMU's icount instruction cap is independent and remains unchanged.
    """
    return min(run_slice, config.period_seconds)


def interrupts_unmasked(sr: int) -> bool:
    """The host must not force an IRQ through an original critical section."""
    return (sr & SR_INTERRUPT_MASK) == 0


def nested_board_schedule(run_slice: float, ticks: int, elapsed: float) -> float:
    """Let a blocked original ISR receive its next budgeted DUART tick."""
    ticks = max(1, ticks)
    if elapsed >= run_slice:
        return run_slice / ticks
    return min(run_slice / ticks, run_slice - elapsed)


def wait_until(
    deadline: float,
    *,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> None:
    """Wait until a monotonic deadline, tolerating early host wakeups."""
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleeper(remaining)


def send_serial_frame(
    sock: socket.socket,
    frame: bytes,
    *,
    seconds_per_byte: float = UART_FRAME_SECONDS,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> float:
    """Send one byte per 8-N-1 interval and return elapsed wire time."""
    started_at = clock()
    for index, byte in enumerate(frame):
        if index:
            wait_until(
                started_at + index * seconds_per_byte,
                clock=clock,
                sleeper=sleeper,
            )
        sock.sendall(bytes([byte]))
    return clock() - started_at


def initial_frame_retry_due(
    *,
    response_pending: bool,
    attempts: int,
    now: float,
    retry_at: float,
) -> bool:
    """Return whether the captured DB INIT may be repeated on the serial wire."""
    return (
        not response_pending
        and attempts < INITIAL_FRAME_MAX_ATTEMPTS
        and now >= retry_at
    )


def initvideo_retry_confirmed(
    stage: str, *, response_pending: bool, systeminfo_seen: bool
) -> bool:
    """A short guest ACK alone does not prove either INITVIDEO was consumed."""
    if stage not in ("startup", "later"):
        raise ValueError(f"unknown INITVIDEO stage: {stage}")
    return systeminfo_seen


def observe_guest_systeminfo(tail: bytearray, value: int) -> bool:
    """Detect the M90 system-info prefix across COM3 receive boundaries."""
    tail.append(value & 0xFF)
    del tail[:-4]
    return tail == b"M90-"


def idle_protocol_diagnostic_due(
    *,
    first_frame_reported: bool,
    retry_active: bool,
    response_pending: bool,
    now: float,
    last_uart_activity_at: float,
    next_diagnostic_at: float,
) -> bool:
    """Keep idle heartbeats live while a read watchpoint remains armed."""
    return (
        first_frame_reported
        and not retry_active
        and not response_pending
        and now - last_uart_activity_at >= IDLE_DIAGNOSTIC_SECONDS
        and now >= next_diagnostic_at
    )


def should_publish_pending_uart_byte(
    pending_count: int,
    *,
    rx_status_watch: bool,
    rx_data_watch: bool,
    rx_ready_seen: bool,
) -> bool:
    """Publish RX data only when no prior UART byte is still observable."""
    return (
        pending_count > 0
        and not rx_status_watch
        and not rx_data_watch
        and not rx_ready_seen
    )


def qemu_creation_flags() -> int:
    """Run at normal host priority; icount bounds only the emulated database."""
    return 0


def interrupt_after_run_slice(rsp: RspClient) -> str:
    """Interrupt QEMU, then leave a safe timeout for debugger control I/O."""
    rsp.set_timeout(RSP_INTERRUPT_REPLY_TIMEOUT)
    try:
        return rsp.interrupt()
    finally:
        rsp.set_timeout(RSP_CONTROL_REPLY_TIMEOUT)


def is_run_slice_interrupt(reply: str) -> bool:
    """Only SIGINT ends a slice; delayed hardware stops still need dispatch.

    The target may already have hit a watchpoint when the socket times out.
    Its pending SIGTRAP reply then wins the race with our Ctrl-C. Treating
    that reply as a timer stop silently loses the SCC/UART transaction.
    """
    kind, _ = parse_stop_address(reply)
    return kind is None and reply[:3] in ("S02", "T02")


def parse_stop_address(reply: str) -> tuple[str | None, int | None]:
    for kind in ("rwatch", "watch", "awatch"):
        marker = kind + ":"
        start = reply.find(marker)
        if start >= 0:
            value = reply[start + len(marker):].split(";", 1)[0]
            return kind, int(value, 16)
    return None, None


def should_forward_tx(pc: int, value: bytes) -> bool:
    if pc < DATABASE_RUNTIME_START:
        return False
    return not (pc in RUNTIME_UART_CLEAR_STOP_PCS and value == b"\x00")


def board_latch_feedback(pc: int) -> int | None:
    return BOARD_LATCH_FEEDBACK_BY_STOP_PC.get(pc)


def apply_board_port_strobe(current: int, address: int, mask: int) -> int:
    """Apply the firmware-visible set/clear alias to the board input byte."""
    if address == BOARD_PORT_SET:
        return (current | mask) & 0xFF
    if address == BOARD_PORT_CLEAR:
        return (current & ~mask) & 0xFF
    raise ValueError(f"unknown board port strobe address: {address:08X}")


def apply_cabinet_inputs(current: int, *, door_closed: bool) -> int:
    """Overlay externally driven cabinet inputs on the board port byte."""
    if door_closed:
        return (current | BOARD_DOOR_CLOSED_MASK) & 0xFF
    return (current & ~BOARD_DOOR_CLOSED_MASK) & 0xFF


def board_timer_status(current: int, asserted: bool) -> int:
    """Assert or clear only the proven board-timer pending bit."""
    if asserted:
        return (current | BOARD_TIMER_PENDING) & 0xFF
    return (current & ~BOARD_TIMER_PENDING) & 0xFF


def board_interrupt_status(
    current: int, *, timer_asserted: bool, scc_tx_active: bool,
    scc_a_rx_pending: bool = False,
) -> int:
    """Publish timer and SCC status without inventing an SCC-A RX byte."""
    value = board_timer_status(current, timer_asserted)
    if scc_tx_active:
        value |= BOARD_SCC_PENDING
    else:
        value &= ~BOARD_SCC_PENDING
    if scc_a_rx_pending:
        value |= BOARD_SCC_A_RX_PENDING
    else:
        value &= ~BOARD_SCC_A_RX_PENDING
    return value & 0xFF


def board_service_target(vector: int = BOARD_TIMER_VECTOR) -> tuple[int, int, int]:
    """Select the actual firmware-programmed IVR, not a pending command byte."""
    if vector == BOARD_SCC_A_VECTOR:
        return BOARD_SCC_A_VECTOR, BOARD_SCC_A_HANDLER, BOARD_SCC_A_RTE_PC
    if vector != BOARD_TIMER_VECTOR:
        raise ValueError(f"unsupported board interrupt vector: {vector}")
    return BOARD_TIMER_VECTOR, BOARD_TIMER_HANDLER, BOARD_TIMER_RTE_PC


def can_nest_board_timer(pc: int, sr: int, depth: int) -> bool:
    """Advance original timer waits entered by its unmasked timer ISR."""
    return (
        depth == 1
        and pc in {
            *TIMER_QUEUE_WAIT_PCS,
            BOARD_TICK_COUNTER_WAIT_PC,
            *UART_REPLY_WAIT_PCS,
            *HOPPER_QUEUE_WAIT_PCS,
        }
        and (sr & SR_INTERRUPT_MASK) == 0
    )


def expired_timer_queue_slot(pc: int, sr: int, a0: int) -> int | None:
    """Return the exact original delay slot that may expire after a run slice.

    0x6042A stores a delay in one of six queue longs and spins at 0x60442
    until 0x73450 decrements it. The bridge has already allowed one bounded
    icount-bounded run slice here. Expiring only that validated slot avoids
    replaying many slow GDB-mediated board scans for a 100 ms firmware delay.
    """
    queue_end = TIMER_QUEUE_BASE + TIMER_QUEUE_SLOTS * 4
    return (
        a0
        if pc in TIMER_QUEUE_WAIT_PCS
        and (sr & SR_INTERRUPT_MASK) == 0
        and TIMER_QUEUE_BASE <= a0 < queue_end
        and (a0 - TIMER_QUEUE_BASE) % 4 == 0
        else None
    )


def may_fast_forward_timer_wait(board_isr_depth: int, scc_state: int, *, cabinet_work_pending: bool = False) -> bool:
    """Keep original timer IRQs active while board or SCC work is pending."""
    return not cabinet_work_pending and board_isr_depth == 0 and not (
        scc_state & (BOARD_SCC_TX_ACTIVE | BOARD_SCC_REPLY_WAITING)
    )


def expired_device_discovery_timer_slot(
    pc: int,
    sr: int,
    a0: int,
    d0: int,
    queued_delay: int,
) -> int | None:
    """Return slot zero only for the proven absent-device polling timeout."""
    return (
        TIMER_QUEUE_BASE
        if pc in DEVICE_DISCOVERY_TIMER_WAIT_PCS
        and (sr & SR_INTERRUPT_MASK) == 0
        and a0 == TIMER_QUEUE_BASE
        and 0 < queued_delay <= DEVICE_DISCOVERY_TIMER_MAX_TICKS
        and d0 == queued_delay
        else None
    )


def read_hopper_diagnostics(rsp: RspClient) -> dict[str, int]:
    """Sample original firmware state without changing payout hardware state."""
    return {
        name: int.from_bytes(rsp.read_memory(address, width), "big")
        for name, address, width in HOPPER_DIAGNOSTIC_FIELDS
    }


def idle_hopper_sensor_bit(counter: int, phase: int) -> int:
    """Predict the phase used by 0xE0682 after its counter update.

    0xE0642 increments the 0..20 counter, clearing the phase at zero and
    setting it at ten. The board scan happens before that update.
    """
    if counter >= 20:
        phase = 0
    elif counter == 9:
        phase = 1
    return 0 if phase else HOPPER_SENSOR_IDLE_MASK


def publish_idle_hopper_sensors(rsp: RspClient) -> bytes:
    """Expose the idle sensor level for the firmware's next test phase."""
    modulation = rsp.read_memory(HOPPER_MODULATION_COUNTER, 3)
    counter, phase = modulation[0], modulation[2]
    bit = idle_hopper_sensor_bit(counter, phase)
    current = rsp.read_memory(HOPPER_SENSOR_SHADOW, 2)
    idle = bytes(
        (value & ~HOPPER_SENSOR_IDLE_MASK) | bit for value in current
    )
    if idle != current:
        rsp.write_memory(HOPPER_SENSOR_SHADOW, idle)
    return idle


def publish_released_return_button(rsp: RspClient) -> tuple[int, int]:
    """Publish the firmware-level released state for the absent cabinet key."""
    current = rsp.read_memory(RETURN_BUTTON_CURRENT, 1)[0]
    released = current | RETURN_BUTTON_MASK
    if released != current:
        rsp.write_memory(RETURN_BUTTON_CURRENT, bytes([released]))

    event = rsp.read_memory(RETURN_BUTTON_EVENT, 1)[0]
    cleared_event = event & ~RETURN_BUTTON_MASK
    if cleared_event != event:
        rsp.write_memory(RETURN_BUTTON_EVENT, bytes([cleared_event]))
    return released, cleared_event


def publish_cabinet_buttons(
    rsp: RspClient, pressed: set[str], pending_edges: set[str],
    released: set[str] | None = None,
    mapping_cache: dict[int, dict[str, int]] | None = None,
) -> dict[str, int]:
    """Apply GUI switch levels after the owner's physical board scan.

    The firmware maps logical IDs through the active profile's original
    16-word table.  We only model single-bit, active-low board switches;
    unsupported mappings are left untouched rather than guessed.
    """
    profile = rsp.read_memory(MP_STATE_ADDRESS, 1)[0]
    if profile >= KEY_TABLE_PROFILES:
        raise RuntimeError(f"unknown cabinet key profile {profile}")
    if mapping_cache is None:
        mapping_cache = {}
    if profile not in mapping_cache:
        row = rsp.read_memory(
            KEY_TABLE_BASE + profile * KEY_TABLE_STRIDE, KEY_TABLE_STRIDE
        )
        mapping_cache[profile] = {
            name: int.from_bytes(row[key_id * 2:key_id * 2 + 2], "big")
            for name, key_id in KEY_IDS.items()
        }
    mappings = mapping_cache[profile]
    locations = {name: key_location(mapping) for name, mapping in mappings.items()}
    for name in pressed:
        if locations[name] is None:
            raise RuntimeError(f"unsupported mapping for pressed {name}: {mappings[name]:04X}")
    addresses = [location[0] for location in locations.values() if location is not None]
    # Include the independently released return contact even if another
    # cabinet profile maps its logical payout key elsewhere.
    addresses.append(RETURN_BUTTON_CURRENT)
    start, end = min(addresses), max(addresses) + 1
    event_start = start + (KEY_EVENT_BASE - KEY_CURRENT_BASE)
    current_before = rsp.read_memory(start, end - start)
    event_before = rsp.read_memory(event_start, end - start)
    current_bytes, event_bytes = bytearray(current_before), bytearray(event_before)
    if "auszahlung" not in pressed:
        index = RETURN_BUTTON_CURRENT - start
        current_bytes[index] |= RETURN_BUTTON_MASK
        event_bytes[index] &= ~RETURN_BUTTON_MASK
    # machine=none returns the board's output byte as its input sample. Its
    # absent switches therefore appear continuously active-low. Publish the
    # idle level for every switch on every scan, not just GUI-touched keys.
    for name in KEY_IDS:
        if name == "auszahlung" and name not in pressed:
            continue  # already released above
        location = locations[name]
        if location is None:
            continue
        current_address, event_address, mask = location
        index = current_address - start
        current = current_bytes[index]
        current_bytes[index] = current & ~mask if name in pressed else current | mask
        event = event_bytes[event_address - event_start]
        if name in pending_edges:
            desired_event = event | mask
            pending_edges.discard(name)
        elif name not in pressed:
            # The floating scanner invents a new press edge on each tick.
            desired_event = event & ~mask
        else:
            # While held, the original accessor at 0x60186 consumes the edge.
            desired_event = event
        event_bytes[event_address - event_start] = desired_event
    # QEMU is stopped throughout this publication. Two bank reads/writes
    # preserve every unrelated byte and bit without per-key debugger trips.
    if current_bytes != current_before:
        rsp.write_memory(start, bytes(current_bytes))
    if event_bytes != event_before:
        rsp.write_memory(event_start, bytes(event_bytes))
    if released is not None:
        released.clear()
    return mappings


BUTTON_PULSE_BOARD_SCANS = 20
MAX_UART_SERVICE_BURST = 32
TOUCH_STATUS_POLL_SECONDS = 0.01


def should_drain_uart_before_board(work_pending: bool, board_ticks: int, burst: int) -> bool:
    """A busy serial link must yield even when the current IRQ batch is empty."""
    return work_pending and burst < MAX_UART_SERVICE_BURST


def uart_work_pending(rsp: RspClient, receive_pending: bool, tx_ready_pending: bool = False) -> bool:
    """Poll the original TX ring in one exchange; never run an idle UART IRQ."""
    if receive_pending or tx_ready_pending:
        return True
    pointers = rsp.read_memory(0x001EBBB4, 8)
    return pointers[:4] != pointers[4:]


def consume_cabinet_button_events(
    events, pressed: set[str], pending_edges: set[str], released: set[str],
    release_after_scans: dict[str, int],
) -> list[dict]:
    """Stage one edge per key at the board scan, never collapse quick clicks."""
    consumed = []
    seen: set[str] = set()
    for _ in range(len(KEY_IDS)):
        try:
            event = events.pop_button(pressed | released, seen)
        except queue.Empty:
            break
        name = event["name"]
        if event["down"]:
            pending_edges.add(name)
            pressed.add(name)
        elif name in pressed:
            release_after_scans[name] = BUTTON_PULSE_BOARD_SCANS
        else:
            released.add(name)
        consumed.append(event)
    return consumed


def advance_button_pulses(
    pressed: set[str], pending_edges: set[str], released: set[str],
    release_after_scans: dict[str, int],
    *, foreground_pending: set[str] | None = None,
) -> set[str]:
    """Hold quick clicks across both board scans and foreground execution.

    Many scans can run in one IRQ batch without the main program reading
    its keys. A scan count alone must not expire a pulse inside that batch.
    """
    completed = set()
    for name, remaining in tuple(release_after_scans.items()):
        if name in pending_edges:
            continue
        if remaining <= 1:
            if foreground_pending is not None and name in foreground_pending:
                release_after_scans[name] = 1
                continue
            completed.add(name)
            del release_after_scans[name]
        else:
            release_after_scans[name] = remaining - 1
    pressed.difference_update(completed)
    released.update(completed)
    return completed


def can_nest_uart_reply(
    pc: int, sr: int, depth: int, outer_rte_pc: int,
    response_pending: bool,
) -> bool:
    """Let the original UART consume a real PC reply during its board wait."""
    return (
        depth == 1
        and outer_rte_pc == BOARD_TIMER_RTE_PC
        and pc in UART_REPLY_WAIT_PCS
        and (sr & SR_INTERRUPT_MASK) == 0
        and response_pending
    )


def can_nest_scc_a_tx(
    pc: int, sr: int, depth: int, outer_rte_pc: int,
    command: int, status: int,
) -> bool:
    """Release the firmware's command wait inside its unmasked timer ISR.

    0x7075C waits for the command byte to be cleared by vector 65's original
    TX-ready dispatcher. The timer ISR unmasks interrupts at 0x74E36, so the
    physical SCC can interrupt this wait. No RX-ready byte is fabricated.
    """
    return (
        depth == 1
        and outer_rte_pc == BOARD_TIMER_RTE_PC
        and pc == SCC_A_COMMAND_WAIT_PC
        and (sr & SR_INTERRUPT_MASK) == 0
        and command != 0
        and (status & BOARD_SCC_A_TX_READY) != 0
    )


def touch_controller_response(request: bytes) -> bytes | None:
    """Return the response of the 3M controller identified in the firmware."""
    return VirtualTouchController.initial_response(request)


def inject_touch_controller_response(rsp: RspClient, response: bytes) -> bool:
    """Put one controller reply into the original firmware RX ring."""
    if not response or len(response) > TOUCH_UART_RX_BUFFER_SIZE:
        raise ValueError("invalid touch-controller response length")
    status = rsp.read_memory(TOUCH_UART_RX_COUNT, 2)
    if status[0] != 0:
        return False
    publish_touch_controller_response(rsp, response, status[1])
    return True


def publish_touch_controller_response(rsp: RspClient, response: bytes, read_index: int) -> None:
    """Publish to an already-checked empty RX ring while the CPU is halted."""
    if not response or len(response) > TOUCH_UART_RX_BUFFER_SIZE:
        raise ValueError("invalid touch-controller response length")
    if read_index >= TOUCH_UART_RX_BUFFER_SIZE:
        raise RuntimeError(
            f"touch-controller RX index outside ring: {read_index}"
        )
    first = min(len(response), TOUCH_UART_RX_BUFFER_SIZE - read_index)
    rsp.write_memory(
        TOUCH_UART_RX_BUFFER + read_index, response[:first]
    )
    if first < len(response):
        rsp.write_memory(TOUCH_UART_RX_BUFFER, response[first:])
    # Publish the count last so the emulated CPU never sees partial data.
    rsp.write_memory(TOUCH_UART_RX_COUNT, bytes([len(response)]))


def log_touch_controller_events(controller: VirtualTouchController) -> None:
    for event in controller.events:
        print(event, flush=True)
    controller.events.clear()


def service_touch_command(rsp: RspClient, controller: VirtualTouchController,
                          request: bytes) -> tuple[bool, bytes | None]:
    """Reply exactly once; busy RX must not start/reset calibration early."""
    status = rsp.read_memory(TOUCH_UART_RX_COUNT, 2)
    if status[0]:
        return False, None
    if status[1] >= TOUCH_UART_RX_BUFFER_SIZE:
        raise RuntimeError("touch-controller RX index outside ring")
    response = controller.command(request)
    if response is not None:
        publish_touch_controller_response(rsp, response, status[1])
    return True, response


def deliver_touch_packet(rsp: RspClient, controller: VirtualTouchController,
                         stream: "TouchPacketStream") -> tuple | None:
    """Consume one input only at an empty native RX ring; never replay a point."""
    if not stream.packets:
        return None
    if controller.calibration_session and not controller.calibrating:
        # After point 2/failure, wait for native calibration teardown. It
        # reinitializes the receiver; do not leak tablet packets into its ACKs.
        return None
    status = rsp.read_memory(TOUCH_UART_RX_COUNT, 2)
    if status[0] != 0:
        return None
    if status[1] >= TOUCH_UART_RX_BUFFER_SIZE:
        raise RuntimeError("touch-controller RX index outside ring")
    x, y, down, _queued_packet = stream.packets[0]
    wide = rsp.read_memory(TOUCH_WIDE_MODE_ADDRESS, 1)[0] == 1
    calibration_input = controller.calibrating
    response = controller.touch(x, y, down, wide=wide)
    if response is not None:
        publish_touch_controller_response(rsp, response, status[1])
    stream.packets.popleft()
    point = None if calibration_input else controller.screen_point(x, y)
    return x, y, down, response, point, calibration_input


def complete_initvideo_board_profile(frame: bytes, when: dt.datetime) -> bytes:
    """Complete only RTC/cabinet fields missing from QEMU machine=none."""
    completed = bytearray(complete_initvideo_clock(frame, when))
    completed[34:38] = INITVIDEO_DEVICE_FIELDS
    return bytes(completed)


def complete_initvideo_clock(frame: bytes, when: dt.datetime) -> bytes:
    """Replace only the absent board RTC fields in an original INITVIDEO."""
    if (
        len(frame) != INITVIDEO_FRAME_LENGTH
        or frame[:4] != INITVIDEO_PREFIX
        or frame[-1:] != b"\x04"
    ):
        raise ValueError("unexpected INITVIDEO frame layout")
    if frame[4:24] != INITVIDEO_OWNER_FIELDS:
        raise ValueError("owner INITVIDEO identity/content fields changed")
    # The owner's display/profile bytes at 30..33 change after the auxiliary
    # board identifies itself. They are not RTC bytes and must pass through.
    completed = bytearray(frame)
    completed[24:30] = when.year.to_bytes(2, "little") + bytes((when.month, when.day, when.hour, when.minute))
    return bytes(completed)


def initvideo_time_text(frame: bytes) -> str:
    """Report the minute-resolution calendar actually sent on the wire."""
    year, month, day, hour, minute = struct.unpack("<HBBBB", frame[24:30])
    return dt.datetime(year, month, day, hour, minute).isoformat(timespec="minutes")


def initvideo_frame_complete(frame: bytes | bytearray) -> bool:
    """INITVIDEO is fixed-size; an embedded EOT is ordinary payload data."""
    if len(frame) > INITVIDEO_FRAME_LENGTH:
        raise ValueError("INITVIDEO frame exceeds fixed length")
    if not INITVIDEO_PREFIX.startswith(frame[:len(INITVIDEO_PREFIX)]):
        raise ValueError("unexpected first INITVIDEO prefix")
    return len(frame) == INITVIDEO_FRAME_LENGTH


class InitvideoClockForwarder:
    """Hold only INITVIDEO frames to complete their selected RTC date."""

    def __init__(self, clock: Callable[[], dt.datetime]) -> None:
        self.buffer = bytearray()
        self.clock = clock

    def feed(self, value: bytes) -> tuple[bytes, bool]:
        if len(value) != 1:
            raise ValueError("expected one database UART byte")
        if not self.buffer and value != b"\x01":
            return value, False
        self.buffer.extend(value)
        if len(self.buffer) <= len(INITVIDEO_PREFIX):
            if INITVIDEO_PREFIX.startswith(self.buffer):
                return b"", False
            unchanged = bytes(self.buffer)
            self.buffer.clear()
            return unchanged, False
        if len(self.buffer) < INITVIDEO_FRAME_LENGTH:
            return b"", False
        completed = complete_initvideo_clock(bytes(self.buffer), self.clock())
        self.buffer.clear()
        return completed, True


class TouchClickForwarder:
    """Preserve DB touch events, supplying coordinates when its parser emits 0,0.

    The owner's VidComLog has the same Menue GO and real little-endian X/Y
    coordinates. Under our virtual serial controller the original firmware
    currently emits 0,0 for every press, while its -1,-1 release is intact.
    Only that observed zero-coordinate press is completed here.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(
        self, value: bytes, touch_point: tuple[int, int] | None
    ) -> tuple[bytes, tuple[int, int] | None]:
        if len(value) != 1:
            raise ValueError("expected one database UART byte")
        if not self.buffer and value != b"\x01":
            return value, None
        self.buffer.extend(value)
        if len(self.buffer) <= len(TOUCH_CLICK_MENUE_PREFIX):
            if TOUCH_CLICK_MENUE_PREFIX.startswith(self.buffer):
                return b"", None
            unchanged = bytes(self.buffer)
            self.buffer.clear()
            return unchanged, None
        if len(self.buffer) < TOUCH_CLICK_FRAME_LENGTH:
            return b"", None
        frame = bytearray(self.buffer)
        self.buffer.clear()
        if (
            frame[-1] == 0x04
            and frame[6:10] == b"\x00\x00\x00\x00"
            and touch_point is not None
            and touch_point != (0, 0)
        ):
            x, y = touch_point
            if not 0 <= x < 800 or not 0 <= y < 600:
                raise ValueError("touch point outside lower cabinet display")
            frame[6:10] = x.to_bytes(2, "little") + y.to_bytes(2, "little")
            return bytes(frame), touch_point
        return bytes(frame), None


class TouchPacketStream:
    """One contact edge, real drag positions, then immediate queued release."""

    MAX_PACKETS = 64

    def __init__(self) -> None:
        self.packets: deque[tuple[int, int, bool, bytes]] = deque()
        self.active_point: tuple[int, int] | None = None

    def _append(self, x: int, y: int, down: bool) -> None:
        self.packets.append((x, y, down, format_tablet_packet(x, y, down)))

    def can_accept_contact(self) -> bool:
        # Leave room for the current contact's first sample, two final drag
        # positions and release. More contacts stay in the bounded ingress.
        return self.active_point is not None or len(self.packets) <= self.MAX_PACKETS - 4

    def _trim_contact(self) -> None:
        # A slow firmware consumer must not work through a long queue of stale
        # held/move reports before seeing the release. Keep the first contact
        # sample and the two most recent positions of this contact only.
        queued = list(self.packets)
        start = len(queued)
        for index in range(len(queued) - 1, -1, -1):
            if not queued[index][2]:
                start = index + 1
                break
        else:
            start = 0
        contact = queued[start:]
        if len(contact) > 3:
            self.packets = deque(queued[:start] + [contact[0], *contact[-2:]])

    def _finish_release(self, x: int, y: int) -> None:
        self._trim_contact()
        self._append(x, y, False)
        self.active_point = None

    def request(self, x: int, y: int, down: bool) -> None:
        if down:
            if self.active_point == (x, y):
                return
            if not self.can_accept_contact():
                raise queue.Full("touch contact queue is full")
            self.active_point = (x, y)
            self._append(x, y, True)
            self._trim_contact()
            return
        if self.active_point is None:
            return
        self._finish_release(x, y)


def uart_state_is_ready(uart_state: int) -> bool:
    """Recognize the ready word written by the original UART initializer."""
    return uart_state == UART_STATE_READY


def format_zero_exception_frame(sr: int, pc: int, vector: int) -> bytes:
    """Build a 68020 format-zero exception frame for an emulated interrupt."""
    return (
        (sr & 0xFFFF).to_bytes(2, "big")
        + (pc & 0xFFFFFFFF).to_bytes(4, "big")
        + ((vector * 4) & 0x0FFF).to_bytes(2, "big")
    )


def inject_interrupt(
    rsp: RspClient, vector: int, handler: int,
    *, snapshot: M68kRegisterSnapshot | None = None,
) -> tuple[int, int]:
    snapshot = snapshot if snapshot is not None else rsp.read_register_snapshot()
    registers = snapshot.core_u32
    pc = registers[REG_PC]
    sr = registers[REG_SR]
    stack = registers[REG_A7] - 8
    rsp.write_memory(stack, format_zero_exception_frame(sr, pc, vector))
    # Runtime is already supervisor-only. Mask nested IRQs while the handler
    # runs; RTE restores the exact interrupted SR from the frame.
    rsp.write_registers_u32({
        REG_A7: stack, REG_SR: (sr | 0x2700) & 0xFFFF, REG_PC: handler,
    }, snapshot=snapshot)
    return pc, sr


def inject_board_tick(
    rsp: RspClient, target: tuple[int, int, int],
    *, snapshot: M68kRegisterSnapshot | None = None,
) -> tuple[int, int]:
    """Assert timer hardware and enter its original vector, without firmware edits."""
    status = rsp.read_memory(BOARD_TIMER_STATUS, 1)[0]
    scc_active = bool(rsp.read_memory(BOARD_SCC_STATE, 1)[0] & BOARD_SCC_TX_ACTIVE)
    rsp.write_memory(BOARD_TIMER_STATUS, bytes([board_interrupt_status(
        status, timer_asserted=True, scc_tx_active=scc_active,
    )]))
    return inject_interrupt(rsp, target[0], target[1], snapshot=snapshot)


def set_watchpoint(
    rsp: RspClient,
    kind: int,
    address: int,
    enabled: bool,
    length: int = 1,
) -> None:
    command = ("Z" if enabled else "z") + f"{kind},{address:x},{length:x}"
    response = rsp.command(command)
    if response != "OK":
        raise RuntimeError(f"QEMU rejected watchpoint {command}: {response!r}")


def step_past_breakpoint(rsp: RspClient, address: int) -> str:
    """Execute the instruction under a persistent QEMU breakpoint once."""
    set_watchpoint(rsp, 0, address, False)
    try:
        return rsp.command("s")
    finally:
        set_watchpoint(rsp, 0, address, True)


def run_original_uart_initializer(rsp: RspClient) -> int:
    """Execute the owner's UART initializer while preserving CPU context."""
    saved_registers = list(rsp.read_registers_u32())
    stack = saved_registers[REG_A7] - 4
    saved_stack_bytes = rsp.read_memory(stack, 4)
    breakpoint_set = False
    try:
        rsp.write_memory(
            stack, UART_INIT_RETURN_SENTINEL.to_bytes(4, "big")
        )
        rsp.write_register_u32(REG_A7, stack)
        rsp.write_register_u32(REG_PC, UART_INIT_ENTRY)
        set_watchpoint(rsp, 0, UART_INIT_RETURN_SENTINEL, True)
        breakpoint_set = True
        rsp.set_timeout(UART_INIT_CALL_TIMEOUT)
        reply = rsp.command("c")
        returned_pc = rsp.read_register_u32(REG_PC)
        if returned_pc != UART_INIT_RETURN_SENTINEL:
            raise RuntimeError(
                "original UART initializer stopped unexpectedly: "
                f"PC={returned_pc:08X} reply={reply!r}"
            )
        uart_state = int.from_bytes(
            rsp.read_memory(UART_STATE_ADDRESS, 2), "big"
        )
        if not uart_state_is_ready(uart_state):
            raise RuntimeError(
                "original UART initializer returned without ready magic: "
                f"{uart_state:04X}"
            )
        return uart_state
    finally:
        try:
            if breakpoint_set:
                set_watchpoint(rsp, 0, UART_INIT_RETURN_SENTINEL, False)
        finally:
            try:
                rsp.write_memory(stack, saved_stack_bytes)
                for index, value in enumerate(saved_registers):
                    rsp.write_register_u32(index, value)
            finally:
                rsp.set_timeout(60.0)


def connect_com3(host: str, port: int, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setblocking(False)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def load_memory(rsp: RspClient, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 1024):
        rsp.write_memory(address + offset, data[offset:offset + 1024])


def run_original_factory_reset(rsp: RspClient, runtime: bytes, entrypoint: int) -> None:
    """Execute the selected Factory module on the fresh virtual SRAM.

    The owner module clears SRAM, invalidates the old image checksum, writes
    INIT into QSPI RAM, and calls the loader at 0x408. Stop at that handoff,
    then let the caller program config/firmware in their documented order.
    Never replace the firmware's initialization flags with guessed values.
    """
    handoff = 0x408
    load_memory(rsp, 0x1000, runtime)
    rsp.write_register_u32(REG_A7, 0x001FFF80)
    rsp.write_register_u32(REG_PC, entrypoint)
    set_watchpoint(rsp, 0, handoff, True)
    rsp.set_timeout(15.0)
    try:
        reply = rsp.command("c")
        pc = rsp.read_register_u32(REG_PC)
        if pc != handoff:
            raise RuntimeError(f"Factory module did not return to loader: pc={pc:08X} stop={reply}")
        marker = rsp.read_memory(0x00FFFD00, 4)
        checksum = rsp.read_memory(0x1000, 4)
        if marker != b"INIT" or checksum != bytes(4):
            raise RuntimeError("Factory module did not prepare native initialization")
        print("DB_FACTORY_EXECUTED source=original-module loader_handoff=00000408 marker=INIT", flush=True)
    finally:
        set_watchpoint(rsp, 0, handoff, False)
        rsp.set_timeout(60.0)


def receive_com3(
    sock: socket.socket,
    pending: deque[int],
    stop: threading.Event,
    errors: list[BaseException],
) -> None:
    """Continuously drain QEMU COM3 while the m68k CPU is running."""
    try:
        while not stop.is_set():
            readable, _, _ = select.select([sock], [], [], 0.5)
            if not readable:
                continue
            chunk = sock.recv(4096)
            if not chunk:
                raise Com3Disconnected("XP QEMU closed the COM3 socket")
            pending.extend(chunk)
            print(f"COM3_TO_DB {chunk.hex(' ').upper()}", flush=True)
    except (ConnectionResetError, ConnectionAbortedError) as exc:
        if not stop.is_set():
            errors.append(Com3Disconnected(f"XP QEMU reset COM3: {exc}"))
    except BaseException as exc:
        if not stop.is_set():
            errors.append(exc)


def ensure_com3_receiver_alive(errors: list[BaseException]) -> None:
    """Propagate a closed guest COM3 link even while firmware is not polling RX."""
    if errors:
        raise errors[0]


def read_register_snapshot(rsp: RspClient) -> str:
    names = [*(f"D{i}" for i in range(8)), *(f"A{i}" for i in range(8)), "SR", "PC"]
    values = rsp.read_registers_u32()
    return " ".join(f"{name}={value:08X}" for name, value in zip(names, values))


def read_rtc_fault_snapshot(rsp: RspClient, when: dt.datetime) -> str:
    """Read the native F_UHR call context, never change calendar or error flags."""
    registers = rsp.read_registers_u32()
    stack = registers[REG_A7]
    calendar_pointer = registers[13]  # A5: calendar pointer in FUN_00079f42
    source = (rsp.read_memory(stack, 4).hex().upper()
              if 0 <= stack <= 0x200000 - 4 else "unavailable")
    calendar = (rsp.read_memory(calendar_pointer, 7).hex().upper()
                if 0x10000 <= calendar_pointer <= 0x200000 - 7 else "unavailable")
    return (
        f"rtc={when.isoformat()} source_return={source} "
        f"calendar_ptr={calendar_pointer:08X} calendar={calendar} "
        f"d0={registers[0]:08X} d1={registers[1]:08X} "
        f"timestamp={rsp.read_memory(0x001E2EB8, 4).hex().upper()} "
        f"invalid_flag={rsp.read_memory(0x001EFFFE, 1).hex().upper()} "
        f"cached_calendar={rsp.read_memory(0x001F8110, 7).hex().upper()} "
        f"header_year={rsp.read_memory(0x108C, 2).hex().upper()}"
    )


def run_bridge(args: argparse.Namespace) -> int:
    validate_timer_interval(args.timer_interval)
    admission_eeprom = None
    if args.admission_eeprom is not None:
        admission_eeprom = args.admission_eeprom.read_bytes()
        admission_number, card_model = inspect_eeprom(admission_eeprom)
        print(
            "DB_ADMISSION_CARD_LOADED "
            f"number_ending={admission_number[-4:]} "
            f"model={card_model.hex(' ').upper()} "
            f"eeprom_bytes={len(admission_eeprom)}",
            flush=True,
        )
    timer_config = DuartTimerConfig(x1_hz=args.duart_x1_hz)
    timer_budget = DuartWallTimer(timer_config)
    timer_target = board_service_target(timer_config.vector)
    door_closed = not args.door_open
    loader = args.loader.read_bytes()
    if len(loader) > MAX_LOADER_SIZE:
        raise ValueError("loader exceeds safety size limit")
    loader_digest = hashlib.sha256(loader).hexdigest().upper()
    if loader_digest != args.expected_loader_sha256.replace(" ", "").upper():
        raise ValueError("loader SHA-256 mismatch")
    runtime, runtime_report = prepare_runtime(
        args.database,
        args.expected_database_sha256,
        d3=args.d3,
        runtime_dump_path=args.runtime_dump,
    )
    config_writes, config_digest = prepare_config_writes(
        args.config, args.expected_config_sha256,
        args.d3 if args.d3 is not None else 0xD27B7159,
    )
    factory_runtime = None
    factory_path = getattr(args, "factory", None)
    if factory_path is not None:
        factory_runtime, factory_entrypoint = prepare_factory_runtime(
            factory_path, args.expected_factory_sha256,
            args.d3 if args.d3 is not None else 0xD27B7159,
        )
    if 0x1000 + len(runtime) > CONFIG_CLEAR_START:
        raise ValueError("database runtime overlaps the config SRAM region")
    entrypoint = int(runtime_report["entrypoint"], 16)
    print(
        "REAL_DATABASE_RUNTIME_READY "
        f"runtime_sha256={runtime_report['runtime_sha256']} "
        f"entry={runtime_report['entrypoint']} source={runtime_report['runtime_source']}",
        flush=True,
    )

    loader_device = (
        f"loader,file={args.loader.resolve()},addr=0x400,force-raw=on,cpu-num=0"
    )
    command = [
        str(args.qemu), "-machine", "none", "-cpu", "m68020", "-m", "16M",
        "-accel", qemu_tcg_accelerator(args.fast_tb),
        "-icount", f"shift={args.icount_shift},align=on,sleep=on",
        "-device", loader_device, "-display", "none", "-monitor", "none",
        "-serial", "none", "-S", "-gdb", f"tcp:127.0.0.1:{args.gdb_port}",
    ]
    controls = CabinetControlServer(args.control_port)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=qemu_creation_flags(),
        )
    except BaseException:
        controls.close()
        raise
    com3 = None
    receiver_stop = threading.Event()
    receiver_thread = None
    receiver_errors: list[BaseException] = []
    try:
        with connect_rsp(args.gdb_port, 10.0) as gdb_socket:
            # Before timer setup, allow enough time for native delay loops.
            # Once initialization returns, the timeout becomes the emulated
            # hardware timer period and is handled as an interrupt, not an
            # execution failure.
            rsp = RspClient(gdb_socket, timeout=60.0)
            rsp.command("?")
            rsp.write_memory(0, (2 * 1024 * 1024).to_bytes(4, "big"))
            rsp.write_memory(MAIN_TX_STATUS, bytes([TX_READY]))
            rsp.write_memory(MAIN_RX_STATUS, b"\x00")
            if factory_runtime is not None:
                run_original_factory_reset(
                    rsp, factory_runtime, factory_entrypoint,
                )
            load_memory(rsp, 0x1000, runtime)
            for address, data in config_writes:
                load_memory(rsp, address, data)
            print(
                "DB_CONFIG_RAM_PROGRAMMED "
                f"sha256={config_digest} "
                f"clear={CONFIG_CLEAR_START:08X} "
                f"config={config_writes[1][0]:08X} "
                f"identity={config_writes[2][0]:08X}",
                flush=True,
            )
            # Enter through the original loader. Its native existing-image
            # checksum path sets VBR/hardware state and calls the header entry.
            # The boot ROM supplies D2=5F72D920; the unmodified loader checks
            # that exact cookie at 0x06B4 before calling its native validator.
            rsp.write_register_u32(REG_D2, LOADER_RUNTIME_COOKIE)
            rsp.write_register_u32(REG_D3, 0)
            rsp.write_register_u32(REG_A7, 0x001FFF80)
            rsp.write_register_u32(REG_PC, 0x040E)

            set_watchpoint(rsp, 3, MAIN_RX_STATUS, True)  # read
            set_watchpoint(rsp, 2, MAIN_DATA, True)       # write / TX
            set_watchpoint(rsp, 2, BOARD_LATCH, True)     # write / board probe
            set_watchpoint(rsp, 2, BOARD_SCC_CONTROL_A, True)
            set_watchpoint(rsp, 2, BOARD_SCC_CONTROL_B, True)
            set_watchpoint(rsp, 2, BOARD_SCC_DATA_B, True)
            set_watchpoint(rsp, 3, BOARD_SCC_DATA_B, True)
            # Capture both configuration writes and every serial pin change.
            set_watchpoint(
                rsp,
                2,
                RTC_CONTROL_REGISTER,
                True,
                length=RTC_CONTROL_WATCH_LENGTH,
            )
            set_watchpoint(rsp, 2, RTC_PORT_REGISTER, True)
            # Executes only on F_UHR, not on every normal clock read. Capture
            # its native reason once, then let the error handler run unchanged.
            set_watchpoint(rsp, 0, RTC_FAULT_ENTRY, True)
            set_watchpoint(rsp, 0, TOUCH_CALIBRATION_CX_RETURN_PC, True)
            set_watchpoint(rsp, 0, TOUCH_CALIBRATION_FINISH_PC, True)
            for address in BOARD_PORT_STROBES:
                set_watchpoint(rsp, 2, address, True)     # write / set-clear alias
            set_watchpoint(rsp, 0, RUNTIME_IO_INIT_RETURN_PC, True)
            rx_status_watch = True
            rx_data_watch = False
            rx_ready_seen = False
            rx_wait_reported = False
            tx_status_watch = False
            tx_ready_at = 0.0
            last_tx = None
            repeated_tx = 0
            first_frame = bytearray()
            first_frame_started_at = None
            first_frame_reported = False
            wire_frame = bytearray()
            rtc = Rtc4543(getattr(args, "rtc_date", RTC_DEFAULT_TIME))
            initvideo_clock_forwarder = InitvideoClockForwarder(rtc.now)
            touch_click_forwarder = TouchClickForwarder()
            touch_controller = VirtualTouchController(getattr(args, "touch_state", None))
            log_touch_controller_events(touch_controller)
            last_touch_point: tuple[int, int] | None = None
            last_wire_tx_at = 0.0
            initial_retry_frame = None
            initial_retry_stage = "startup"
            initial_frame_attempts = 0
            initial_frame_retry_at = 0.0
            guest_systeminfo_tail = bytearray()
            guest_systeminfo_seen = False
            last_uart_activity_at = time.monotonic()
            idle_diagnostic_at = 0.0
            active_isr_diagnostic_at = 0.0
            scc_a_progress_diagnostic_at = 0.0
            scc_a_pointer_watch_reports = 0
            scc_a_pointer_watch_enabled = False
            mp_type_watch_reports = 0
            idle_board_input_watch = False
            filtered_loader_tx = 0
            reported_tx_status_overlaps = set()
            board_port_strobes = 0
            board_scc_a_feedback_reported = False
            board_scc_a_irq_reported = False
            board_scc_feedback_reported = False
            timer_enabled = False
            cpu_run_budget = CpuRunBudget(foreground_timer_quantum(args.timer_interval, timer_config))
            accumulated_slice_reports = 0
            door_input_pending = False
            timer_injections = 0
            board_timer_ticks_remaining = 0
            board_interrupt_stack: list[tuple[int, int]] = []
            nested_timer_injections = 0
            timer_wait_diagnostic_at = 0.0
            hopper_fault_reported = False
            hopper_idle_reported = False
            return_button_released_reported = False
            uart_return_pc = None
            uart_service_burst = 0
            coin_validator = VirtualCoinValidator()
            coin_entry_watch = False
            mp_error_snapshot_reported = False
            rtc_reads_reported = 0
            touch_response_latch = None
            touch_status_poll_at = 0.0
            timer_clock_diagnostic_at = 0.0
            reported_unknown_touch_commands = set()
            pressed_buttons: set[str] = set()
            pending_button_edges: set[str] = set()
            released_buttons: set[str] = set(KEY_IDS)
            release_after_scans: dict[str, int] = {}
            button_run_budgets: dict[str, CpuRunBudget] = {}
            button_mapping_cache: dict[int, dict[str, int]] = {}
            button_trace_scans: dict[str, int] = {}
            touch_packets = TouchPacketStream()
            button_mappings_reported = False
            pending = deque()
            com3 = connect_com3(args.com3_host, args.com3_port, args.connect_timeout)
            receiver_thread = threading.Thread(
                target=receive_com3,
                args=(com3, pending, receiver_stop, receiver_errors),
                name="com3-receiver",
                daemon=True,
            )
            receiver_thread.start()
            print(
                f"REAL_DATABASE_COM3_CONNECTED {args.com3_host}:{args.com3_port}",
                flush=True,
            )

            while True:
                ensure_com3_receiver_alive(receiver_errors)
                for _ in range(32):
                    try:
                        input_types = {"door", "coin", "touch_calibration_reset"}
                        if touch_packets.can_accept_contact():
                            input_types.add("touch")
                        control_event = controls.events.pop_types(input_types)
                    except queue.Empty:
                        break
                    event_type = control_event["type"]
                    if event_type == "touch_calibration_reset":
                        try:
                            touch_controller.reset_calibration()
                        except ValueError as exc:
                            print(f"DB_TOUCH_CALIBRATION_REJECTED source=control_panel reason={exc}", flush=True)
                        log_touch_controller_events(touch_controller)
                    elif event_type == "door":
                        door_closed = not control_event["open"]
                        door_input_pending = True
                        print(
                            "DB_CABINET_DOOR_QUEUED "
                            f"door={'closed' if door_closed else 'open'}",
                            flush=True,
                        )
                    elif event_type == "touch":
                        x, y, down = (
                            control_event["x"], control_event["y"],
                            control_event["down"],
                        )
                        touch_packets.request(x, y, down)
                        print(
                            f"DB_TOUCH_REQUEST x={x} y={y} down={down}",
                            flush=True,
                        )
                    elif event_type == "coin":
                        try:
                            sequence = coin_validator.queue_coin(control_event["cents"], time.monotonic())
                            print(f"DB_VIRTUAL_MP_COIN_QUEUED id={sequence} cents=100", flush=True)
                        except ValueError as exc:
                            print(f"DB_VIRTUAL_MP_COIN_REJECTED cents=100 reason={exc}", flush=True)
                for coin_event in coin_validator.expire_coins(time.monotonic()):
                    print(coin_event, flush=True)
                if not coin_entry_watch and coin_validator.stage_entry_sensor(rsp):
                    set_watchpoint(rsp, 0, COIN_ENTRY_READ_PC, True)
                    coin_entry_watch = True
                    print(f"DB_VIRTUAL_MP_ENTRY_SENSOR_START id={coin_validator._entry_sensor_sequence} samples=4", flush=True)
                touch_poll_now = time.monotonic()
                if timer_enabled and (touch_poll_now >= touch_status_poll_at or touch_packets.packets):
                    touch_status_poll_at = touch_poll_now + TOUCH_STATUS_POLL_SECONDS
                    touch_state = rsp.read_memory(
                        TOUCH_TRANSACTION_STATE, 1
                    )[0]
                    if touch_state != TOUCH_WAITING_FOR_REPLY:
                        touch_response_latch = None
                    elif not touch_controller.calibrating:
                        request_bytes = rsp.read_memory(
                            TOUCH_TRANSACTION_REQUEST,
                            TOUCH_TRANSACTION_REQUEST_SIZE,
                        )
                        request = request_bytes.split(b"\x00", 1)[0]
                        if request and request != touch_response_latch:
                            handled, response = service_touch_command(rsp, touch_controller, request)
                            if handled and response is None:
                                if request not in reported_unknown_touch_commands:
                                    print(
                                        "DB_TOUCH_UNKNOWN_COMMAND "
                                        f"data={request.hex(' ').upper()}",
                                        flush=True,
                                    )
                                    reported_unknown_touch_commands.add(request)
                                touch_response_latch = request
                            elif handled:
                                print(
                                    "DB_TOUCH_RESPONSE "
                                    f"command={request.hex(' ').upper()} "
                                    f"response={response.hex(' ').upper()}",
                                    flush=True,
                                )
                                touch_response_latch = request
                    if ((touch_state != TOUCH_WAITING_FOR_REPLY or touch_controller.calibrating)
                            and touch_packets.packets):
                        delivered = deliver_touch_packet(rsp, touch_controller, touch_packets)
                        if delivered is not None:
                            x, y, down, packet, point, calibration_input = delivered
                            if down and point is not None:
                                last_touch_point = point
                            prefix = "DB_TOUCH_CALIBRATION_INPUT" if calibration_input else "DB_TOUCH_INPUT"
                            print(
                                f"{prefix} "
                                f"x={x} y={y} down={down} "
                                f"tablet={packet.hex(' ').upper() if packet else 'none'}",
                                flush=True,
                            )
                    log_touch_controller_events(touch_controller)
                retry_now = time.monotonic()
                if wire_frame and (
                    pending or retry_now - last_wire_tx_at >= 1.0
                ):
                    if (
                        not mp_error_snapshot_reported
                        and b"F\x00A\x00L\x00S\x00C\x00H\x00E\x00R\x00" in wire_frame
                    ):
                        print(
                            "DB_MP_ERROR_STATE "
                            f"state={rsp.read_memory(0x001F9988, 1)[0]:02X} "
                            f"missing={rsp.read_memory(0x001F1665, 1)[0]:02X} "
                            f"manufacturer={rsp.read_memory(0x001E230A, 1)[0]:02X} "
                            f"comm_error={rsp.read_memory(0x001E2D29, 1)[0]:02X} "
                            f"profile_selector={rsp.read_memory(0x001E2A96, 1)[0]:02X} "
                            f"profile_key={rsp.read_memory(0x001E2AA1, 16).hex(' ').upper()} "
                            f"profile_index={rsp.read_memory(0x001F9C19, 1)[0]:02X} "
                            f"identity={rsp.read_memory(0x001F16AC, 33).hex(' ').upper()}",
                            flush=True,
                        )
                        mp_error_snapshot_reported = True
                    print(
                        f"DB_FRAME data={wire_frame.hex(' ').upper()}",
                        flush=True,
                    )
                    if (
                        args.trace_diagnostics
                        and not scc_a_pointer_watch_enabled
                        and wire_frame == bytes.fromhex("01 02 3E 00 81 2D 01 04")
                    ):
                        set_watchpoint(
                            rsp, 2, BOARD_SCC_A_TX_POINTER, True, length=4
                        )
                        scc_a_pointer_watch_enabled = True
                        print("DB_SCC_A_TX_POINTER_WATCH_ENABLED after=VIEW", flush=True)
                    wire_frame.clear()
                retry_confirmed = initvideo_retry_confirmed(
                    initial_retry_stage,
                    response_pending=bool(pending),
                    systeminfo_seen=guest_systeminfo_seen,
                )
                if initial_retry_frame is not None and retry_confirmed:
                    print(
                        "DB_INITVIDEO_RETRY_DISARMED "
                        f"reason={'guest-response' if initial_retry_stage == 'startup' else 'guest-systeminfo'} "
                        f"attempts={initial_frame_attempts}",
                        flush=True,
                    )
                    initial_retry_frame = None
                elif initial_retry_frame is not None and initial_frame_retry_due(
                    response_pending=bool(pending),
                    attempts=initial_frame_attempts,
                    now=retry_now,
                    retry_at=initial_frame_retry_at,
                ):
                    # Reuse the original profile, not its cached calendar. Every
                    # attempt must follow the RTC initialized from settings.
                    initial_retry_frame = complete_initvideo_clock(
                        initial_retry_frame, rtc.now()
                    )
                    wire_ms = send_serial_frame(
                        com3, initial_retry_frame
                    ) * 1000.0
                    initial_frame_attempts += 1
                    initial_frame_retry_at = (
                        time.monotonic() + INITIAL_FRAME_RETRY_SECONDS
                    )
                    print(
                        "DB_INITVIDEO_WIRE_RETRY "
                        f"attempt={initial_frame_attempts} "
                        f"time={initvideo_time_text(initial_retry_frame)} "
                        f"wire_ms={wire_ms:.3f}",
                        flush=True,
                    )
                    if initial_frame_attempts >= INITIAL_FRAME_MAX_ATTEMPTS:
                        initial_retry_frame = None
                        print(
                            "DB_INITVIDEO_RETRY_LIMIT_REACHED "
                            f"attempts={initial_frame_attempts}",
                            flush=True,
                        )
                if should_publish_pending_uart_byte(
                    len(pending),
                    rx_status_watch=rx_status_watch,
                    rx_data_watch=rx_data_watch,
                    rx_ready_seen=rx_ready_seen,
                ):
                    # With no byte pending, a real UART returns RX-ready=0
                    # without stopping the CPU.  Once the receiver thread has
                    # a byte, publish data first and the ready flag last; trap
                    # only the eventual data read so board timers keep running.
                    rsp.write_memory(MAIN_DATA, bytes([pending[0]]))
                    rsp.write_memory(MAIN_RX_STATUS, bytes([RX_READY]))
                    set_watchpoint(rsp, 3, MAIN_DATA, True)
                    rx_data_watch = True
                    rx_ready_seen = True
                    rx_wait_reported = False
                timer_stop = False
                foreground_run = timer_enabled and not board_interrupt_stack and uart_return_pc is None
                if timer_enabled and board_interrupt_stack:
                    run_timeout = foreground_timer_quantum(args.timer_interval, timer_config)
                else:
                    run_timeout = cpu_run_budget.remaining if foreground_run else (args.timer_interval if timer_enabled else 60.0)
                if foreground_run and cpu_run_budget.due:
                    # The previous watchpoint was handled with the CPU stopped.
                    # Service the elapsed slice now, without losing that event
                    # or waiting for a fresh uninterrupted slice to complete.
                    reply = "S02"
                    timer_stop = True
                else:
                    rsp.set_timeout(run_timeout)
                    run_started_at = time.monotonic()
                    try:
                        reply = rsp.command("c")
                    except TimeoutError:
                        reply = interrupt_after_run_slice(rsp)
                        timer_stop = is_run_slice_interrupt(reply)
                    finally:
                        if foreground_run:
                            foreground_seconds = min(run_timeout, time.monotonic() - run_started_at)
                            cpu_run_budget.add_run(foreground_seconds)
                            for button_budget in button_run_budgets.values():
                                button_budget.add_run(foreground_seconds)
                        # Stopped work does not consume foreground CPU time.
                        # The independent wall timer accounts for it separately.
                        rsp.set_timeout(RSP_CONTROL_REPLY_TIMEOUT)

                if timer_stop:
                    if foreground_run:
                        if cpu_run_budget.segments > 1 and accumulated_slice_reports < 5:
                            print(
                                "DB_TIMER_SLICE_ACCUMULATED "
                                f"segments={cpu_run_budget.segments} "
                                f"run_ms={cpu_run_budget.elapsed * 1000:.3f}", flush=True,
                            )
                            accumulated_slice_reports += 1
                        cpu_run_budget.reset()
                    if not timer_enabled:
                        raise RuntimeError(
                            "runtime did not complete board I/O initialization: "
                            + read_register_snapshot(rsp)
                        )
                    active_snapshot = rsp.read_register_snapshot()
                    active_registers = active_snapshot.core_u32
                    active_pc = active_registers[REG_PC]
                    active_sr = active_registers[REG_SR]
                    active_a0 = active_registers[REG_A0]
                    expired_slot = expired_timer_queue_slot(
                        active_pc, active_sr, active_a0
                    )
                    if expired_slot is not None:
                        # Let the original queue decrement under native IRQs.
                        # Short scheduler quanta must not expire firmware
                        # waits early or bypass timer delivery altogether.
                        diagnostic_now = time.monotonic()
                        if diagnostic_now >= timer_wait_diagnostic_at:
                            queued_delay = int.from_bytes(
                                rsp.read_memory(expired_slot, 4), "big"
                            )
                            print(
                                "DB_TIMER_WAIT_NEEDS_BOARD_TICK "
                                f"pc={active_pc:08X} sr={active_sr:04X} "
                                f"slot={expired_slot:08X} value={queued_delay:08X} "
                                f"scc_state={rsp.read_memory(BOARD_SCC_STATE, 1)[0]:02X} "
                                f"board_depth={len(board_interrupt_stack)} "
                                f"input_queued={controls.events.qsize()} "
                                f"touch_queued={len(touch_packets.packets)}", flush=True,
                            )
                            timer_wait_diagnostic_at = diagnostic_now + IDLE_DIAGNOSTIC_SECONDS
                    if uart_return_pc is not None:
                        continue
                    if board_interrupt_stack:
                        scc_a_command = rsp.read_memory(
                            BOARD_SCC_A_COMMAND, 1
                        )[0]
                        scc_a_status = rsp.read_memory(
                            BOARD_SCC_CONTROL_A, 1
                        )[0]
                        diagnostic_now = time.monotonic()
                        if diagnostic_now >= active_isr_diagnostic_at:
                            board_status = rsp.read_memory(
                                BOARD_TIMER_STATUS, 1
                            )[0]
                            print(
                                "DB_ACTIVE_BOARD_ISR_SNAPSHOT "
                                f"pc={active_pc:08X} sr={active_sr:04X} "
                                f"depth={len(board_interrupt_stack)} "
                                f"return={board_interrupt_stack[-1][1]:08X} "
                                f"scc_a_command={scc_a_command:02X} "
                                f"scc_a_status={scc_a_status:02X} "
                                f"scc_b_status={rsp.read_memory(BOARD_SCC_CONTROL_B, 1)[0]:02X} "
                                f"scc_b_pending={coin_validator.pending_count()} "
                                f"board_status={board_status:02X}",
                                flush=True,
                            )
                            if not hopper_fault_reported:
                                fault = rsp.read_memory(
                                    HOPPER_FAULT_ADDRESS, 1
                                )[0]
                                if fault:
                                    fields = read_hopper_diagnostics(rsp)
                                    print(
                                        "DB_HOPPER_FAULT_SNAPSHOT "
                                        f"fault={fault:02X} "
                                        + " ".join(
                                            f"{name}={value:02X}"
                                            for name, value in fields.items()
                                        ),
                                        flush=True,
                                    )
                                    hopper_fault_reported = True
                            active_isr_diagnostic_at = (
                                diagnostic_now + IDLE_DIAGNOSTIC_SECONDS
                            )
                        if can_nest_scc_a_tx(
                            active_pc, active_sr, len(board_interrupt_stack),
                            board_interrupt_stack[-1][1],
                            scc_a_command, scc_a_status,
                        ):
                            interrupted_pc, _ = inject_interrupt(
                                rsp, BOARD_SCC_A_VECTOR, BOARD_SCC_A_HANDLER,
                                snapshot=active_snapshot,
                            )
                            board_interrupt_stack.append(
                                (interrupted_pc, BOARD_SCC_A_RTE_PC)
                            )
                            print(
                                "DB_NESTED_SCC_A_TX_SERVICE "
                                f"command={scc_a_command:02X} "
                                f"pc={active_pc:08X} sr={active_sr:04X}",
                                flush=True,
                            )
                            continue
                        if can_nest_uart_reply(
                            active_pc, active_sr, len(board_interrupt_stack),
                            board_interrupt_stack[-1][1], bool(pending),
                        ):
                            interrupted_pc, _ = inject_interrupt(
                                rsp, TIMER_VECTOR, TIMER_HANDLER, snapshot=active_snapshot,
                            )
                            uart_return_pc = interrupted_pc
                            print(
                                "DB_NESTED_UART_REPLY "
                                f"pc={active_pc:08X} pending={len(pending)}",
                                flush=True,
                            )
                            continue
                        if can_nest_board_timer(
                            active_pc, active_sr, len(board_interrupt_stack)
                        ):
                            # A nested tick consumes the existing reservation
                            # first; never deliver it again after the ISR exits.
                            if board_timer_ticks_remaining:
                                board_timer_ticks_remaining -= 1
                            elif not timer_budget.due_ticks(limit=1):
                                continue
                            interrupted_pc, _ = inject_board_tick(
                                rsp, timer_target, snapshot=active_snapshot,
                            )
                            board_interrupt_stack.append(
                                (interrupted_pc, timer_target[2])
                            )
                            timer_injections += 1
                            nested_timer_injections += 1
                            if nested_timer_injections == 1:
                                queue_values = [
                                    int.from_bytes(
                                        rsp.read_memory(
                                            TIMER_QUEUE_BASE + offset, 4
                                        ),
                                        "big",
                                    )
                                    for offset in range(
                                        0, TIMER_QUEUE_SLOTS * 4, 4
                                    )
                                ]
                                print(
                                    "DB_NESTED_BOARD_TIMER "
                                    f"pc={active_pc:08X} sr={active_sr:04X} "
                                    f"queue={','.join(f'{value:08X}' for value in queue_values)}",
                                    flush=True,
                                )
                        # The short nested slices share one selected run budget.
                        continue
                    diagnostic_now = time.monotonic()
                    if idle_protocol_diagnostic_due(
                        first_frame_reported=first_frame_reported,
                        retry_active=initial_retry_frame is not None,
                        response_pending=bool(pending),
                        now=diagnostic_now,
                        last_uart_activity_at=last_uart_activity_at,
                        next_diagnostic_at=idle_diagnostic_at,
                    ):
                        idle_uart_state = int.from_bytes(
                            rsp.read_memory(UART_STATE_ADDRESS, 2), "big"
                        )
                        idle_board_input = rsp.read_memory(BOARD_PORT_INPUT, 1)[0]
                        idle_board_status = rsp.read_memory(BOARD_TIMER_STATUS, 1)[0]
                        idle_pc = rsp.read_register_u32(REG_PC)
                        caller_detail = ""
                        if idle_pc in TIMER_QUEUE_WAIT_PCS:
                            stack = rsp.read_register_u32(REG_A7)
                            caller = int.from_bytes(
                                rsp.read_memory(stack, 4), "big"
                            )
                            caller_detail = f" timer_caller={caller:08X}"
                        mp_missing = rsp.read_memory(0x001F1665, 1)[0]
                        mp_phase = int.from_bytes(
                            rsp.read_memory(0x001E2300, 2), "big"
                        )
                        mp_active = rsp.read_memory(0x001F8221, 1)[0]
                        mp_required_type = rsp.read_memory(MP_REQUIRED_TYPE_STATE, 1)[0]
                        mp_detected_type = rsp.read_memory(MP_DETECTED_TYPE_STATE, 1)[0]
                        scc_a_command = rsp.read_memory(BOARD_SCC_A_COMMAND, 1)[0]
                        scc_a_status = rsp.read_memory(BOARD_SCC_CONTROL_A, 1)[0]
                        scc_a_mode = rsp.read_memory(BOARD_SCC_A_COMMAND + 1, 1)[0]
                        scc_a_tx_pointer = int.from_bytes(
                            rsp.read_memory(0x001E2120, 4), "big"
                        )
                        scc_a_active_pointer = int.from_bytes(
                            rsp.read_memory(0x001E211C, 4), "big"
                        )
                        scc_a_busy = rsp.read_memory(0x001E1D0D, 1)[0]
                        scc_a_isr_toggle = rsp.read_memory(0x001E29D9, 1)[0]
                        scc_a_guard = rsp.read_memory(0x001E2334, 1)[0]
                        cabinet_profile = rsp.read_memory(MP_STATE_ADDRESS, 1)[0]
                        coin_ready = rsp.read_memory(0x001F777D, 1)[0]
                        service_active = rsp.read_memory(0x001E2A02, 1)[0]
                        coin_io_window = int.from_bytes(rsp.read_memory(0x001E26CE, 2), "big")
                        pc_reply_wait = int.from_bytes(rsp.read_memory(0x001EC8B0, 4), "big")
                        scc_a_payload_detail = ""
                        if scc_a_command == 0x64 and idle_pc in (
                            SCC_A_COMMAND_WAIT_PC, SCC_A_COMMAND_WAIT_PC + 2
                        ):
                            payload = rsp.read_memory(0x001E2924, 16)
                            scc_a_payload_detail = (
                                f" scc_a_payload={payload.hex(' ').upper()}"
                            )
                        if idle_pc in (SCC_A_COMMAND_WAIT_PC, SCC_A_COMMAND_WAIT_PC + 2):
                            stack = rsp.read_register_u32(REG_A7)
                            caller = int.from_bytes(rsp.read_memory(stack, 4), "big")
                            caller_detail += f" scc_a_caller={caller:08X}"
                        print(
                            "DB_IDLE_PROTOCOL_SNAPSHOT "
                            + read_register_snapshot(rsp)
                            + " "
                            + f"uart_state={idle_uart_state:04X} "
                            + f"board_input={idle_board_input:02X} "
                            + f"board_status={idle_board_status:02X} "
                            + f"mp_missing={mp_missing:02X} "
                            + f"mp_phase={mp_phase:04X} "
                            + f"mp_active={mp_active:02X}"
                            + f" mp_required_type={mp_required_type:02X}"
                            + f" mp_detected_type={mp_detected_type:02X}"
                            + f" scc_a_command={scc_a_command:02X}"
                            + f" scc_a_status={scc_a_status:02X}"
                            + f" scc_a_mode={scc_a_mode:02X}"
                            + f" scc_a_tx_pointer={scc_a_tx_pointer:08X}"
                            + f" scc_a_active_pointer={scc_a_active_pointer:08X}"
                            + f" scc_a_busy={scc_a_busy:02X}"
                            + f" scc_a_isr_toggle={scc_a_isr_toggle:02X}"
                            + f" scc_a_guard={scc_a_guard:02X}"
                            + f" cabinet_profile={cabinet_profile:02X}"
                            + f" coin_ready={coin_ready:02X}"
                            + f" service_active={service_active:02X}"
                            + f" coin_io_window={coin_io_window:04X}"
                            + f" pc_reply_wait={pc_reply_wait:08X}"
                            + scc_a_payload_detail
                            + caller_detail,
                            flush=True,
                        )
                        if args.trace_diagnostics and not idle_board_input_watch:
                            set_watchpoint(rsp, 3, BOARD_PORT_INPUT, True)
                            idle_board_input_watch = True
                        idle_diagnostic_at = (
                            diagnostic_now + IDLE_DIAGNOSTIC_SECONDS
                        )
                    if not interrupts_unmasked(active_sr):
                        # Keep hardware debt pending while original critical
                        # code runs; injecting through SR2700 corrupts state.
                        continue
                    timer_ticks_per_cycle = timer_budget.due_ticks()
                    if not timer_ticks_per_cycle:
                        continue
                    if time.monotonic() >= timer_clock_diagnostic_at:
                        print(
                            "DB_TIMER_CLOCK "
                            f"source=monotonic-wall batch={timer_ticks_per_cycle} "
                            f"pending={timer_budget.pending_ticks} coalesced={timer_budget.coalesced_ticks} "
                            f"foreground_ms={cpu_run_budget.seconds * 1000:.3f}", flush=True,
                        )
                        timer_clock_diagnostic_at = time.monotonic() + IDLE_DIAGNOSTIC_SECONDS
                    board_timer_ticks_remaining = timer_ticks_per_cycle - 1
                    board_vector, board_handler, board_rte = timer_target
                    scc_a_active = board_vector == BOARD_SCC_A_VECTOR
                    interrupted_pc, interrupted_sr = inject_board_tick(
                        rsp, timer_target, snapshot=active_snapshot,
                    )
                    board_interrupt_stack.append((interrupted_pc, board_rte))
                    timer_injections += 1
                    if scc_a_active and not board_scc_a_irq_reported:
                        scc_a_command = rsp.read_memory(BOARD_SCC_A_COMMAND, 1)[0]
                        print(
                            "DB_DUART_ALTERNATE_TIMER "
                            f"command={scc_a_command:02X} "
                            f"vector={BOARD_SCC_A_VECTOR} "
                            f"pc={interrupted_pc:08X}",
                            flush=True,
                        )
                        board_scc_a_irq_reported = True
                    if timer_injections == 1:
                        tx_read = int.from_bytes(rsp.read_memory(0x1EBBB4, 4), "big")
                        tx_write = int.from_bytes(rsp.read_memory(0x1EBBB8, 4), "big")
                        print(
                            "DB_FIRST_BOARD_TIMER_SAMPLE "
                            f"pc={interrupted_pc:08X} sr={interrupted_sr:04X} "
                            f"tx_read={tx_read:08X} tx_write={tx_write:08X}",
                            flush=True,
                        )
                    continue

                kind, address = parse_stop_address(reply)
                if kind == "watch" and address in (
                    DUART_TIMER_ACR, DUART_TIMER_CTUR, DUART_TIMER_CTLR, DUART_TIMER_IVR,
                ):
                    changed_config = read_duart_timer_config(rsp, args.duart_x1_hz)
                    if changed_config != timer_config:
                        timer_config = changed_config
                        timer_budget = DuartWallTimer(timer_config)
                        timer_budget.start()
                        cpu_run_budget = CpuRunBudget(foreground_timer_quantum(args.timer_interval, timer_config))
                        timer_target = board_service_target(timer_config.vector)
                        board_timer_ticks_remaining = 0
                        print(
                            "DB_DUART_TIMER_RECONFIGURED "
                            f"acr={timer_config.acr:02X} preset={timer_config.preset:04X} "
                            f"vector={timer_config.vector} x1_hz={timer_config.x1_hz} "
                            f"period_ms={timer_config.period_seconds * 1000:.6f}",
                            flush=True,
                        )
                    continue
                if kind == "watch" and address in (
                    MP_REQUIRED_TYPE_STATE, MP_DETECTED_TYPE_STATE
                ):
                    if mp_type_watch_reports < 40:
                        print(
                            "DB_MP_TYPE_STATE_WRITE "
                            f"pc={rsp.read_register_u32(REG_PC):08X} "
                            f"address={address:08X} "
                            f"value={rsp.read_memory(address, 1)[0]:02X} "
                            f"required={rsp.read_memory(MP_REQUIRED_TYPE_STATE, 1)[0]:02X} "
                            f"detected={rsp.read_memory(MP_DETECTED_TYPE_STATE, 1)[0]:02X}",
                            flush=True,
                        )
                        mp_type_watch_reports += 1
                    continue
                if kind == "watch" and address == BOARD_SCC_A_TX_POINTER:
                    command = rsp.read_memory(BOARD_SCC_A_COMMAND, 1)[0]
                    if command == 0x64 and scc_a_pointer_watch_reports < 20:
                        print(
                            "DB_SCC_A_TX_POINTER_WRITE "
                            f"pc={rsp.read_register_u32(REG_PC):08X} "
                            f"value={int.from_bytes(rsp.read_memory(BOARD_SCC_A_TX_POINTER, 4), 'big'):08X} "
                            f"command={command:02X}",
                            flush=True,
                        )
                        scc_a_pointer_watch_reports += 1
                    continue
                if kind == "watch" and address == AUX_TRANSACTION_ACTIVE:
                    if rsp.read_memory(AUX_TRANSACTION_ACTIVE, 1)[0] == 1:
                        command = rsp.read_memory(AUX_TRANSACTION_COMMAND, 1)[0]
                        length = rsp.read_memory(AUX_TRANSACTION_LENGTH, 1)[0]
                        payload = rsp.read_memory(
                            AUX_TRANSACTION_DATA, min(length, 16)
                        )
                        print(
                            "DB_AUX_TRANSACTION_START "
                            f"pc={rsp.read_register_u32(REG_PC):08X} "
                            f"command={command:02X} length={length:02X} "
                            f"data={payload.hex(' ').upper()}",
                            flush=True,
                        )
                    continue
                if kind == "watch" and address == AUX_PROFILE_SELECTOR:
                    print(
                        "DB_AUX_PROFILE_SELECTOR "
                        f"pc={rsp.read_register_u32(REG_PC):08X} "
                        f"value={rsp.read_memory(AUX_PROFILE_SELECTOR, 1)[0]:02X} "
                        f"status={rsp.read_memory(0x001E2A95, 1)[0]:02X} "
                        f"command={rsp.read_memory(AUX_TRANSACTION_COMMAND, 1)[0]:02X} "
                        f"data={rsp.read_memory(AUX_TRANSACTION_DATA, 16).hex(' ').upper()} "
                        f"key={rsp.read_memory(0x001E2AA1, 4).hex(' ').upper()}",
                        flush=True,
                    )
                    continue
                if kind == "watch" and address == MP_STATE_ADDRESS:
                    print(
                        "DB_MP_STATE_WRITE "
                        f"pc={rsp.read_register_u32(REG_PC):08X} "
                        f"state={rsp.read_memory(MP_STATE_ADDRESS, 1)[0]:02X} "
                        f"profile_index={rsp.read_memory(0x001F9C19, 1)[0]:02X} "
                        f"identity={rsp.read_memory(0x001F16AC, 33).hex(' ').upper()}",
                        flush=True,
                    )
                    continue
                if kind is None:
                    pc = rsp.read_register_u32(REG_PC)
                    if pc == TOUCH_CALIBRATION_CX_RETURN_PC:
                        # CX is sent synchronously, not through the normal
                        # transaction object. The transmitter has drained RX;
                        # deliver the initial ACK before its state-6 receiver.
                        handled, response = service_touch_command(rsp, touch_controller, b"\x01CX\r")
                        if not handled:
                            raise RuntimeError("calibration start RX ring is not empty")
                        touch_packets.packets.clear()
                        touch_packets.active_point = None
                        last_touch_point = None
                        log_touch_controller_events(touch_controller)
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == TOUCH_CALIBRATION_FINISH_PC:
                        if touch_controller.calibration_session:
                            touch_controller.finish()
                            touch_packets.packets.clear()
                            touch_packets.active_point = None
                            last_touch_point = None
                            log_touch_controller_events(touch_controller)
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == RTC_FAULT_ENTRY:
                        print("DB_RTC_FAULT_SNAPSHOT " + read_rtc_fault_snapshot(rsp, rtc.now()), flush=True)
                        set_watchpoint(rsp, 0, RTC_FAULT_ENTRY, False)
                        continue
                    if coin_entry_watch and pc == COIN_ENTRY_READ_PC:
                        # This instruction reads the AY input after selecting
                        # port A. Restore the selector after the native read;
                        # unrelated AY accesses keep their previous behavior.
                        selected = rsp.read_memory(COIN_ENTRY_PORT, 1)[0]
                        sample = coin_validator.sample_entry_sensor(selected)
                        rsp.write_memory(COIN_ENTRY_PORT, bytes([sample]))
                        step_past_breakpoint(rsp, pc)
                        rsp.write_memory(COIN_ENTRY_PORT, bytes([selected & ~COIN_ENTRY_MASK]))
                        if not coin_validator._entry_sensor_samples:
                            set_watchpoint(rsp, 0, COIN_ENTRY_READ_PC, False)
                            coin_entry_watch = False
                            print(f"DB_VIRTUAL_MP_ENTRY_SENSOR_RELEASED id={coin_validator._entry_sensor_sequence}", flush=True)
                        continue
                    if pc in (AUX_RESPONSE_CHECK_PC,
                              AUX_PROFILE_RESPONSE_CHECK_PC,
                              AUX_VALUE_RESPONSE_CHECK_PC):
                        command = rsp.read_memory(AUX_TRANSACTION_COMMAND, 1)[0]
                        if (pc, command) in (
                            (AUX_RESPONSE_CHECK_PC, 0x31),
                            (AUX_PROFILE_RESPONSE_CHECK_PC, 0x34),
                            (AUX_VALUE_RESPONSE_CHECK_PC, 0x32),
                        ):
                            challenge = rsp.read_register_u32(
                                REG_D6 if command == 0x32 else REG_D7
                            )
                            index = rsp.read_register_u32(REG_D7) & 0xFF
                            encoded = (
                                virtual_aux_identification_reply(challenge, admission_eeprom)
                                if command == 0x31
                                else virtual_aux_profile_reply(challenge, admission_eeprom)
                                if command == 0x34
                                else virtual_aux_value_reply(challenge, index, admission_eeprom)
                            )
                            if encoded is not None:
                                rsp.write_memory(AUX_TRANSACTION_DATA, encoded)
                                status = rsp.read_memory(AUX_PROFILE_STATUS, 1)[0]
                                rsp.write_memory(
                                    AUX_PROFILE_STATUS,
                                    bytes([(status & 0xFC) | 3]),
                                )
                                print(
                                    "DB_VIRTUAL_AUX_REPLY "
                                    f"command={command:02X} "
                                    f"index={index:02X} "
                                    f"challenge={challenge:08X} "
                                    f"wire={encoded.hex(' ').upper()} "
                                    f"decoded={decode_aux_packet(encoded).hex(' ').upper()}",
                                    flush=True,
                                )
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == VirtualCoinValidator.PAIRING_READ_PC:
                        reconciliation = coin_validator.reconcile_pairing_receive_buffer(rsp)
                        if reconciliation is not None:
                            observed, published = reconciliation
                            print(
                                "DB_VIRTUAL_MP_RECEIVE_BUFFER "
                                f"observed={observed.hex(' ').upper()} "
                                f"published={published.hex(' ').upper()} "
                                f"repaired={observed != published}",
                                flush=True,
                            )
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == VirtualCoinValidator.PAIRING_COMPARE_PC:
                        frame_gap_fallback = (
                            coin_validator.pairing_frame_gap
                            and not coin_validator.challenge_pending
                        )
                        match = coin_validator.match_challenge(rsp)
                        print(
                            "DB_VIRTUAL_MP_CHALLENGE_COMPARE "
                            f"frame_gap_fallback={frame_gap_fallback} "
                            f"gap={coin_validator.last_pairing_gap} "
                            f"wire_match={bool(match) and match[0] == match[1]} "
                            f"register_override={bool(match) and match[2]} "
                            f"received_before_override={match[0] if match else -1:04X} "
                            f"forced_expected={match[1] if match else -1:04X} "
                            f"remaining={coin_validator.pending_count()}",
                            flush=True,
                        )
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == VirtualCoinValidator.PAIRING_FAILURE_PC:
                        print(
                            "DB_VIRTUAL_MP_CHALLENGE_FAILED "
                            f"pending={coin_validator.challenge_pending} "
                            f"remaining={coin_validator.pending_count()} "
                            f"d6={rsp.read_register_u32(REG_D6):08X} "
                            f"expected={rsp.read_memory(VirtualCoinValidator.PAIRING_EXPECTED_WORD, 2).hex().upper()} "
                            f"reply={rsp.read_memory(0x001E223C, 4).hex(' ').upper()}",
                            flush=True,
                        )
                        step_past_breakpoint(rsp, pc)
                        continue
                    if pc == BOARD_SCAN_COMPLETE_PC:
                        if not board_interrupt_stack:
                            raise RuntimeError(
                                "board scan completed outside board interrupt"
                            )
                        # Consume physical button edges only at the completed
                        # board scan. A second click cannot overwrite the first
                        # pulse or bypass its published released state.
                        for control_event in consume_cabinet_button_events(
                            controls.events, pressed_buttons, pending_button_edges,
                            released_buttons, release_after_scans,
                        ):
                            name = control_event["name"]
                            if control_event["down"]:
                                button_run_budgets[name] = CpuRunBudget(args.timer_interval)
                            print(
                                "DB_CABINET_BUTTON "
                                f"name={name} key_id={KEY_IDS[name]} "
                                f"down={control_event['down']} source=board-scan "
                                f"queued={controls.events.qsize()}", flush=True,
                            )
                        for name, scans in tuple(button_trace_scans.items()):
                            scans += 1
                            if scans in (1, 5, 20, 100, 200):
                                profile = rsp.read_memory(MP_STATE_ADDRESS, 1)[0]
                                mapping = button_mapping_cache.get(profile, {}).get(name)
                                location = key_location(mapping) if mapping is not None else None
                                if location is not None:
                                    current_addr, event_addr, mask = location
                                    print(
                                        "DB_CABINET_BUTTON_TRACE "
                                        f"name={name} scans={scans} "
                                        f"current={rsp.read_memory(current_addr, 1)[0]:02X} "
                                        f"event={rsp.read_memory(event_addr, 1)[0]:02X} "
                                        f"mask={mask:02X}",
                                        flush=True,
                                    )
                            if scans >= 200:
                                del button_trace_scans[name]
                            else:
                                button_trace_scans[name] = scans
                        new_button_edges = pending_button_edges.copy()
                        mappings = publish_cabinet_buttons(
                            rsp, pressed_buttons, pending_button_edges,
                            released_buttons, button_mapping_cache,
                        )
                        for name in sorted(new_button_edges):
                            location = key_location(mappings[name])
                            if location is not None:
                                button_trace_scans[name] = 0
                                current_addr, event_addr, mask = location
                                print(
                                    "DB_CABINET_BUTTON_APPLIED "
                                    f"name={name} "
                                    f"current={rsp.read_memory(current_addr, 1)[0]:02X} "
                                    f"event={rsp.read_memory(event_addr, 1)[0]:02X} "
                                    f"mask={mask:02X}",
                                    flush=True,
                                )
                        completed_button_pulses = advance_button_pulses(
                            pressed_buttons, pending_button_edges,
                            released_buttons, release_after_scans,
                            foreground_pending={
                                name for name, budget in button_run_budgets.items()
                                if not budget.due
                            },
                        )
                        if completed_button_pulses:
                            for name in completed_button_pulses:
                                button_run_budgets.pop(name, None)
                            print(
                                "DB_CABINET_BUTTON_PULSE_COMPLETE "
                                + ",".join(sorted(completed_button_pulses)),
                                flush=True,
                            )
                        if not button_mappings_reported:
                            print(
                                "DB_CABINET_BUTTON_MAP "
                                + " ".join(
                                    f"{name}={mapping:04X}"
                                    for name, mapping in mappings.items()
                                ),
                                flush=True,
                            )
                            button_mappings_reported = True
                        if not return_button_released_reported:
                            return_current = rsp.read_memory(
                                RETURN_BUTTON_CURRENT, 1
                            )[0]
                            return_event = rsp.read_memory(
                                RETURN_BUTTON_EVENT, 1
                            )[0]
                            print(
                                "DB_RETURN_BUTTON_RELEASED source=board-scan "
                                f"key_id={RETURN_BUTTON_KEY_ID} "
                                f"mapping={RETURN_BUTTON_MAPPING:04X} "
                                f"current={return_current:02X} "
                                f"event={return_event:02X} "
                                f"mask={RETURN_BUTTON_MASK:02X}",
                                flush=True,
                            )
                            return_button_released_reported = True
                        idle = publish_idle_hopper_sensors(rsp)
                        if not hopper_idle_reported:
                            print(
                                "DB_HOPPER_IDLE_SENSORS source=board-scan "
                                f"values={idle.hex(' ').upper()}",
                                flush=True,
                            )
                            hopper_idle_reported = True
                        step_past_breakpoint(rsp, pc)
                        continue
                    if (
                        board_interrupt_stack
                        and pc == board_interrupt_stack[-1][1]
                    ):
                        interrupted_pc, rte_pc = board_interrupt_stack.pop()
                        if (
                            rte_pc == BOARD_SCC_A_RTE_PC
                            and interrupted_pc in (
                                SCC_A_COMMAND_WAIT_PC,
                                SCC_A_COMMAND_WAIT_PC + 2,
                            )
                            and time.monotonic() >= scc_a_progress_diagnostic_at
                        ):
                            print(
                                "DB_SCC_A_TX_PROGRESS "
                                f"pc={interrupted_pc:08X} "
                                f"command={rsp.read_memory(BOARD_SCC_A_COMMAND, 1)[0]:02X} "
                                f"pointer={int.from_bytes(rsp.read_memory(0x001E2120, 4), 'big'):08X} "
                                f"active_pointer={int.from_bytes(rsp.read_memory(0x001E211C, 4), 'big'):08X} "
                                f"toggle={rsp.read_memory(0x001E29D9, 1)[0]:02X} "
                                f"guard={rsp.read_memory(0x001E2334, 1)[0]:02X}",
                                flush=True,
                            )
                            scc_a_progress_diagnostic_at = (
                                time.monotonic() + IDLE_DIAGNOSTIC_SECONDS
                            )
                        step_past_breakpoint(rsp, rte_pc)
                        if board_interrupt_stack:
                            # A nested timer has returned to the outer ISR.
                            # Leave the pending state in place; the outer
                            # handler may now finish its wait.
                            continue
                        timer_status = rsp.read_memory(BOARD_TIMER_STATUS, 1)[0]
                        rsp.write_memory(
                            BOARD_TIMER_STATUS,
                            bytes([
                                timer_status
                                & ~(BOARD_TIMER_PENDING | BOARD_SCC_A_RX_PENDING)
                            ]),
                        )
                        # No UART IRQ on an idle link. A pending byte/ready
                        # transition still uses the original UART handler.
                        uart_service_burst = 0
                        resume_snapshot = rsp.read_register_snapshot()
                        if not interrupts_unmasked(resume_snapshot.core_u32[REG_SR]):
                            timer_budget.defer_ticks(board_timer_ticks_remaining)
                            board_timer_ticks_remaining = 0
                            continue
                        if uart_work_pending(rsp, bool(pending), tx_status_watch):
                            interrupted_pc, _ = inject_interrupt(
                                rsp, TIMER_VECTOR, TIMER_HANDLER, snapshot=resume_snapshot,
                            )
                            uart_return_pc = interrupted_pc
                        elif board_timer_ticks_remaining:
                            interrupted_pc, _ = inject_board_tick(rsp, timer_target, snapshot=resume_snapshot)
                            board_interrupt_stack.append((interrupted_pc, timer_target[2]))
                            board_timer_ticks_remaining -= 1
                            timer_injections += 1
                        continue
                    if uart_return_pc is not None and pc == UART_TIMER_RTE_PC:
                        step_past_breakpoint(rsp, UART_TIMER_RTE_PC)
                        uart_return_pc = None
                        uart_service_burst += 1
                        if tx_status_watch:
                            remaining = tx_ready_at - time.monotonic()
                            if remaining > 0:
                                # QEMU is stopped while the 8-N-1 frame leaves
                                # the emulated transmitter at exactly 9600 baud.
                                time.sleep(remaining)
                            rsp.write_memory(MAIN_TX_STATUS, bytes([TX_READY]))
                            set_watchpoint(rsp, 3, MAIN_TX_STATUS, False)
                            tx_status_watch = False
                        if board_interrupt_stack:
                            # Resume the blocked outer handler after its real
                            # response; don't expand the reserved batch here.
                            continue
                        resume_snapshot = rsp.read_register_snapshot()
                        if not interrupts_unmasked(resume_snapshot.core_u32[REG_SR]):
                            timer_budget.defer_ticks(board_timer_ticks_remaining)
                            board_timer_ticks_remaining = 0
                            continue
                        if should_drain_uart_before_board(
                            uart_work_pending(rsp, bool(pending)),
                            board_timer_ticks_remaining, uart_service_burst,
                        ):
                            # Drain an already queued frame at the physical UART
                            # rate, yielding periodically to already due board
                            # timers even if the serial link remains busy.
                            interrupted_pc, _ = inject_interrupt(
                                rsp, TIMER_VECTOR, TIMER_HANDLER, snapshot=resume_snapshot,
                            )
                            uart_return_pc = interrupted_pc
                        elif board_timer_ticks_remaining:
                            # Deliver the remaining programmed DUART hardware
                            # ticks as a bounded batch under the icount cap.
                            interrupted_pc, _ = inject_board_tick(rsp, timer_target, snapshot=resume_snapshot)
                            board_interrupt_stack.append(
                                (interrupted_pc, timer_target[2])
                            )
                            board_timer_ticks_remaining -= 1
                            timer_injections += 1
                        continue
                    if pc == RUNTIME_IO_INIT_RETURN_PC:
                        set_watchpoint(rsp, 0, RUNTIME_IO_INIT_RETURN_PC, False)
                        timer_config = read_duart_timer_config(rsp, args.duart_x1_hz)
                        timer_budget = DuartWallTimer(timer_config)
                        cpu_run_budget = CpuRunBudget(foreground_timer_quantum(args.timer_interval, timer_config))
                        timer_target = board_service_target(timer_config.vector)
                        for register in (DUART_TIMER_ACR, DUART_TIMER_CTUR, DUART_TIMER_CTLR, DUART_TIMER_IVR):
                            set_watchpoint(rsp, 2, register, True)
                        vector_target = int.from_bytes(
                            rsp.read_memory(0x1100 + TIMER_VECTOR * 4, 4), "big"
                        )
                        if vector_target != TIMER_HANDLER:
                            raise RuntimeError(
                                "runtime timer vector mismatch: "
                                f"expected {TIMER_HANDLER:08X}, got {vector_target:08X}"
                            )
                        board_timer_target = int.from_bytes(
                            rsp.read_memory(
                                0x1100 + BOARD_TIMER_VECTOR * 4, 4
                            ),
                            "big",
                        )
                        if board_timer_target != BOARD_TIMER_HANDLER:
                            raise RuntimeError(
                                "runtime board timer vector mismatch: "
                                f"expected {BOARD_TIMER_HANDLER:08X}, "
                                f"got {board_timer_target:08X}"
                            )
                        board_scc_a_target = int.from_bytes(
                            rsp.read_memory(
                                0x1100 + BOARD_SCC_A_VECTOR * 4, 4
                            ),
                            "big",
                        )
                        if board_scc_a_target != BOARD_SCC_A_HANDLER:
                            raise RuntimeError(
                                "runtime SCC A vector mismatch: "
                                f"expected {BOARD_SCC_A_HANDLER:08X}, "
                                f"got {board_scc_a_target:08X}"
                            )
                        uart_state = int.from_bytes(
                            rsp.read_memory(UART_STATE_ADDRESS, 2), "big"
                        )
                        print(
                            "DB_RUNTIME_IO_INITIALIZED "
                            f"board_timer_vector={BOARD_TIMER_VECTOR} "
                            f"board_timer_handler={BOARD_TIMER_HANDLER:08X} "
                            f"uart_vector={TIMER_VECTOR} "
                            f"uart_handler={TIMER_HANDLER:08X} "
                            f"uart_state={uart_state:04X}",
                            flush=True,
                        )
                        if not uart_state_is_ready(uart_state):
                            print(
                                "DB_RUNNING_ORIGINAL_UART_INITIALIZER "
                                f"entry={UART_INIT_ENTRY:08X}",
                                flush=True,
                            )
                            uart_state = run_original_uart_initializer(rsp)
                            print(
                                "DB_UART_READY source=original-initializer "
                                f"value={uart_state:04X}",
                                flush=True,
                            )
                        board_input = apply_cabinet_inputs(
                            rsp.read_memory(BOARD_PORT_INPUT, 1)[0],
                            door_closed=door_closed,
                        )
                        rsp.write_memory(
                            BOARD_PORT_INPUT, bytes([board_input])
                        )
                        print(
                            "DB_CABINET_INPUT_STATE "
                            f"door={'closed' if door_closed else 'open'} "
                            f"mask={BOARD_DOOR_CLOSED_MASK:02X} "
                            f"board_input={board_input:02X}",
                            flush=True,
                        )
                        # Track the handlers at their actual RTE instructions.
                        # A breakpoint on the interrupted PC is ambiguous: the
                        # handler can legitimately call that same address before
                        # returning, which previously caused false completions.
                        set_watchpoint(rsp, 0, BOARD_TIMER_RTE_PC, True)
                        set_watchpoint(rsp, 0, BOARD_SCAN_COMPLETE_PC, True)
                        set_watchpoint(rsp, 0, BOARD_SCC_A_RTE_PC, True)
                        set_watchpoint(rsp, 0, UART_TIMER_RTE_PC, True)
                        set_watchpoint(
                            rsp, 0, VirtualCoinValidator.PAIRING_READ_PC,
                            True,
                        )
                        set_watchpoint(
                            rsp, 0, VirtualCoinValidator.PAIRING_COMPARE_PC,
                            True,
                        )
                        set_watchpoint(
                            rsp, 0, VirtualCoinValidator.PAIRING_FAILURE_PC,
                            True,
                        )
                        if args.trace_diagnostics:
                            set_watchpoint(rsp, 2, AUX_TRANSACTION_ACTIVE, True)
                            set_watchpoint(rsp, 2, AUX_PROFILE_SELECTOR, True)
                            set_watchpoint(rsp, 2, MP_STATE_ADDRESS, True)
                            set_watchpoint(rsp, 2, MP_REQUIRED_TYPE_STATE, True)
                            set_watchpoint(rsp, 2, MP_DETECTED_TYPE_STATE, True)
                        set_watchpoint(rsp, 0, AUX_RESPONSE_CHECK_PC, True)
                        set_watchpoint(
                            rsp, 0, AUX_PROFILE_RESPONSE_CHECK_PC, True
                        )
                        set_watchpoint(
                            rsp, 0, AUX_VALUE_RESPONSE_CHECK_PC, True
                        )
                        timer_enabled = True
                        timer_budget.start()
                        print(
                            "DB_RTC4543_ENABLED "
                            f"control={RTC_CONTROL_REGISTER:08X} "
                            f"port={RTC_PORT_REGISTER:08X} "
                            f"data_mask={RTC_DATA:02X} "
                            f"initial={args.rtc_date.isoformat()}",
                            flush=True,
                        )
                        print(
                            "DB_TIMER_ENABLED source=board-io "
                            f"run_ms={args.timer_interval * 1000:.3f} "
                            "clock=monotonic-wall "
                            f"max_batch={MAX_WALL_TIMER_BATCH_TICKS} max_pending={MAX_WALL_TIMER_PENDING_TICKS} "
                            f"foreground_ms={cpu_run_budget.seconds * 1000:.3f} "
                            f"acr={timer_config.acr:02X} preset={timer_config.preset:04X} "
                            f"vector={timer_config.vector} x1_hz={timer_config.x1_hz} "
                            f"period_ms={timer_config.period_seconds * 1000:.6f} "
                            "x1_source=configured-default-or-cli "
                            f"icount_shift={args.icount_shift} "
                            f"max_guest_ips={1_000_000_000 // (1 << args.icount_shift)}",
                            flush=True,
                        )
                        continue
                    raise RuntimeError(
                        f"m68k stopped without known watchpoint at PC={pc:08X}: {reply}"
                    )

                if kind == "watch" and address == MAIN_DATA:
                    # QEMU reports the PC after MOVE.B has written the byte.
                    pc = rsp.read_register_u32(REG_PC)
                    value = rsp.read_memory(MAIN_DATA, 1)
                    if not should_forward_tx(pc, value):
                        filtered_loader_tx += 1
                        if filtered_loader_tx <= 8:
                            print(
                                "DB_LOCAL_LOADER_TX "
                                f"{value.hex().upper()} pc={pc:08X} "
                                "(COM3 filtered)",
                                flush=True,
                            )
                        # Keep TX ready so internal loader/UART-clear traffic
                        # completes without becoming guest COM3 payload.
                        rsp.write_memory(MAIN_TX_STATUS, bytes([TX_READY]))
                        if pc == LOADER_IDLE_TX_STOP_PC and value == b"\xFF":
                            # 0x0C40..0x0C88 is only a finite LED/idle delay.
                            # Skip to its register-restore epilogue after the
                            # first observed iteration; no validation or image
                            # processing occurs inside the skipped loop.
                            rsp.write_register_u32(REG_PC, LOADER_IDLE_RETURN_PC)
                        continue
                    if filtered_loader_tx:
                        print(
                            "DB_LOCAL_LOADER_TX_COMPLETE "
                            f"count={filtered_loader_tx}",
                            flush=True,
                        )
                        filtered_loader_tx = 0
                    forward_immediately = True
                    if not first_frame_reported:
                        if not first_frame and value == b"\x01":
                            first_frame_started_at = time.monotonic()
                        if first_frame_started_at is not None:
                            forward_immediately = False
                            first_frame.extend(value)
                            if initvideo_frame_complete(first_frame):
                                generation_ms = (
                                    time.monotonic() - first_frame_started_at
                                ) * 1000.0
                                completed_frame = complete_initvideo_board_profile(
                                    bytes(first_frame), rtc.now()
                                )
                                wire_ms = send_serial_frame(
                                    com3, completed_frame
                                ) * 1000.0
                                initial_retry_frame = completed_frame
                                initial_retry_stage = "startup"
                                initial_frame_attempts = 1
                                initial_frame_retry_at = (
                                    time.monotonic() + INITIAL_FRAME_RETRY_SECONDS
                                )
                                print(
                                    "DB_INITVIDEO_BOARD_PROFILE_COMPLETED "
                                    f"length={len(completed_frame)} "
                                    f"time={initvideo_time_text(completed_frame)} "
                                    f"attempt={initial_frame_attempts} "
                                    f"generation_ms={generation_ms:.3f} "
                                    f"wire_ms={wire_ms:.3f} "
                                    f"data={completed_frame.hex(' ').upper()}",
                                    flush=True,
                                )
                                first_frame_reported = True
                    if forward_immediately:
                        outgoing, clock_completed = (
                            initvideo_clock_forwarder.feed(value)
                        )
                        if outgoing:
                            touch_outgoing = bytearray()
                            for touch_byte in outgoing:
                                chunk, corrected_point = touch_click_forwarder.feed(
                                    bytes([touch_byte]), last_touch_point
                                )
                                touch_outgoing.extend(chunk)
                                if corrected_point is not None:
                                    print(
                                        "DB_TOUCH_COORDS_COMPLETED "
                                        "original=0,0 "
                                        f"x={corrected_point[0]} "
                                        f"y={corrected_point[1]}",
                                        flush=True,
                                    )
                            outgoing = bytes(touch_outgoing)
                        if outgoing:
                            if len(outgoing) == 1:
                                com3.sendall(outgoing)
                            else:
                                send_serial_frame(com3, outgoing)
                            # Log what actually reached COM3. A literal 0x01
                            # inside a frame is data, not a new frame start.
                            if wire_frame or outgoing[:1] == b"\x01":
                                wire_frame.extend(outgoing)
                                last_wire_tx_at = time.monotonic()
                                if len(wire_frame) > 8192:
                                    print("DB_FRAME_TOO_LONG", flush=True)
                                    wire_frame.clear()
                        if clock_completed:
                            # The original database can issue a fresh INIT
                            # before Windows opens COM3. Retry only this newest
                            # original frame, never an older startup attempt.
                            initial_retry_frame = outgoing
                            initial_retry_stage = "later"
                            guest_systeminfo_seen = False
                            guest_systeminfo_tail.clear()
                            initial_frame_attempts = 1
                            initial_frame_retry_at = (
                                time.monotonic() + INITIAL_FRAME_RETRY_SECONDS
                            )
                            print(
                                "DB_INITVIDEO_RETRY_REFRESHED "
                                "source=original-later-initvideo "
                                f"attempts={initial_frame_attempts}",
                                flush=True,
                            )
                            print(
                                "DB_INITVIDEO_CLOCK_COMPLETED "
                                f"time={initvideo_time_text(outgoing)} data={outgoing.hex(' ').upper()}",
                                flush=True,
                            )
                    last_uart_activity_at = time.monotonic()
                    # A real UART clears TX-ready until the complete 10-bit
                    # serial frame has left the transmitter. Without this,
                    # QEMU's fast polling loop floods the XP serial driver.
                    rsp.write_memory(MAIN_TX_STATUS, b"\x00")
                    tx_ready_at = time.monotonic() + UART_FRAME_SECONDS
                    if not tx_status_watch:
                        set_watchpoint(rsp, 3, MAIN_TX_STATUS, True)
                        tx_status_watch = True
                    if value == last_tx:
                        repeated_tx += 1
                        if repeated_tx % 256 == 0:
                            print(
                                f"DB_TO_COM3_REPEAT {value.hex().upper()} "
                                f"count={repeated_tx}",
                                flush=True,
                            )
                    else:
                        last_tx = value
                        repeated_tx = 1
                        print(
                            f"DB_TO_COM3 {value.hex(' ').upper()} pc={pc:08X}",
                            flush=True,
                        )
                    continue

                if kind == "watch" and address in (
                    RTC_CONTROL_REGISTER, RTC_PORT_REGISTER
                ):
                    raw = rsp.read_memory(RTC_PORT_REGISTER, 1)[0]
                    feedback = rtc.port_write(raw)
                    if feedback != raw:
                        rsp.write_memory(RTC_PORT_REGISTER, bytes([feedback]))
                    if rtc.completed_read is not None:
                        rtc_reads_reported += 1
                        if rtc_reads_reported <= 3:
                            print(
                                "DB_RTC4543_READ "
                                f"count={rtc_reads_reported} "
                                f"bcd={rtc.completed_read.hex(' ').upper()}",
                                flush=True,
                            )
                        rtc.completed_read = None
                    if rtc.completed_write is not None:
                        print(
                            "DB_RTC4543_WRITE "
                            f"time={rtc.completed_write.isoformat()}",
                            flush=True,
                        )
                        rtc.completed_write = None
                    continue

                if kind == "watch" and address == BOARD_LATCH:
                    pc = rsp.read_register_u32(REG_PC)
                    feedback = board_latch_feedback(pc)
                    if feedback is not None:
                        rsp.write_memory(BOARD_LATCH, bytes([feedback]))
                        print(
                            "DB_BOARD_LATCH_FEEDBACK "
                            f"value={feedback:02X} pc={pc:08X}",
                            flush=True,
                        )
                    continue

                if kind == "watch" and address == BOARD_SCC_CONTROL_A:
                    command = rsp.read_memory(BOARD_SCC_CONTROL_A, 1)[0]
                    rsp.write_memory(
                        BOARD_SCC_CONTROL_A, bytes([BOARD_SCC_A_TX_READY])
                    )
                    if not board_scc_a_feedback_reported:
                        pc = rsp.read_register_u32(REG_PC)
                        print(
                            "DB_BOARD_SCC_A_FEEDBACK "
                            f"command={command:02X} "
                            f"status={BOARD_SCC_A_TX_READY:02X} pc={pc:08X}",
                            flush=True,
                        )
                        board_scc_a_feedback_reported = True
                    continue

                if kind == "watch" and address == BOARD_SCC_CONTROL_B:
                    command = rsp.read_memory(BOARD_SCC_CONTROL_B, 1)[0]
                    coin_validator.publish(rsp)
                    if not board_scc_feedback_reported:
                        pc = rsp.read_register_u32(REG_PC)
                        print(
                            "DB_BOARD_SCC_FEEDBACK "
                            f"command={command:02X} "
                            f"status={coin_validator.status():02X} pc={pc:08X}",
                            flush=True,
                        )
                        board_scc_feedback_reported = True
                    continue

                if kind == "watch" and address == BOARD_SCC_DATA_B:
                    value = rsp.read_memory(BOARD_SCC_DATA_B, 1)[0]
                    remaining = rsp.read_memory(BOARD_SCC_STATE + 1, 1)[0]
                    prior_gap = coin_validator.last_pairing_gap
                    prior_count_lag = coin_validator.last_pairing_count_lag
                    response = coin_validator.observe_tx(value, remaining)
                    if coin_validator.last_pairing_count_lag and not prior_count_lag:
                        print(
                            "DB_VIRTUAL_MP_TX_COUNT_LAG "
                            "observed_remaining=00 accepted_checksum=1",
                            flush=True,
                        )
                    gap_log = pairing_gap_log_transition(
                        prior_gap, coin_validator.last_pairing_gap
                    )
                    if gap_log is not None:
                        print(gap_log, flush=True)
                    if remaining == 1 and coin_validator.last_tx_frame is not None:
                        frame = coin_validator.last_tx_frame
                        print(
                            "DB_SCC_B_TX_FRAME "
                            f"data={bytes([len(frame)]).hex(' ').upper()} "
                            f"{frame.hex(' ').upper()}",
                            flush=True,
                        )
                    if coin_validator.coin_window:
                        coin_response, coin_events = coin_validator.prepare_coin_reply(rsp, time.monotonic())
                        for coin_event in coin_events:
                            print(coin_event, flush=True)
                        if coin_response is not None:
                            response = coin_response
                    if response is not None:
                        if coin_validator.challenge_pending:
                            expected, response = coin_validator.prepare_pairing_reply(rsp)
                            print(
                                "DB_VIRTUAL_MP_PAIRING_EXPECTED "
                                f"value={expected:04X} "
                                f"channel={coin_validator.last_tx_frame[2]} "
                                f"denomination={response[0]:02X}",
                                flush=True,
                            )
                        if coin_validator.type_pending:
                            detected, response = coin_validator.prepare_type_reply(rsp)
                            print(
                                "DB_VIRTUAL_MP_TYPE_REPLY "
                                f"required={rsp.read_memory(MP_REQUIRED_TYPE_STATE, 1)[0]:02X} "
                                f"detected={detected:02X} "
                                f"wire={response.hex(' ').upper()}",
                                flush=True,
                            )
                        coin_validator.publish(rsp)
                        print(
                            "DB_VIRTUAL_MP_REPLY "
                            f"data={response.hex(' ').upper()}",
                            flush=True,
                        )
                    print(
                        "DB_SCC_B_TX "
                        f"value={value:02X} remaining={remaining:02X}",
                        flush=True,
                    )
                    continue

                if kind == "rwatch" and address == BOARD_SCC_DATA_B:
                    pairing_rx_detail = ""
                    if coin_validator.challenge_pending:
                        pairing_rx_detail = (
                            f" pc={rsp.read_register_u32(REG_PC):08X}"
                            f" receive_buffer={rsp.read_memory(0x001E223C, 4).hex(' ').upper()}"
                            f" timer_irqs={timer_injections}"
                        )
                    received, stale_status = consume_coin_validator_byte(
                        coin_validator, rsp
                    )
                    if received is not None:
                        print(
                            "DB_VIRTUAL_MP_RX "
                            f"value={received:02X} "
                            f"remaining={coin_validator.pending_count()}"
                            + pairing_rx_detail,
                            flush=True,
                        )
                    elif stale_status is not None:
                        print(
                            "DB_DUART_B_EMPTY_RX_STATUS_CLEARED "
                            f"pc={rsp.read_register_u32(REG_PC):08X} "
                            f"before={stale_status:02X} "
                            f"after={coin_validator.status():02X} pending=0",
                            flush=True,
                        )
                    continue

                if kind == "watch" and address in BOARD_PORT_STROBES:
                    mask = rsp.read_memory(address, 1)[0]
                    current = rsp.read_memory(BOARD_PORT_INPUT, 1)[0]
                    feedback = apply_board_port_strobe(current, address, mask)
                    feedback = apply_cabinet_inputs(
                        feedback, door_closed=door_closed
                    )
                    rsp.write_memory(BOARD_PORT_INPUT, bytes([feedback]))
                    door_input_pending = False
                    if (current ^ feedback) & BOARD_DOOR_CLOSED_MASK:
                        print(
                            "DB_CABINET_INPUT_STATE "
                            f"door={'closed' if door_closed else 'open'} "
                            f"mask={BOARD_DOOR_CLOSED_MASK:02X} "
                            f"board_input={feedback:02X} source=board-strobe",
                            flush=True,
                        )
                    board_port_strobes += 1
                    if board_port_strobes == 1:
                        pc = rsp.read_register_u32(REG_PC)
                        print(
                            "DB_BOARD_PORT_FEEDBACK_ENABLED "
                            f"input={BOARD_PORT_INPUT:08X} pc={pc:08X}",
                            flush=True,
                        )
                    continue

                if (
                    kind == "rwatch"
                    and address == BOARD_PORT_INPUT
                    and idle_board_input_watch
                ):
                    pc = rsp.read_register_u32(REG_PC)
                    value = rsp.read_memory(BOARD_PORT_INPUT, 1)[0]
                    set_watchpoint(rsp, 3, BOARD_PORT_INPUT, False)
                    idle_board_input_watch = False
                    print(
                        "DB_IDLE_BOARD_INPUT_READ "
                        f"pc={pc:08X} value={value:02X}",
                        flush=True,
                    )
                    continue

                if (
                    kind == "rwatch"
                    and address == MAIN_TX_STATUS
                    and tx_status_watch
                ):
                    if time.monotonic() >= tx_ready_at:
                        rsp.write_memory(MAIN_TX_STATUS, bytes([TX_READY]))
                        set_watchpoint(rsp, 3, MAIN_TX_STATUS, False)
                        tx_status_watch = False
                    # The status read has already completed. On the next loop
                    # iteration the firmware polls again and sees the update.
                    continue

                if kind == "rwatch" and address == MAIN_RX_STATUS:
                    pc = rsp.read_register_u32(REG_PC)
                    if pc in TX_STATUS_OVERLAP_STOP_PCS:
                        if pc not in reported_tx_status_overlaps:
                            print(
                                f"DB_UART_TX_STATUS_OVERLAP pc={pc:08X} "
                                "(continued, not RX)",
                                flush=True,
                            )
                            reported_tx_status_overlaps.add(pc)
                        continue
                    if rx_ready_seen:
                        set_watchpoint(rsp, 3, MAIN_RX_STATUS, False)
                        rx_status_watch = False
                        set_watchpoint(rsp, 3, MAIN_DATA, True)
                        rx_data_watch = True
                        continue
                    if not pending:
                        if not rx_wait_reported:
                            print(
                                "DB_WAITING_FOR_COM3 " + read_register_snapshot(rsp),
                                flush=True,
                            )
                            rx_wait_reported = True
                        ensure_com3_receiver_alive(receiver_errors)
                        if process.poll() is not None:
                            raise RuntimeError("m68k QEMU exited")
                        # A real UART returns RX-ready=0 immediately. Do not
                        # freeze the CPU waiting for the peer: the interrupt
                        # handler must return so the database application can
                        # produce its own startup traffic.
                        rsp.write_memory(MAIN_RX_STATUS, b"\x00")
                        set_watchpoint(rsp, 3, MAIN_RX_STATUS, False)
                        rx_status_watch = False
                        continue
                    rx_wait_reported = False
                    rsp.write_memory(MAIN_DATA, bytes([pending[0]]))
                    rsp.write_memory(MAIN_RX_STATUS, bytes([RX_READY]))
                    rx_ready_seen = True
                    continue

                if kind == "rwatch" and address == MAIN_DATA and rx_data_watch:
                    received_byte = pending.popleft()
                    if observe_guest_systeminfo(
                        guest_systeminfo_tail, received_byte
                    ):
                        guest_systeminfo_seen = True
                        print(
                            "DB_INITVIDEO_GUEST_SYSTEMINFO prefix=M90-",
                            flush=True,
                        )
                    last_uart_activity_at = time.monotonic()
                    rsp.write_memory(MAIN_RX_STATUS, b"\x00")
                    set_watchpoint(rsp, 3, MAIN_DATA, False)
                    rx_data_watch = False
                    rx_ready_seen = False
                    continue

                raise RuntimeError(
                    f"unexpected UART watchpoint kind={kind} address={address:08X}"
                )
    finally:
        failure_type = sys.exc_info()[0]
        cleanup_trigger = failure_type.__name__ if failure_type else "bridge-return"
        controls.close()
        receiver_stop.set()
        if receiver_thread is not None:
            receiver_thread.join(timeout=1.0)
        if com3 is not None:
            com3.close()
        stop_database_process(process, cleanup_trigger)


def parse_int(value: str) -> int:
    return int(value, 0)


def run_with_log_file(action: Callable[[], int], path: Path) -> int:
    """Write live bridge output directly, avoiding PowerShell's line pipeline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", buffering=1) as log:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            return action()
        except Com3Disconnected as exc:
            print(f"DB_COM3_DISCONNECTED {exc}", file=log, flush=True)
            return 1
        except BaseException:
            traceback.print_exc(file=log)
            raise
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loader", type=Path, required=True)
    parser.add_argument("--expected-loader-sha256", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-database-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--factory", type=Path,
                        help="original Factory module, executed before cold-start programming")
    parser.add_argument("--expected-factory-sha256")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--d3", type=parse_int)
    source.add_argument("--runtime-dump", type=Path)
    parser.add_argument("--qemu", type=Path, default=DEFAULT_QEMU)
    parser.add_argument("--gdb-port", type=int, default=1235)
    parser.add_argument("--com3-host", default="127.0.0.1")
    parser.add_argument("--com3-port", type=int, default=4553)
    parser.add_argument("--control-port", type=int, default=4554)
    parser.add_argument("--touch-state", type=Path,
                        help="virtual touch-controller calibration sidecar for this working image")
    parser.add_argument("--connect-timeout", type=float, default=120.0)
    parser.add_argument("--rtc-date", type=dt.datetime.fromisoformat, default=RTC_DEFAULT_TIME,
                        help="initial RTC calendar; default 2012-02-01T22:14:00")
    parser.add_argument("--timer-interval", type=float, default=0.01)
    parser.add_argument("--duart-x1-hz", type=int, default=DEFAULT_X1_HZ,
                        help="DUART crystal/input Hz, independent of MC68331 clock (default: 3686400)")
    parser.add_argument(
        "--icount-shift", type=int, choices=(5, 6),
        default=GUEST_ICOUNT_SHIFT,
        help="database timing: 5=31.25M or 6=15.625M guest instructions/s",
    )
    parser.add_argument("--log-file", type=Path)
    parser.add_argument(
        "--admission-eeprom", type=Path,
        help="256-byte ATmega48 V3 M90 admission-card EEPROM image",
    )
    parser.add_argument(
        "--trace-diagnostics", action="store_true",
        help="enable extra logging-only hardware watchpoints",
    )
    parser.add_argument(
        "--fast-tb", action="store_true",
        help="use normal TCG translation blocks; omit for single-instruction fallback",
    )
    parser.add_argument(
        "--door-open",
        action="store_true",
        help=(
            "expose the non-monetary cabinet door input as open; the safe "
            "default models the pressed/closed switch"
        ),
    )
    args = parser.parse_args()
    if (args.factory is None) != (args.expected_factory_sha256 is None):
        parser.error("--factory and --expected-factory-sha256 must be provided together")
    try:
        validate_timer_interval(args.timer_interval)
    except ValueError as exc:
        parser.error(str(exc))
    if args.log_file is not None:
        return run_with_log_file(lambda: run_bridge(args), args.log_file)
    return run_bridge(args)


if __name__ == "__main__":
    raise SystemExit(main())
