"""Versioned, rollback-capable update of our graphics files in a mounted copy.

The caller mounts only a verified working image, never the source image. This
module also works on ordinary directories for offline validation without QEMU.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

BOOTSTRAP_HASH = "fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea"
PROXY_HASH = "cc152b096bf74a01bfd23f0dece9e8f619eb8dfcc38c405faebce6cb19d20737"
PREVIOUS_BOOTSTRAP = "ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6"
PREVIOUS_PROXIES = {
    "31d2d484d4821ef34dd764e68a66338ed638926c66b73b14078d360713f4987f",
    "801eb42c6af73542ecb290f4844bf6ddab5a9a5daf2c3a963a66583188fd06d3",
}
SWIFTSHADER_HASH = "fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9"
GAME_HASH = "27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d"
CGOS_HASH = "16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51"
AUDIO_HASH = "8efc687d626c56fa995e084fa462a187446d238f871abab6c622b13a9bbbd7c8"
PREVIOUS_AUDIO = {
    "0e811b9ddeedb53d9494e5ac3ca871743ad51a0c9ecf654cf76102a9af83b10d",
    "5bb36e7dae437afb3e68895237d18b65d50f5fad2dbebe4a2c905b8fc6006af361",
}
ORIGINAL_AUDIO_HASH = "ab0bff115cf3f55a608a7059ae3fd5fdc73a5f4ee814db4aa4b6c7e44cce8297"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside(root: Path, relative: str) -> Path:
    path = root / relative
    # Do not follow an unexpected guest symlink into another host directory.
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"Symlink refused: {current}")
        current = current.parent
    path.resolve().relative_to(root.resolve())
    return path


def require_hash(path: Path, accepted: set[str]) -> str:
    actual = sha256(path)
    if actual not in accepted:
        raise ValueError(f"Unrecognized file: {path} ({actual})")
    return actual


def durable_copy(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    # Windows _commit requires a writable descriptor; Linux fsync accepts it too.
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def update(root: Path, bootstrap: Path, proxy: Path, *, audio: Path | None = None,
           check_only: bool = False) -> str:
    root = root.resolve()
    require_hash(bootstrap, {BOOTSTRAP_HASH})
    require_hash(proxy, {PROXY_HASH})
    label = "Runtime" if audio is not None else "Graphics"
    if audio is not None:
        require_hash(audio, {AUDIO_HASH})
        require_hash(inside(root, "WINDOWS/system32/irrKlang.dll"), {ORIGINAL_AUDIO_HASH})
        # New audio setup intentionally removes the legacy driver from active
        # paths. Accept only its complete, hash-checked quarantine, not absence.
        from audio_legacy_driver import MANIFEST, validate_quarantine
        if inside(root, MANIFEST).exists():
            validate_quarantine(root)
        else:
            for relative in ("WINDOWS/INF/realtekac97.inf", "WINDOWS/system32/drivers/ALCXWDM.SYS"):
                if not inside(root, relative).is_file():
                    raise ValueError(f"AC97 driver file missing: {relative}")
    require_hash(inside(root, "WINDOWS/system32/Cgos.dll"), {CGOS_HASH})
    marker = inside(root, "NVRAM/m90_setup_stage.txt")
    if marker.exists() and marker.read_text().strip() != "stage=ready":
        raise ValueError("Image is not in ready stage")
    targets = [("WINDOWS/explorer.exe", bootstrap, BOOTSTRAP_HASH,
                {BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP})]
    for directory in ("NVRAM", "WorkDir"):
        require_hash(inside(root, f"{directory}/game.exe"), {GAME_HASH})
        require_hash(inside(root, f"{directory}/swiftshader_d3d9.dll"), {SWIFTSHADER_HASH})
        targets.append((f"{directory}/d3d9.dll", proxy, PROXY_HASH,
                        {PROXY_HASH, *PREVIOUS_PROXIES}))
        if audio is not None:
            targets.append((f"{directory}/irrKlang.dll", audio, AUDIO_HASH,
                            {AUDIO_HASH, *PREVIOUS_AUDIO}))
    changed = []
    # Validate every target before creating backups or changing anything.
    for relative, source, expected, accepted in targets:
        target = inside(root, relative)
        actual = require_hash(target, accepted)
        if actual != expected:
            changed.append((relative, source, expected, actual))
        if target.with_name(target.name + ".m90-graphics-new").exists():
            raise ValueError(f"Unfinished update file: {target}")
    if not changed:
        return f"{label} already current"
    if check_only:
        return f"{label} update required"
    backup_parent = inside(root, "NVRAM/m90-graphics-backups")
    backup = backup_parent / uuid.uuid4().hex
    backup.mkdir(parents=True, exist_ok=False)
    entries = []
    for relative, _, expected, previous in changed:
        destination = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        durable_copy(inside(root, relative), destination)
        require_hash(destination, {previous})
        entries.append(dict(path=relative, previous=previous, replacement=expected))
    manifest = backup / "manifest.json"
    manifest.write_text(json.dumps(dict(state="backed-up", files=entries), indent=2) + "\n")
    with manifest.open("r+b") as stream:
        os.fsync(stream.fileno())
    replaced = []
    try:
        for relative, source, expected, _ in changed:
            target = inside(root, relative)
            temporary = target.with_name(target.name + ".m90-graphics-new")
            durable_copy(source, temporary)
            require_hash(temporary, {expected})
            os.replace(temporary, target)
            replaced.append(relative)
        for relative, _, expected, _ in changed:
            require_hash(inside(root, relative), {expected})
    except Exception:
        # On ordinary I/O failures restore every file already replaced. A
        # power-loss backup remains recoverable through the versioned manifest.
        for relative in reversed(replaced):
            durable_copy(backup / relative, inside(root, relative))
        manifest.write_text(json.dumps(dict(state="rolled-back", files=entries), indent=2) + "\n")
        raise
    finally:
        for relative, _, _, _ in changed:
            temporary = inside(root, relative).with_name(Path(relative).name + ".m90-graphics-new")
            if temporary.exists():
                temporary.unlink()
    manifest.write_text(json.dumps(dict(state="installed", files=entries), indent=2) + "\n")
    return f"{label} updated; backup: {backup.relative_to(root)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("bootstrap", type=Path)
    parser.add_argument("proxy", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--check-only", action="store_true")
    options = parser.parse_args()
    print(update(options.root, options.bootstrap, options.proxy, audio=options.audio,
                 check_only=options.check_only))
