#!/usr/bin/env python3
"""Cabinet software the setup builds on: the verified versions and notes for others.

CF cards of the same cabinet differ mostly in their games. The setup performs
the same steps for every card; a file that is not the verified version is
reported, not refused.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

GAME_HASH = "27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d"
# Path in the CF image, verified SHA-256, what it is.
ORIGINALS = (
    ("WINDOWS/system32/Cgos.dll", "480703586ea6f5bdc9ae3d8aa7bb47f03fa4d8234b48a3f2abc92356fb76a14e",
     "Board-Bibliothek"),
    ("WINDOWS/explorer.exe", "2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1", "Loader"),
    ("WINDOWS/system32/fbwflib.dll", "17dc9581c25b9c77d2d4368c3923f83e414d6be9a8acf675871ff2ce6df28263",
     "Schreibfilter-Bibliothek"),
    ("NVRAM/game.exe", GAME_HASH, "Spielprogramm"),
    ("WorkDir/game.exe", GAME_HASH, "Spielprogramm"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def note(path: Path, verified: set[str], label: str) -> str | None:
    """None for a verified file; otherwise the line shown to the operator."""
    if not path.is_file():
        raise ValueError(f"missing file: {path}")
    actual = sha256(path)
    if actual in verified:
        return None
    return f"Hinweis: {label} ({path.name}) ist eine nicht verifizierte Version ({actual[:16]}…)."


def original_notes(root: Path) -> list[str]:
    """Notes for an untouched CF image; a missing file means it is not such an image."""
    notes = []
    for relative, verified, label in ORIGINALS:
        line = note(root / relative, {verified}, label)
        if line is not None and line not in notes:
            notes.append(line)
    return notes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="mounted CF image")
    arguments = parser.parse_args()
    try:
        lines = original_notes(arguments.root)
    except ValueError as exc:
        raise SystemExit(str(exc))
    for line in lines:
        print(line)
    if lines:
        print("Hinweis: Diese CF-Karte ist nicht in der Liste der geprüften Kombinationen; "
              "sie wird wie die geprüfte eingerichtet.")
