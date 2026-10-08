"""Start QEMU with its stderr in a size-limited log.

QEMU reports on stderr for its whole run; a plain file redirect would grow
without bound. This relay owns the pipe and writes a capped log instead. The
status file receives QEMU's PID once it survived option parsing, or its exit
code when it ended during that time.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from capped_log import CappedTextLog, retire_previous

STARTUP_SECONDS = 1.5


def write_status(path: Path, status: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(status), encoding="utf-8")
    os.replace(temporary, path)


def pump(stream, log: CappedTextLog) -> None:
    for line in iter(stream.readline, b""):
        log.write(line.decode("utf-8", errors="replace").replace("\r\n", "\n"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--command-file", type=Path, required=True,
                        help="UTF-8 file holding the complete QEMU command line")
    args = parser.parse_args()
    command_line = args.command_file.read_text(encoding="utf-8").strip()
    retire_previous(args.log)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1  # SW_SHOWNORMAL for QEMU's display window
    with CappedTextLog(args.log) as log:
        try:
            process = subprocess.Popen(
                command_line, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            log.write(f"QEMU konnte nicht gestartet werden: {exc}\n")
            write_status(args.status, {"exit": -1})
            return 1
        reader = threading.Thread(target=pump, args=(process.stderr, log), daemon=True)
        reader.start()
        try:
            code = process.wait(STARTUP_SECONDS)
        except subprocess.TimeoutExpired:
            write_status(args.status, {"pid": process.pid})
            process.wait()
        else:
            reader.join(5)
            write_status(args.status, {"exit": code})
        reader.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
