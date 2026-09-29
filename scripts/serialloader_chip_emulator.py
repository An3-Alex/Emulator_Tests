#!/usr/bin/env python3
"""Model the documented SerialLoader programming cycle of an owner DB chip."""

from __future__ import annotations

import argparse
import datetime as dt
import enum
import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from inspect_owner_database import inspect_dump as inspect_database
from owner_database_runtime import prepare_runtime


SYNC_WAIT = bytes.fromhex("1B53594E4353594E4357414954474F0A")
STATUS_DATE_ACCEPTED = bytes.fromhex("1B31")
STATUS_READY = bytes.fromhex("1B32")
STATUS_UPLOAD_ACCEPTED = bytes.fromhex("1B33")
EXPECTED_FAMILY = "61640400"


class ChipPhase(enum.Enum):
    EMPTY = "empty"
    LOADER_RUNNING = "loader-running"
    FACTORY_RESET = "factory-reset"
    DATE_SET = "date-set"
    CONFIG_SET = "config-set"
    PROGRAMMED = "programmed"


def pinned_bytes(path: Path, expected_sha256: str) -> tuple[bytes, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest().upper()
    expected = expected_sha256.replace(" ", "").upper()
    if digest != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {digest}")
    return data, digest


def bcd(value: int) -> int:
    if not 0 <= value <= 99:
        raise ValueError("BCD value must be between 0 and 99")
    return ((value // 10) << 4) | (value % 10)


def build_date_wire_frame(when: dt.datetime, daylight_saving: bool = False) -> bytes:
    # SerialLoader maps Monday..Sunday to 1..7 and writes the inverse of its
    # IsDaylightSavingTime result: summer=0, standard=1.
    parameters = bytes([
        bcd(when.second), bcd(when.minute), 0 if daylight_saving else 1,
        bcd(when.day), bcd(when.month), bcd(when.year % 100), when.isoweekday(),
    ])
    checksum = sum(parameters) & 0xFF
    return SYNC_WAIT + parameters + bytes([checksum])


class VirtualDatabaseChip:
    def __init__(self) -> None:
        self.phase = ChipPhase.EMPTY
        self.loader_sha256: str | None = None
        self.database_sha256: str | None = None
        self.runtime_sha256: str | None = None
        self.factory_modules: list[dict] = []
        self.config_modules: list[dict] = []
        self.programmed_time: str | None = None
        self.runtime: bytes | None = None
        self.events: list[dict] = []

    def _event(self, operation: str, status: bytes) -> None:
        self.events.append({
            "operation": operation,
            "phase": self.phase.value,
            "status_hex": status.hex(" ").upper(),
        })

    def upload_loader(self, path: Path, expected_sha256: str) -> bytes:
        if self.phase is not ChipPhase.EMPTY:
            raise RuntimeError("Upload (R) is only valid for an empty/reset chip")
        data, digest = pinned_bytes(path, expected_sha256)
        if len(data) < 0x410 or data[0x0E:0x14] != bytes.fromhex("4EF900000CC8"):
            raise ValueError("loader entry is not the verified JMP 0x00000CC8")
        self.loader_sha256 = digest
        self.phase = ChipPhase.LOADER_RUNNING
        self._event("upload-r-loader", STATUS_READY)
        return STATUS_READY

    def set_date(self, when: dt.datetime, daylight_saving: bool = False) -> tuple[bytes, bytes]:
        if self.phase is not ChipPhase.FACTORY_RESET:
            raise RuntimeError("date requires FactoryReset first")
        frame = build_date_wire_frame(when, daylight_saving)
        # Validate the same seven-byte additive header accepted by the loader.
        if (sum(frame[16:23]) & 0xFF) != frame[23]:
            raise AssertionError("internal date-frame checksum error")
        self.programmed_time = when.replace(microsecond=0).isoformat()
        self.phase = ChipPhase.DATE_SET
        status = STATUS_DATE_ACCEPTED + STATUS_READY
        self._event("set-date", status)
        return frame, status

    def upload_factory_module(self, path: Path, expected_sha256: str) -> bytes:
        if self.phase is not ChipPhase.LOADER_RUNNING:
            raise RuntimeError("FactoryReset upload requires the RAM loader")
        _, digest = pinned_bytes(path, expected_sha256)
        report = inspect_database(path)
        if report["sha256"] != expected_sha256.replace(" ", "").upper():
            raise ValueError("FactoryReset SHA-256 mismatch")
        if report["role"] != "database_module" or not report["recognized"]:
            raise ValueError("FactoryReset file is not a recognized database module")
        if report["header"]["module_family"] != EXPECTED_FAMILY:
            raise ValueError("FactoryReset module belongs to another database family")
        self.factory_modules.append({"path": str(path.resolve()), "sha256": digest})
        self.phase = ChipPhase.FACTORY_RESET
        status = STATUS_UPLOAD_ACCEPTED + STATUS_READY
        self._event("upload-l-factory", status)
        return status

    def upload_config_module(self, path: Path, expected_sha256: str) -> bytes:
        if self.phase is not ChipPhase.DATE_SET:
            raise RuntimeError("config upload requires FactoryReset and date first")
        _, digest = pinned_bytes(path, expected_sha256)
        report = inspect_database(path)
        if report["role"] != "database_module" or not report["recognized"]:
            raise ValueError("config file is not a recognized database module")
        if report["header"]["module_family"] != EXPECTED_FAMILY:
            raise ValueError("config module belongs to another database family")
        self.config_modules.append({"path": str(path.resolve()), "sha256": digest})
        self.phase = ChipPhase.CONFIG_SET
        status = STATUS_UPLOAD_ACCEPTED + STATUS_READY
        self._event("upload-l-config", status)
        return status

    def upload_database(
        self,
        path: Path,
        expected_sha256: str,
        *,
        d3: int | None = None,
        runtime_dump: Path | None = None,
    ) -> bytes:
        if self.phase is not ChipPhase.CONFIG_SET:
            raise RuntimeError("database upload requires loader, factory, date and config")
        runtime, report = prepare_runtime(
            path, expected_sha256, d3=d3, runtime_dump_path=runtime_dump
        )
        self.runtime = runtime
        self.database_sha256 = report["transport_sha256"]
        self.runtime_sha256 = report["runtime_sha256"]
        self.phase = ChipPhase.PROGRAMMED
        self._event("upload-l-database", STATUS_UPLOAD_ACCEPTED)
        return STATUS_UPLOAD_ACCEPTED

    def manifest(self) -> dict:
        return {
            "schema": "m90-virtual-database-chip-v1",
            "phase": self.phase.value,
            "loader_sha256": self.loader_sha256,
            "database_sha256": self.database_sha256,
            "runtime_sha256": self.runtime_sha256,
            "programmed_time": self.programmed_time,
            "factory_modules": self.factory_modules,
            "config_modules": self.config_modules,
            "runtime_held_in_memory": self.runtime is not None,
            "events": self.events,
        }


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loader", type=Path, required=True)
    parser.add_argument("--expected-loader-sha256", required=True)
    parser.add_argument("--factory", type=Path, required=True)
    parser.add_argument("--expected-factory-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-database-sha256", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--d3", type=parse_int)
    source.add_argument("--runtime-dump", type=Path)
    parser.add_argument("--date", default="2012-02-01T22:14:00")
    args = parser.parse_args()

    chip = VirtualDatabaseChip()
    chip.upload_loader(args.loader, args.expected_loader_sha256)
    chip.upload_factory_module(args.factory, args.expected_factory_sha256)
    chip.set_date(dt.datetime.fromisoformat(args.date))
    chip.upload_config_module(args.config, args.expected_config_sha256)
    chip.upload_database(
        args.database, args.expected_database_sha256,
        d3=args.d3, runtime_dump=args.runtime_dump,
    )
    print(json.dumps(chip.manifest(), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
