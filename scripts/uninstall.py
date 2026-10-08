"""Remove the data the emulator created; never the user's own source files.

Removed: the runtime folder (scripts, GPU runtime, logs) and the settings
folder (paths, options, found database keys). The working copy and its sidecar
files only on separate request. The original CF image, database files and
installed programs (QEMU, Python, WSL tools) are never touched.
"""

from __future__ import annotations

from pathlib import Path
import shutil

APP_FOLDER = "M90 Emulator"
WORKING_COPY_SIDECARS = (".touch.json", ".qemu3dfx.json", ".m90-partial")


def emulator_folders(settings_dir: Path, runtime_dir: Path, *, frozen: bool) -> list[Path]:
    """Folders the emulator owns. Outside the EXE the runtime is the source tree."""
    folders = []
    if settings_dir.name == APP_FOLDER and settings_dir.is_dir():
        folders.append(settings_dir)
    if frozen and runtime_dir.name == "runtime" and runtime_dir.parent.name == APP_FOLDER \
            and runtime_dir.is_dir():
        folders.append(runtime_dir)
    return folders


def working_copy_files(image: str, original: str) -> list[Path]:
    """The working copy and its sidecars; never the original image."""
    if not image:
        return []
    path = Path(image)
    if not path.is_file():
        return []
    if original:
        source = Path(original)
        if path.resolve() == source.resolve() or (source.is_file() and path.samefile(source)):
            return []
    files = [path]
    files.extend(sidecar for sidecar in (path.with_name(path.name + suffix)
                                         for suffix in WORKING_COPY_SIDECARS) if sidecar.exists())
    return files


def remove(paths: list[Path]) -> list[str]:
    """Delete the given folders/files; return readable errors instead of stopping."""
    errors = []
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    for path in paths:
        parent = path.parent
        if parent.name == APP_FOLDER:
            try:
                parent.rmdir()  # only when nothing else is left in it
            except OSError:
                pass
    return errors
