"""Build and validate an isolated GPU runtime; never starts a VM or host driver."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from graphics_update import inside, require_hash, sha256

REVISION = "920661f3b48bd278b93acd9cf9ff8c968afb02c9"
WINE_REVISION = "f977ef3903b444fb5cfd65c543cde1ad5e8f601c"
DRIVER_HASH = "7374ce199ee9e223bd7d823023c51da11a753a1741943377d7fde8383b24b145"
BIOS_FILES = ("bios.bin", "bios-256k.bin", "vgabios-qxl.bin", "efi-eepro100.rom",
              "kvmvapic.bin", "linuxboot.bin", "linuxboot_dma.bin",
              "multiboot.bin", "multiboot_dma.bin")
GUEST_FILES = ("d3d9.dll", "wined3d_d3d9.dll", "wined3d.dll", "opengl32.dll", "fxptl.sys")


def pe_imports(objdump: Path, path: Path, *, guest: bool = False) -> list[str]:
    result = subprocess.run([str(objdump), "-p", str(path)], check=True,
                            capture_output=True, text=True, timeout=30).stdout
    expected = "pei-i386" if guest else "pei-x86-64"
    if f"file format {expected}" not in result:
        raise ValueError(f"Wrong PE architecture: {path}")
    names = re.findall(r"DLL Name:\s*([^\s]+)", result)
    for name in names:
        if not re.fullmatch(r"[a-zA-Z0-9_.+-]+\.dll", name, re.IGNORECASE) and not (
                guest and path.suffix.lower() == ".sys" and name.lower() == "ntoskrnl.exe"):
            raise ValueError(f"Unsafe PE import name: {name}")
    return names


def collect_dependencies(executable: Path, runtime: Path, objdump: Path,
                         system: Path) -> list[Path]:
    candidates = {p.name.lower(): p for p in runtime.glob("*.dll")}
    pending = [executable]
    found: dict[str, Path] = {}
    while pending:
        for name in pe_imports(objdump, pending.pop()):
            key = name.lower()
            if key in found:
                continue
            if key in candidates:
                found[key] = candidates[key]
                pending.append(candidates[key])
            elif key.startswith(("api-ms-win-", "ext-ms-win-")) or (system / name).is_file():
                continue  # Windows loader supplies these; never copy host OS DLLs.
            else:
                raise ValueError(f"Missing host dependency: {name}")
    return list(found.values())


def build(checkout: Path, runtime: Path, wine: Path, proxy: Path, destination: Path) -> None:
    for root, revision in ((checkout, REVISION), (wine, WINE_REVISION)):
        actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        if actual != revision:
            raise ValueError(f"Unexpected source revision: {root}")
    objdump = runtime / "objdump.exe"
    executable = checkout / "build-host/qemu-system-x86_64.exe"
    dependencies = collect_dependencies(executable, runtime, objdump,
                                         Path(os.environ["SystemRoot"]) / "System32")
    guest = {
        "d3d9.dll": proxy,
        "wined3d_d3d9.dll": wine / "output/1.8.7/d3d9.dll",
        "wined3d.dll": wine / "output/1.8.7/wined3d.dll",
        "opengl32.dll": checkout / "wrappers/mesa/build/opengl32.dll",
        "fxptl.sys": checkout / "wrappers/3dfx/build/fxptl.sys",
    }
    require_hash(guest["fxptl.sys"], {DRIVER_HASH})
    for source in guest.values():
        pe_imports(objdump, source, guest=True)
    if destination.exists():
        raise ValueError("Destination already exists; refusing to overwrite a runtime")
    files = {"host/qemu-system-x86_64.exe": executable}
    files.update({f"host/{path.name}": path for path in dependencies})
    files.update({f"host/pc-bios/{name}": checkout / f"qemu-9.2.2/pc-bios/{name}"
                  for name in BIOS_FILES})
    files.update({f"guest/{name}": path for name, path in guest.items()})
    files["licenses/QEMU-COPYING"] = checkout / "qemu-9.2.2/COPYING"
    files["licenses/Wine-COPYING.LIB"] = wine / "wine-1.8.7/COPYING.LIB"
    # Include the installed dependency license texts in the local distribution.
    license_root = runtime.parent / "share/licenses"
    for path in license_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files[f"licenses/dependencies/{path.relative_to(license_root).as_posix()}"] = path
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    destination.mkdir(parents=True)
    for name, source in files.items():
        target = inside(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = dict(version=1, backend="qemu3dfx-hybrid", qemu_revision=REVISION,
                    wine_revision=WINE_REVISION, gpu_adapter=0, cpu_adapter=1,
                    files={name: sha256(destination / name) for name in files})
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    validate(destination)


def validate(root: Path) -> dict:
    root = root.resolve()
    manifest_path = inside(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("version") != 1 or manifest.get("backend") != "qemu3dfx-hybrid"
            or manifest.get("qemu_revision") != REVISION or manifest.get("wine_revision") != WINE_REVISION
            or manifest.get("gpu_adapter") != 0 or manifest.get("cpu_adapter") != 1):
        raise ValueError("Unknown GPU runtime manifest")
    files = manifest.get("files")
    required = {"host/qemu-system-x86_64.exe", "licenses/QEMU-COPYING"}
    required.update(f"host/pc-bios/{name}" for name in BIOS_FILES)
    required.update(f"guest/{name}" for name in GUEST_FILES)
    if not isinstance(files, dict) or not required.issubset(files):
        raise ValueError("Incomplete GPU runtime")
    for name, digest in files.items():
        if (not isinstance(name, str) or "\\" in name or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise ValueError("Invalid runtime file entry")
        require_hash(inside(root, name), {digest})
    if files["guest/fxptl.sys"] != DRIVER_HASH:
        raise ValueError("Unexpected GPU guest driver")
    return manifest


def verify_launch(image: Path, qemu: Path) -> None:
    root = qemu.resolve().parent.parent
    validate(root)
    expected = root / "host/qemu-system-x86_64.exe"
    if qemu.resolve() != expected:
        raise ValueError("Selected QEMU is not the packaged GPU host")
    receipt = json.loads(Path(str(image) + ".qemu3dfx.json").read_text())
    if (receipt.get("version") != 1 or receipt.get("backend") != "qemu3dfx-hybrid"
            or receipt.get("bundle_sha256") != sha256(root / "manifest.json")
            or receipt.get("image") != str(image.resolve())):
        raise ValueError("No matching offline GPU preparation receipt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("build")
    for name in ("checkout", "runtime", "wine", "proxy", "destination"):
        pack.add_argument("--" + name, type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("root", type=Path)
    launch = commands.add_parser("verify-launch")
    launch.add_argument("--image", type=Path, required=True)
    launch.add_argument("--qemu", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        build(args.checkout, args.runtime, args.wine, args.proxy, args.destination)
    elif args.command == "validate":
        validate(args.root)
    else:
        verify_launch(args.image, args.qemu)
    print("QEMU3DFX_PACKAGE_OK")


if __name__ == "__main__":
    main()
