"""Size-limited diagnostic logs: one fixed file per kind instead of one per run.

A log is reused once it reaches LOG_LIMIT: its content is kept as the single
"<name>.old<suffix>" copy and the file starts again from the beginning. The
file is truncated in place rather than renamed, because the live event window
may hold it open. Starting a new run keeps the previous run as the old copy.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil

LOG_LIMIT = 10 * 1024 * 1024


def previous_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.old{path.suffix}")


def retire_previous(path: Path) -> None:
    """Keep the last run's log as the old copy; a missing log is fine.

    A log still held open elsewhere stays in place and is overwritten instead.
    """
    try:
        os.replace(path, previous_path(path))
    except OSError:
        pass


class CappedTextLog(io.TextIOBase):
    """Line-buffered UTF-8 log that never grows beyond its limit."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, path: Path, limit: int = LOG_LIMIT) -> None:
        super().__init__()
        self.path = path
        self.limit = limit
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("w", encoding="utf-8", errors="replace", buffering=1)
        self._size = 0

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        size = len(text.encode("utf-8", errors="replace"))
        if self._size and self._size + size > self.limit:
            self._rollover()
        self._stream.write(text)
        self._size += size
        return len(text)

    def _rollover(self) -> None:
        self._stream.flush()
        temporary = previous_path(self.path).with_suffix(".tmp")
        try:
            shutil.copyfile(self.path, temporary)
            os.replace(temporary, previous_path(self.path))
        except OSError:
            pass  # A locked old copy must not stop logging; the limit still applies.
        self._stream.seek(0)
        self._stream.truncate()
        self._size = 0
        self.write(f"--- Log nach {self.limit // (1024 * 1024)} MB neu begonnen; "
                   f"vorheriger Inhalt in {previous_path(self.path).name} ---\n")

    def flush(self) -> None:
        if not self._stream.closed:
            self._stream.flush()

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()
        super().close()
