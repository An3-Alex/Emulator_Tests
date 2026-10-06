"""Offline, reversible GPU staging on a prepared working copy, not a host install."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from graphics_update import (PROXY_HASH, PREVIOUS_PROXIES, GAME_HASH, SWIFTSHADER_HASH, CGOS_HASH,
                             inside, require_hash, sha256, durable_copy)
from qemu3dfx_package import validate

MARKER = "NVRAM/m90_qemu3dfx.json"
BACKUP = "NVRAM/m90-qemu3dfx-backup"
SYSTEM = "WINDOWS/system32/config/SYSTEM"
DRIVER = "WINDOWS/system32/drivers/fxptl.sys"
DRIVER_PATH = r"\SystemRoot\system32\drivers\fxptl.sys"
LEGACY_DRIVER_PATH = r"%SystemRoot%\system32\drivers\fxptl.sys"
LEGACY_OPENGL_HASHES = {"385115486db927790080371dac45204a5cf809aba00cbd72c52c647020f51c8b",
                        "67318a811a84c9742a90622ff674f13cf65dcb7d76cd98740dd5246bed0abc65",
                        "d6722aa9583ceb7384c8bc51675ed45e6f920fd04f6b7fe2d74b846fbc47559e",
                        "d4955e298ec0f0c2ec1697e1ef5f205782d3eb9f80436f5129de4b8cb8b40b51",
                        "b4d5c55e8acf33a6e48da68ad56044c42072a1ec0509b5e3a612ceecefc6bb9d",
                        "988a3aa09c09c9f0977bd03f7f46c0ab847115b4f6944e09f1d8876555e92da9",
                        "21f47fafa9896b9c8351970cc73a03910138cc7d8c21cbb480a0e03ecf2349a2"}
LEGACY_WINE_HASHES = {
    "d3d9.dll": {"50338a4b5ce53d3b7ab6e04f639b37c76edf2acf85f31f15b31c8ca2672f99bf"},
    "wined3d.dll": {"40140e6c87562a3288ea52840270a9446e9cdaae2680a049e405ea3e2fcd5220",
                    "f4a7f12b1c802db676dd1766e55ce02a0fb7afa465633d194f7fdc3c3a23ac18"},
    "wined3d_d3d9.dll": {"baa7f96adb69d1413c76a5c0d12a759db0377ed3627d4263025b5aaf9f051aac",
                         "e11ffbf6114de54d90a786fdb0e777a3f64829ac54b98bcd46ad12e98e3874da"},
}


def update_guest_dlls(root: Path, bundle: Path, state: dict, entries: list[dict], manifest: dict) -> str:
    """Replace paired verified legacy DLLs; preserve the original migration."""
    backup = inside(root, BACKUP + "/before-graphics-runtime-fix")
    marker = inside(root, MARKER)
    if backup.exists():
        # Preserve older completed updates; never reuse their recovery files.
        backup = inside(root, BACKUP + "/before-graphics-runtime-fix-" + sha256(marker)[:16])
    next_marker = marker.with_suffix(".json.new")
    if backup.exists() or next_marker.exists():
        raise ValueError("Unfinished GPU DLL update; refusing overwrite")
    for entry in entries:
        target = inside(root, entry["path"])
        if target.with_name(target.name + ".gpu-new").exists():
            raise ValueError("Unfinished GPU DLL replacement")
    backup.mkdir()
    durable_copy(marker, backup / "marker.json")
    require_hash(backup / "marker.json", {sha256(marker)})
    for entry in entries:
        target = inside(root, entry["path"])
        saved = backup / (target.parent.name + "." + target.name)
        durable_copy(target, saved)
        require_hash(saved, {entry["replacement"]})
    changed = []
    try:
        for entry in entries:
            target = inside(root, entry["path"])
            temporary = target.with_name(target.name + ".gpu-new")
            source = bundle / "guest" / target.name
            durable_copy(source, temporary)
            require_hash(temporary, {manifest["files"]["guest/" + target.name]})
            temporary.replace(target)
            changed.append(entry)
            entry["replacement"] = sha256(target)
        state["bundle_sha256"] = sha256(bundle / "manifest.json")
        state["backend"] = manifest["backend"]
        next_marker.write_text(json.dumps(state, indent=2) + "\n")
        next_marker.replace(marker)
    except Exception:
        for entry in reversed(changed):
            target = inside(root, entry["path"])
            durable_copy(backup / (target.parent.name + "." + target.name), target)
        raise
    finally:
        for entry in entries:
            target = inside(root, entry["path"])
            temporary = target.with_name(target.name + ".gpu-new")
            if temporary.exists(): temporary.unlink()
        if next_marker.exists(): next_marker.unlink()
    return "QEMU3DFX_IMAGE_UPDATED"


def register_driver(source: Path, destination: Path) -> None:
    """Create the upstream MAPMEM service only in an offline XP hive copy."""
    import hivex
    hive = hivex.Hivex(str(source), write=True)
    controls = [node for node in hive.node_children(hive.root())
                if hive.node_name(node).startswith("ControlSet")]
    if not controls:
        raise ValueError("No XP control sets")
    for control in controls:
        services = hive.node_get_child(control, "Services")
        if not services:
            raise ValueError("Missing XP Services node")
        if hive.node_get_child(services, "MAPMEM"):
            raise ValueError("MAPMEM already exists; refusing to replace another driver")
        service = hive.node_add_child(services, "MAPMEM")
        values = [{"key": name, "t": 4, "value": struct.pack("<I", value)}
                  for name, value in (("Type", 1), ("Start", 2), ("ErrorControl", 1))]
        values.extend({"key": name, "t": kind, "value": (value + "\0").encode("utf-16le")}
                      for name, kind, value in (
                          ("ImagePath", 2, DRIVER_PATH),
                          ("DisplayName", 1, "MAPMEM")))
        hive.node_set_values(service, values)
    hive.commit(str(destination))


def verify_driver(hive_path: Path, *, allow_legacy_path: bool = False) -> bool:
    """Allow normal XP registry writes, but require the actual MAPMEM settings."""
    import hivex
    hive = hivex.Hivex(str(hive_path), write=False)
    controls = [node for node in hive.node_children(hive.root())
                if hive.node_name(node).startswith("ControlSet")]
    if not controls:
        raise ValueError("No XP control sets")
    legacy = False
    for control in controls:
        services = hive.node_get_child(control, "Services")
        service = hive.node_get_child(services, "MAPMEM") if services else None
        if not service:
            raise ValueError("GPU MAPMEM service missing")
        for name, expected in (("Type", 1), ("Start", 2), ("ErrorControl", 1)):
            kind, value = hive.value_value(hive.node_get_value(service, name))
            if kind != 4 or value != struct.pack("<I", expected):
                raise ValueError(f"GPU MAPMEM {name} changed")
        kind, value = hive.value_value(hive.node_get_value(service, "ImagePath"))
        path = value.decode("utf-16le").rstrip("\0").casefold()
        if kind != 2:
            raise ValueError("GPU MAPMEM driver path changed")
        if path == DRIVER_PATH.casefold():
            continue
        if allow_legacy_path and path == LEGACY_DRIVER_PATH.casefold():
            legacy = True
        else:
            raise ValueError("GPU MAPMEM driver path changed")
    return legacy


def repair_driver_path(source: Path, destination: Path) -> None:
    """Repair only our legacy path; preserve all other live XP hive contents."""
    if not verify_driver(source, allow_legacy_path=True):
        raise ValueError("No legacy MAPMEM path to repair")
    import hivex
    hive = hivex.Hivex(str(source), write=True)
    for control in hive.node_children(hive.root()):
        if not hive.node_name(control).startswith("ControlSet"):
            continue
        services = hive.node_get_child(control, "Services")
        service = hive.node_get_child(services, "MAPMEM")
        hive.node_set_value(service, {"key": "ImagePath", "t": 2,
                            "value": (DRIVER_PATH + "\0").encode("utf-16le")})
    hive.commit(str(destination))


def install(root: Path, bundle: Path, *, check_only: bool = False) -> str:
    root, bundle = root.resolve(), bundle.resolve()
    manifest = validate(bundle)
    marker = inside(root, MARKER)
    if marker.exists():
        state = json.loads(marker.read_text())
        if state.get("version") != 1 or state.get("state") != "installed":
            raise ValueError("Unfinished or unknown GPU migration; restore its backup first")
        expected = {SYSTEM, DRIVER}
        expected.update(f"{directory}/{name}" for directory in ("NVRAM", "WorkDir")
                        for name in ("d3d9.dll", "wined3d_d3d9.dll", "wined3d.dll", "opengl32.dll"))
        if {entry["path"] for entry in state["files"]} != expected or len(state["files"]) != len(expected):
            raise ValueError("Unexpected installed GPU targets")
        updates = []
        for entry in state["files"]:
            if entry["path"] != SYSTEM:
                guest_name = "fxptl.sys" if entry["path"] == DRIVER else Path(entry["path"]).name
                if entry["replacement"] != manifest["files"][f"guest/{guest_name}"]:
                    known = LEGACY_OPENGL_HASHES if guest_name == "opengl32.dll" else LEGACY_WINE_HASHES.get(guest_name, set())
                    if entry["replacement"] in known:
                        updates.append(entry)
                    else:
                        raise ValueError("Another guest GPU bundle is already installed")
                require_hash(inside(root, entry["path"]), {entry["replacement"]})
        legacy_driver = verify_driver(inside(root, SYSTEM), allow_legacy_path=True) is True
        if updates:
            for name in {Path(entry["path"]).name for entry in updates}:
                paired = [entry for entry in updates if Path(entry["path"]).name == name]
                if len(paired) != 2 or paired[0]["replacement"] != paired[1]["replacement"]:
                    raise ValueError("Partially updated GPU DLLs; refusing overwrite")
            if legacy_driver:
                raise ValueError("Repair legacy MAPMEM path before updating GPU DLLs")
            if check_only:
                return "QEMU3DFX_IMAGE_UPDATE_REQUIRED"
            return update_guest_dlls(root, bundle, state, updates, manifest)
        if legacy_driver and not check_only:
            hive = inside(root, SYSTEM)
            saved = inside(root, BACKUP + "/SYSTEM.before-driver-path-fix")
            temporary_hive = hive.with_name(hive.name + ".gpu-driver-new")
            if saved.exists() or temporary_hive.exists():
                raise ValueError("Unfinished MAPMEM path repair; refusing overwrite")
            durable_copy(hive, saved)
            try:
                repair_driver_path(hive, temporary_hive)
                verify_driver(temporary_hive)
                temporary_hive.replace(hive)
            finally:
                if temporary_hive.exists():
                    temporary_hive.unlink()
            for entry in state["files"]:
                if entry["path"] == SYSTEM:
                    entry["replacement"] = sha256(hive)
        if not check_only and (legacy_driver or state.get("bundle_sha256") != sha256(bundle / "manifest.json")):
            state["bundle_sha256"] = sha256(bundle / "manifest.json")
            temporary = marker.with_suffix(".json.new")
            if temporary.exists():
                raise ValueError("Unfinished GPU marker update")
            temporary.write_text(json.dumps(state, indent=2) + "\n")
            temporary.replace(marker)
        return "QEMU3DFX_IMAGE_CURRENT"
    require_hash(inside(root, "WINDOWS/system32/Cgos.dll"), {CGOS_HASH})
    if inside(root, "NVRAM/m90_setup_stage.txt").read_text().strip() != "stage=ready":
        raise ValueError("The image must be completely prepared first")
    targets = []
    for directory in ("NVRAM", "WorkDir"):
        require_hash(inside(root, f"{directory}/game.exe"), {GAME_HASH})
        # SwiftShader is optional (QEMU-3dfx never loads it); a present copy
        # must still be the verified one.
        swiftshader = inside(root, f"{directory}/swiftshader_d3d9.dll")
        if swiftshader.exists():
            require_hash(swiftshader, {SWIFTSHADER_HASH})
        # The software proxy is backed up and replaced; older verified versions
        # are as good as the current one here.
        require_hash(inside(root, f"{directory}/d3d9.dll"), {PROXY_HASH, *PREVIOUS_PROXIES})
        for name in ("d3d9.dll", "wined3d_d3d9.dll", "wined3d.dll", "opengl32.dll"):
            relative = f"{directory}/{name}"
            target = inside(root, relative)
            if name != "d3d9.dll" and target.exists():
                raise ValueError(f"Third-party graphics file already exists: {relative}")
            targets.append((relative, bundle / "guest" / name,
                            manifest["files"][f"guest/{name}"]))
    if inside(root, DRIVER).exists():
        raise ValueError("Guest GPU driver already exists")
    targets.append((DRIVER, bundle / "guest/fxptl.sys", manifest["files"]["guest/fxptl.sys"]))
    hive = inside(root, SYSTEM)
    backup = inside(root, BACKUP)
    if backup.exists():
        raise ValueError("Existing migration backup; refusing overwrite")
    if check_only:
        return "QEMU3DFX_IMAGE_REQUIRED"
    backup.mkdir(parents=True)
    # Save the unmodified hive before preparing a new one. No host registry APIs.
    old_hive = backup / "SYSTEM"
    durable_copy(hive, old_hive)
    next_hive = backup / "SYSTEM.gpu"
    register_driver(old_hive, next_hive)
    targets.append((SYSTEM, next_hive, sha256(next_hive)))
    entries = []
    for relative, source, digest in targets:
        target = inside(root, relative)
        entry = dict(path=relative, previous=sha256(target) if target.exists() else None,
                     replacement=digest)
        if target.exists():
            saved = backup / "files" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            durable_copy(target, saved)
            require_hash(saved, {entry["previous"]})
        if target.with_name(target.name + ".gpu-new").exists():
            raise ValueError(f"Unfinished migration file: {relative}")
        entries.append(entry)
    state = dict(version=1, state="backed-up", backend=manifest["backend"],
                 bundle_sha256=sha256(bundle / "manifest.json"), files=entries)
    marker.write_text(json.dumps(state, indent=2) + "\n")
    replaced = []
    try:
        for (relative, source, digest), entry in zip(targets, entries):
            target = inside(root, relative)
            temporary = target.with_name(target.name + ".gpu-new")
            durable_copy(source, temporary)
            require_hash(temporary, {digest})
            temporary.replace(target)
            replaced.append(entry)
        state["state"] = "installed"
        marker.write_text(json.dumps(state, indent=2) + "\n")
    except Exception:
        for entry in reversed(replaced):
            target = inside(root, entry["path"])
            if entry["previous"] is None:
                target.unlink()
            else:
                durable_copy(backup / "files" / entry["path"], target)
        state["state"] = "rolled-back"
        marker.write_text(json.dumps(state, indent=2) + "\n")
        raise
    finally:
        for relative, _, _ in targets:
            temporary = inside(root, relative).with_name(Path(relative).name + ".gpu-new")
            if temporary.exists():
                temporary.unlink()
    return "QEMU3DFX_IMAGE_INSTALLED"


def restore(root: Path) -> str:
    root = root.resolve()
    marker = inside(root, MARKER)
    state = json.loads(marker.read_text())
    if state.get("version") != 1 or state.get("state") != "installed":
        raise ValueError("Only a complete, unchanged GPU installation can be restored automatically")
    expected = {SYSTEM, DRIVER}
    expected.update(f"{directory}/{name}" for directory in ("NVRAM", "WorkDir")
                    for name in ("d3d9.dll", "wined3d_d3d9.dll", "wined3d.dll", "opengl32.dll"))
    if {entry["path"] for entry in state["files"]} != expected or len(state["files"]) != len(expected):
        raise ValueError("Unexpected backup targets")
    for entry in state["files"]:
        require_hash(inside(root, entry["path"]), {entry["replacement"]})
        if entry["previous"] is not None:
            require_hash(inside(root, BACKUP + "/files/" + entry["path"]), {entry["previous"]})
    # Abort if the guest has since changed its SYSTEM hive. Never undo unrelated
    # device setup by blindly restoring an old complete registry.
    for entry in state["files"]:
        target = inside(root, entry["path"])
        if entry["previous"] is None:
            target.unlink()
        else:
            durable_copy(inside(root, BACKUP + "/files/" + entry["path"]), target)
    state["state"] = "restored"
    marker.write_text(json.dumps(state, indent=2) + "\n")
    return "QEMU3DFX_IMAGE_RESTORED"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        if args.restore or args.check_only or args.bundle is None:
            parser.error("--status requires a bundle and no other action")
        validate(args.bundle)
        print(install(args.root, args.bundle, check_only=True)
              if inside(args.root.resolve(), MARKER).exists() else "QEMU3DFX_IMAGE_REQUIRED")
    elif args.restore:
        if args.check_only: parser.error("--restore and --check-only cannot be combined")
        print(restore(args.root))
    else:
        if args.bundle is None: parser.error("bundle required")
        print(install(args.root, args.bundle, check_only=args.check_only))
