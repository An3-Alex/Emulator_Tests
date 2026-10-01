"""Reversible audio setup phases on a mounted, verified working copy only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from audio_driver_package import FILES, validate
from graphics_update import BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP, CGOS_HASH, inside, require_hash, durable_copy, sha256

INSTALLER_HASH = "cf721386f3fb40ae5364c2a09ad2b2977835db70e84381739dd3e96fea8e58e4"
VERIFIER_HASH = "b42498a87a02073ccd9f4e2d3b047f0f655b3598a6f018cd7a63a7df0b542909"
MARKER = "NVRAM/m90_audio_stage.json"
BACKUP = "NVRAM/m90-audio-backup"


def installed_driver(root: Path) -> Path:
    # NTFS lookup in Linux is case-sensitive; the OEM INF copies STAC97.sys.
    parent = inside(root, "WINDOWS/system32/drivers")
    matches = [path for path in parent.iterdir() if path.name.casefold() == "stac97.sys"]
    if len(matches) != 1:
        raise ValueError("Installed SigmaTel driver missing or ambiguous")
    return inside(root, str(matches[0].relative_to(root)))


def registry(root: Path, values: dict[str, int] | None = None) -> dict[str, int]:
    """Save/restore only Start values; never reset the installed device registry."""
    import hivex
    hive_path = inside(root, "WINDOWS/system32/config/SYSTEM")
    hive = hivex.Hivex(str(hive_path), write=values is not None)
    result = {}
    for control in hive.node_children(hive.root()):
        name = hive.node_name(control)
        if not name.startswith("ControlSet"):
            continue
        for service in ("ALCXWDM", "FBWF"):
            node = control
            for part in ("Services", service):
                node = hive.node_get_child(node, part)
                if not node:
                    break
            if not node:
                continue
            key = f"{name}/Services/{service}"
            val = hive.node_get_value(node, "Start")
            kind, raw = hive.value_value(val)
            if kind != 4 or len(raw) != 4:
                raise ValueError(f"Unexpected Start value: {key}")
            result[key] = struct.unpack("<I", raw)[0]
            if values is not None:
                if key not in values:
                    raise ValueError(f"Missing saved Start value: {key}")
                hive.node_set_value(node, {"key":"Start", "t":4, "value":struct.pack("<I", values[key])})
    if not any(key.endswith("/ALCXWDM") for key in result):
        raise ValueError("Expected original Realtek service missing")
    if values is not None:
        hive.commit(None)
    return result


def write_state(root: Path, state: dict) -> None:
    marker = inside(root, MARKER)
    temporary = marker.with_suffix(".new")
    if temporary.is_symlink():
        raise ValueError("Symlink refused")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(marker)


def read_state(root: Path) -> dict | None:
    marker = inside(root, MARKER)
    if not marker.exists():
        return None
    state = json.loads(marker.read_text(encoding="utf-8"))
    if state.get("version") != 1 or state.get("stage") not in ("staging", "install", "verify", "ready"):
        raise ValueError("Unknown audio setup state")
    return state


def status(root: Path) -> str:
    require_hash(inside(root, "WINDOWS/system32/Cgos.dll"), {CGOS_HASH})
    state = read_state(root)
    if state is None:
        require_hash(inside(root, "WINDOWS/explorer.exe"), {BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP})
        return "required"
    stage = state["stage"]
    require_hash(inside(root, f"{BACKUP}/explorer.exe"), {BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP})
    if stage in ("install", "verify"):
        require_hash(inside(root, "WINDOWS/explorer.exe"),
                     {INSTALLER_HASH} if stage == "install" else {VERIFIER_HASH, state["shell_hash"]})
    if stage == "ready":
        require_hash(installed_driver(root), {FILES["stac97.sys"]})
        verify_log(root)
        starts = registry(root)
        if any(value != 4 for key, value in starts.items() if key.endswith("/ALCXWDM")):
            raise ValueError("Crashing Realtek service is active again")
        require_hash(inside(root, "WINDOWS/explorer.exe"), {BOOTSTRAP_HASH, PREVIOUS_BOOTSTRAP})
    return stage


def require_log(root: Path, name: str, expected: tuple[str, ...]) -> None:
    text = inside(root, f"NVRAM/{name}").read_text(errors="replace")
    if any(line not in text.splitlines() for line in expected):
        raise ValueError(f"Guest audio result incomplete: {name}")


def verify_log(root: Path) -> None:
    require_log(root, "m90_audio_verify.log", (
        "waveOutOpen: 0x00000000", "waveOutWrite: 0x00000000",
        "Playback completed: 0x00000001", "Audio verified",
    ))


def stage(root: Path, action: str, installer: Path, verifier: Path, driver: Path) -> str:
    root = root.resolve()
    current = status(root)
    if action == "check":
        return current
    require_hash(installer, {INSTALLER_HASH})
    require_hash(verifier, {VERIFIER_HASH})
    validate(driver)
    state = read_state(root)
    backup = inside(root, BACKUP)
    shell = inside(root, "WINDOWS/explorer.exe")
    system = inside(root, "WINDOWS/system32/config/SYSTEM")
    if action == "install":
        if current == "ready":
            return current
        if current == "verify":
            raise ValueError("Audio already installed; verify it instead")
        if state is None:
            if backup.exists():
                raise ValueError("Orphan audio backup; refusing overwrite")
            starts = registry(root)
            backup.mkdir()
            durable_copy(shell, backup / "explorer.exe")
            durable_copy(system, backup / "SYSTEM")
            software = inside(root, "WINDOWS/system32/config/SOFTWARE")
            durable_copy(software, backup / "SOFTWARE")
            state = {"version":1, "stage":"staging", "starts":starts,
                     "shell_hash":sha256(shell), "system_hash":sha256(system)}
            write_state(root, state)
        require_hash(backup / "explorer.exe", {state["shell_hash"]})
        require_hash(backup / "SYSTEM", {state["system_hash"]})
        if current == "staging":
            durable_copy(backup / "SYSTEM", system)
            durable_copy(backup / "explorer.exe", shell)
        elif current == "install":
            require_hash(shell, {INSTALLER_HASH})
        starts = registry(root)
        registry(root, {key:4 for key in starts})
        # SetupAPI uses the original XP installation source directory. Some
        # embedded images retain the live files but not their source copies.
        for name, relative in {
            "swenum.sys":"drivers/swenum.sys", "sysaudio.sys":"drivers/sysaudio.sys",
            "wdmaud.sys":"drivers/wdmaud.sys", "kmixer.sys":"drivers/kmixer.sys",
            "streamci.dll":"streamci.dll",
        }.items():
            source = inside(root, f"WINDOWS/system32/{relative}")
            if not source.is_file():
                raise ValueError(f"Original XP audio component missing: {source}")
            destination = inside(root, f"WINDOWS/i386/{name}")
            if not destination.exists():
                destination.parent.mkdir(exist_ok=True)
                durable_copy(source, destination)
        target = inside(root, "NVRAM/m90-audio-driver")
        target.mkdir(exist_ok=True)
        for name, expected in FILES.items():
            destination = inside(root, f"NVRAM/m90-audio-driver/{name}")
            durable_copy(driver / name, destination)
            require_hash(destination, {expected})
        durable_copy(installer, shell)
        state["stage"] = "install"
    elif action == "verify":
        if current != "install":
            raise ValueError("Audio installation must complete first")
        require_hash(shell, {INSTALLER_HASH})
        require_log(root, "m90_audio_install.log", (
            "DIF_REGISTERCOINSTALLERS result: 0x00000001", "DIF_INSTALLINTERFACES result: 0x00000001",
            "Matched devices: 0x00000001", "Installed devices: 0x00000001", "Installer complete.",
        ))
        require_hash(installed_driver(root), {FILES["stac97.sys"]})
        durable_copy(verifier, shell)
        state["stage"] = "verify"
    elif action == "finish":
        if current != "verify":
            raise ValueError("Audio verification must complete first")
        require_hash(shell, {VERIFIER_HASH, state["shell_hash"]})
        verify_log(root)
        require_hash(installed_driver(root), {FILES["stac97.sys"]})
        require_hash(backup / "explorer.exe", {state["shell_hash"]})
        starts = registry(root)
        restored = {key:4 if key.endswith("/ALCXWDM") else state["starts"][key] for key in starts}
        registry(root, restored)
        durable_copy(backup / "explorer.exe", shell)
        state["stage"] = "ready"
    else:
        raise ValueError(f"Unknown audio action: {action}")
    write_state(root, state)
    return state["stage"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("action", choices=("check", "install", "verify", "finish"))
    parser.add_argument("installer", type=Path)
    parser.add_argument("verifier", type=Path)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    print(stage(args.root, args.action, args.installer, args.verifier, args.driver))
