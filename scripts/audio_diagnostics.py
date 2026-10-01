"""Best-effort audio failure evidence; never change the mounted guest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphics_update import inside

GUEST_FILES = (
    "NVRAM/m90_audio_install.log",
    "NVRAM/m90_audio_verify.log",
    "NVRAM/m90_audio_stage.json",
    "WINDOWS/setupapi.log",
)
MAX_LOG_BYTES = 4 * 1024 * 1024


def write_report(destination: Path, name: str, report: dict) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / name).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")


def capture_guest(qmp, destination: Path, phase: str, error: Exception,
                  received: bytes = b"") -> None:
    report = {"phase": phase, "error": str(error), "screenshots": {}, "capture_errors": {}}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "serial-result.log").write_bytes(received)
    if qmp is not None:
        try:
            report["qemu_status"] = qmp.execute("query-status")
        except Exception as exc:
            report["capture_errors"]["status"] = str(exc)
        for device in ("lower", "upper"):
            for format_ in ("png", "ppm"):
                filename = destination / f"xp-{device}.{format_}"
                try:
                    arguments = {"filename": filename.resolve().as_posix(), "device": device}
                    if format_ == "png":
                        arguments["format"] = "png"
                    qmp.execute("screendump", arguments)
                    report["screenshots"][device] = filename.name
                    break
                except Exception as exc:
                    report["capture_errors"][f"{device}-{format_}"] = str(exc)
        report["qmp_events"] = list(qmp.events)
    else:
        report["capture_errors"]["qmp"] = "Keine QMP-Verbindung verfügbar"
    write_report(destination, "failure.json", report)


def export_guest_logs(root: Path, destination: Path) -> dict:
    root = root.resolve()
    destination = destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Diagnoseziel darf nicht im Gast liegen")
    destination.mkdir(parents=True, exist_ok=True)
    report = {"copied": [], "missing": [], "errors": {}, "truncated": []}
    for relative in GUEST_FILES:
        try:
            source = inside(root, relative)
            if not source.exists():
                report["missing"].append(relative)
                continue
            if not source.is_file():
                raise ValueError("Kein reguläres Gastprotokoll")
            with source.open("rb") as stream:
                size = source.stat().st_size
                if size > MAX_LOG_BYTES:
                    stream.seek(size - MAX_LOG_BYTES)
                    report["truncated"].append(relative)
                data = stream.read(MAX_LOG_BYTES)
            (destination / source.name).write_bytes(data)
            report["copied"].append(relative)
        except Exception as exc:
            report["errors"][relative] = str(exc)
    write_report(destination, "guest-logs.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_guest_logs(args.root, args.destination)))
