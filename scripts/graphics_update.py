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
PROXY_HASH = "1b32c211d41909c680e7947b988abce3e80f360dec8eff4a3b65855f88f1832b"
PREVIOUS_BOOTSTRAP = "ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6"
PREVIOUS_PROXIES = {
    "aeb4bd283bdcd362b0226ced63a8f46582733ae3430293a1dc3db881f7f81793",  # before the fullscreen mode after the service
    "cc152b096bf74a01bfd23f0dece9e8f619eb8dfcc38c405faebce6cb19d20737",  # before the 10-MB guest log limit
    "31d2d484d4821ef34dd764e68a66338ed638926c66b73b14078d360713f4987f",
    "801eb42c6af73542ecb290f4844bf6ddab5a9a5daf2c3a963a66583188fd06d3",
}
SWIFTSHADER_HASH = "fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9"
GAME_HASH = "27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d"
CGOS_HASH = "ea6843b6f7927dad09d31dfe297b57ad727320c4e81f213b27ac8408f566af57"
PREVIOUS_CGOS = {"16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51"}  # before the 10-MB guest log limit
AUDIO_HASH = "bc815b845c86d40f289d903b895ec7a082e67a37b379af01ae6207508edd6426"
PREVIOUS_AUDIO = {
    "5c296f4514c09adcff07a89b6372529263dec5c0c3c13282a117f6954d0fdf89",  # before the 10-MB guest log limit
    "11db62d22889c3ac8368f464e24463b0653bb23be8d116b78cc73fc8f30c6ba7",  # PCM over the UART (before IMA ADPCM)
    "de093818dcaf76f38ecfe416551404076e5542cbdd560fd3d02f9dbb0a0df170",
    "d7834a46632c3936823e9b0052b514b50755de7b8c01f5ca6b1df8a9b7e03c9f",
    "8efc687d626c56fa995e084fa462a187446d238f871abab6c622b13a9bbbd7c8",
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
           sram: Path | None = None, cgos: Path | None = None,
           check_only: bool = False, gpu: bool = False) -> str:
    """gpu: the copy runs QEMU-3dfx. Its d3d9.dll belongs to qemu3dfx_image and
    SwiftShader is not loaded, so neither is required or touched here; audio,
    SRAM and bootstrap files are still kept current."""
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
    require_hash(inside(root, "WINDOWS/system32/Cgos.dll"), {CGOS_HASH, *PREVIOUS_CGOS})
    marker = inside(root, "NVRAM/m90_setup_stage.txt")
    if marker.exists() and marker.read_text().strip() != "stage=ready":
        raise ValueError("Image is not in ready stage")
    targets = [("WINDOWS/explorer.exe", bootstrap, BOOTSTRAP_HASH,
                {BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP})]
    if cgos is not None:
        require_hash(cgos, {CGOS_HASH})
        targets.append(("WINDOWS/system32/Cgos.dll", cgos, CGOS_HASH, {CGOS_HASH, *PREVIOUS_CGOS}))
    for directory in ("NVRAM", "WorkDir"):
        require_hash(inside(root, f"{directory}/game.exe"), {GAME_HASH})
        if not gpu:
            require_hash(inside(root, f"{directory}/swiftshader_d3d9.dll"), {SWIFTSHADER_HASH})
            targets.append((f"{directory}/d3d9.dll", proxy, PROXY_HASH,
                            {PROXY_HASH, *PREVIOUS_PROXIES}))
        if audio is not None:
            targets.append((f"{directory}/irrKlang.dll", audio, AUDIO_HASH,
                            {AUDIO_HASH, *PREVIOUS_AUDIO}))
    changed = []
    service_pending = False
    if sram is not None:
        from service_sram import update as update_service_sram
        service_pending = update_service_sram(root, sram, check_only=True) != "Service SRAM already current"
    # Validate every target before creating backups or changing anything.
    for relative, source, expected, accepted in targets:
        target = inside(root, relative)
        actual = require_hash(target, accepted)
        if actual != expected:
            changed.append((relative, source, expected, actual))
        if target.with_name(target.name + ".m90-graphics-new").exists():
            raise ValueError(f"Unfinished update file: {target}")
    if not changed:
        if service_pending:
            return f"{label} update required" if check_only else update_service_sram(root, sram)
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
    if service_pending:
        update_service_sram(root, sram)
    return f"{label} updated; backup: {backup.relative_to(root)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("bootstrap", type=Path)
    parser.add_argument("proxy", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--sram", type=Path)
    parser.add_argument("--cgos", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--gpu", action="store_true")
    options = parser.parse_args()
    print(update(options.root, options.bootstrap, options.proxy, audio=options.audio,
                 sram=options.sram, cgos=options.cgos,
                 check_only=options.check_only, gpu=options.gpu))
