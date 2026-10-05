"""Recoverably isolate only the original ALCXWDM driver in a working copy."""
from __future__ import annotations

import json
import os
from pathlib import Path

from graphics_update import inside, durable_copy, require_hash, sha256

BACKUP = "NVRAM/m90-audio-backup/realtek"
MANIFEST = f"{BACKUP}/manifest.json"


def candidates(root: Path) -> list[Path]:
    result = []
    for directory in ("WINDOWS/system32/drivers", "WINDOWS/system32/dllcache", "WINDOWS/i386"):
        parent = inside(root, directory)
        if parent.is_dir():
            result.extend(inside(root, str(p.relative_to(root))) for p in parent.iterdir()
                          if p.name.casefold() == "alcxwdm.sys")
    parent = inside(root, "WINDOWS/INF")
    if parent.is_dir():
        for path in parent.iterdir():
            if path.suffix.casefold() != ".inf":
                continue
            path = inside(root, str(path.relative_to(root)))
            # Also recognize UTF-16 OEM INF files; don't inspect PNF binaries.
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError(f"Unexpectedly large INF: {path.name}")
            if b"alcxwdm" not in path.read_bytes().lower().replace(b"\x00", b""):
                continue
            result.append(path)
            result.extend(inside(root, str(p.relative_to(root))) for p in parent.iterdir()
                          if p.stem.casefold() == path.stem.casefold() and p.suffix.casefold() == ".pnf")
    return sorted(set(result))


def manifest(root: Path) -> list[dict]:
    data = json.loads(inside(root, MANIFEST).read_text())
    if data.get("version") != 1 or not isinstance(data.get("files"), list) or not data["files"]:
        raise ValueError("Invalid Realtek backup manifest")
    files = data["files"]
    seen = set()
    for entry in files:
        relative = entry["path"]
        parts = Path(relative).parts
        if (len(parts) < 3 or parts[0] != "WINDOWS" or ".." in parts
                or Path(relative).is_absolute() or "\\" in relative or ":" in relative):
            raise ValueError("Invalid Realtek backup path")
        parent = "/".join(parts[:-1]).casefold()
        allowed = (parent in ("windows/system32/drivers", "windows/system32/dllcache", "windows/i386")
                   and parts[-1].casefold() == "alcxwdm.sys") or (
                       parent == "windows/inf" and Path(relative).suffix.casefold() in (".inf", ".pnf"))
        if not allowed:
            raise ValueError("Unexpected Realtek backup file")
        if relative.casefold() in seen:
            raise ValueError("Duplicate Realtek backup file")
        seen.add(relative.casefold())
        saved = inside(root, f"{BACKUP}/{relative}")
        require_hash(saved, {entry["sha256"]})
        if Path(relative).suffix.casefold() == ".inf" and b"alcxwdm" not in saved.read_bytes().lower().replace(b"\x00", b""):
            raise ValueError("Backup INF is not a Realtek audio driver")
    if not any(e["path"].casefold() == "windows/system32/drivers/alcxwdm.sys" for e in files):
        raise ValueError("Realtek SYS backup missing")
    if not any(e["path"].casefold() == "windows/inf/realtekac97.inf" for e in files):
        raise ValueError("Realtek INF backup missing")
    return files


def quarantine(root: Path) -> None:
    root = root.resolve()
    record = inside(root, MANIFEST)
    if not record.exists():
        paths = candidates(root)
        relatives = {str(p.relative_to(root)).replace("\\", "/").casefold() for p in paths}
        if not {"windows/system32/drivers/alcxwdm.sys", "windows/inf/realtekac97.inf"} <= relatives:
            raise ValueError("Original Realtek files missing; refusing incomplete backup")
        entries = []
        for source in paths:
            relative = source.relative_to(root).as_posix()
            digest = sha256(source)
            destination = inside(root, f"{BACKUP}/{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                require_hash(destination, {digest})
            else:
                durable_copy(source, destination)
            require_hash(destination, {digest})
            entries.append({"path": relative, "sha256": digest})
        temporary = inside(root, f"{MANIFEST}.new")
        temporary.write_text(json.dumps({"version": 1, "files": entries}, indent=2) + "\n")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        temporary.replace(record)
    entries = manifest(root)
    # Validate every remaining source BEFORE removing any file. A changed or
    # reinstalled driver is not silently overwritten; original backups survive.
    for entry in entries:
        source = inside(root, entry["path"])
        if source.exists():
            require_hash(source, {entry["sha256"]})
    for entry in entries:
        source = inside(root, entry["path"])
        if source.exists():
            source.unlink()
    validate_quarantine(root)


def validate_quarantine(root: Path) -> None:
    entries = manifest(root)
    if any(inside(root, e["path"]).exists() for e in entries) or candidates(root):
        raise ValueError("Original Realtek driver is loadable again")
