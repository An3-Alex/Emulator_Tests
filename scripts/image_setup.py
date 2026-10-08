"""Safe host-side plan for preparing an owner-supplied CF image copy."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from portable_launcher_model import Selection, file_sha256


COMPONENTS = {
    "audio_software": ("build/audio-software.exe", "0393fe5c8cdeb593afa330a50003b06048c11bbc0f5a527a887598e98db9e7b9"),
    "audio_installer": ("build/audio-installer.exe", "f1b73821f23db2f817b226c1a85c84398c6f6779100273d5d2c9123deeffb860"),
    "audio_verify": ("build/audio-verify.exe", "b42498a87a02073ccd9f4e2d3b047f0f655b3598a6f018cd7a63a7df0b542909"),
    "shim": ("build/Cgos.dll", "ea6843b6f7927dad09d31dfe297b57ad727320c4e81f213b27ac8408f566af57"),
    "bootstrap": ("build/display-bootstrap.exe", "fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea"),
    "qxl_installer": ("build/qxl-installer.exe", "96797f2c715a74197211a9cfc598ef9680f5bea4869e4f0fa7f1f25048142f9a"),
    "display_verify": ("build/display-verify.exe", "81e733743146b025d2f555ba476e1948be1d1515ba55465d8ba4418d4a193349"),
    "d3d9": ("build/d3d9-proxy/d3d9.dll", "aeb4bd283bdcd362b0226ced63a8f46582733ae3430293a1dc3db881f7f81793"),
    "fbwf": ("build/sram-compat/FBWFLIB.dll", "555f2a7b6e886e9476b7f83ecee89c3cfa369c823f82f5bdf4c6181c7ce41dd2"),
    "irrklang": ("build/irrklang-proxy/irrKlang.dll", "bc815b845c86d40f289d903b895ec7a082e67a37b379af01ae6207508edd6426"),
}
QXL_HASHES = {
    "qxl.inf": "2c2ce985936c87406313d68ba54b1c36f42aec97ee357d894e3238aecda776fa",
    "qxl.sys": "42be54fe601af95abeb716c3ab00656e731917fa83fda72ecc6084f39a7ccceb",
    "qxldd.dll": "4f3f81b1b8ba18282e179c6c5dad1c171b5ec54262e88d6a09f94375f7906640",
}
SWIFTSHADER_HASH = "fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9"


def check_file(path: Path, expected: str, label: str) -> str | None:
    if not path.is_file():
        return f"{label}: Datei fehlt: {path}"
    actual = file_sha256(path).lower()
    if actual != expected:
        return f"{label}: Version nicht verifiziert (SHA-256 {actual[:12]}…)"
    return None


def check_preparation(
    selection: Selection, project: Path, *, require_wsl: bool = True,
    resume: bool = False,
) -> list[str]:
    issues: list[str] = []
    source = Path(selection.original_image) if selection.original_image else None
    output = Path(selection.image) if selection.image else None
    if source is None or not source.is_file():
        issues.append("Original-CF-Image: Datei auswählen")
    if output is None:
        issues.append("Arbeitskopie: neuen Dateinamen auswählen")
    else:
        if not output.parent.is_dir():
            issues.append("Arbeitskopie: Zielordner fehlt")
        if source and source.resolve() == output.resolve():
            issues.append("Arbeitskopie: Original und Ziel müssen verschieden sein")
        elif source and source.is_file() and output.is_file() and source.samefile(output):
            issues.append("Arbeitskopie: Original und Ziel müssen verschieden sein (derselbe Dateiknoten)")
        if output.is_file() and not resume:
            issues.append("Arbeitskopie existiert bereits; Fortsetzung nur nach Statusprüfung")
        if output.with_name(output.name + ".m90-partial").exists():
            issues.append("Unvollständige Kopie vorhanden; bitte zuerst prüfen")
    if not selection.swiftshader:
        # QEMU-3dfx never loads SwiftShader; only the software path needs it.
        if selection.graphics_backend != "qemu3dfx":
            issues.append("SwiftShader-DLL: Datei auswählen (nur für den Grafikpfad SwiftShader nötig)")
    else:
        problem = check_file(Path(selection.swiftshader), SWIFTSHADER_HASH, "SwiftShader-DLL")
        if problem:
            issues.append(problem)
    if not selection.qxl_driver_dir:
        issues.append("QXL-Treiberordner auswählen")
    else:
        for name, expected in QXL_HASHES.items():
            problem = check_file(Path(selection.qxl_driver_dir) / name, expected, name)
            if problem:
                issues.append(problem)
    for name, (relative, expected) in COMPONENTS.items():
        if name.startswith("audio_"):
            continue  # Retired AC97 setup tools are not part of the starter.
        problem = check_file(project / relative, expected, name)
        if problem:
            issues.append(problem)
    if not selection.qemu_x86 or not Path(selection.qemu_x86).is_file():
        issues.append("QEMU Spiel-PC: Programm auswählen")
    if not selection.python or not Path(selection.python).is_file():
        issues.append("Python: Programm auswählen")
    if require_wsl:
        try:
            running = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-Command",
                 "Get-Process qemu-system* -ErrorAction SilentlyContinue | "
                 "Select-Object -First 1 -ExpandProperty Id"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"QEMU-Prozessprüfung fehlgeschlagen: {exc}")
        else:
            if running.stdout.strip():
                issues.append("QEMU läuft bereits; vor der Image-Vorbereitung beenden")
        if shutil.which("wsl.exe") is None:
            issues.append("WSL fehlt; Windows-Subsystem für Linux/Ubuntu installieren")
        else:
            try:
                result = subprocess.run(
                    ["wsl.exe", "--user", "root", "--exec", "bash", "-lc",
                     "command -v ntfs-3g >/dev/null && command -v losetup >/dev/null "
                     "&& command -v python3 >/dev/null && python3 -c 'import hivex'"],
                    capture_output=True, text=True, timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                issues.append(f"WSL-Prüfung fehlgeschlagen: {exc}")
            else:
                if result.returncode != 0:
                    issues.append("WSL braucht ntfs-3g und python3-hivex sowie Root-Zugriff")
    return issues


def wsl_path(path: Path) -> str:
    # WSL's default shell invocation consumes Windows backslashes. Direct exec
    # preserves argument boundaries, including spaces in AppData/image folders.
    try:
        result = subprocess.run(
            ["wsl.exe", "--user", "root", "--exec", "wslpath", "-a", "-u",
             path.resolve().as_posix()],
            capture_output=True, text=True, errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"WSL-Pfadumwandlung nicht verfügbar: {path}\n"
            "WSL/Ubuntu installieren und die Ubuntu-Ersteinrichtung abschließen.\n"
            f"Details: {exc}"
        ) from exc
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "Keine Fehlermeldung"
        raise RuntimeError(
            f"WSL konnte Pfad nicht umsetzen: {path} (Code {result.returncode})\n"
            "WSL/Ubuntu muss eingerichtet und als Standarddistribution ausgewählt sein.\n"
            f"Details: {details}"
        )
    converted = result.stdout.strip()
    if not converted.startswith("/"):
        raise RuntimeError(f"WSL konnte Pfad nicht umsetzen: {path}")
    return converted


def refresh_qxl_installer_command(image: Path, project: Path) -> list[str]:
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/install_qxl_helper_shell.sh"),
            wsl_path(image), wsl_path(project / COMPONENTS["qxl_installer"][0])]


def stage_command(selection: Selection, project: Path) -> list[str]:
    paths = [
        project / "scripts/prepare_image_stage.sh",
        Path(selection.original_image), Path(selection.image),
        *(project / COMPONENTS[name][0] for name in
          ("shim", "bootstrap", "qxl_installer", "d3d9", "fbwf", "irrklang")),
    ]
    arguments = [wsl_path(path) for path in paths]
    # "-": no SwiftShader selected (QEMU-3dfx only).
    arguments.append(wsl_path(Path(selection.swiftshader)) if selection.swiftshader else "-")
    arguments.append(wsl_path(Path(selection.qxl_driver_dir)))
    return ["wsl.exe", "--user", "root", "--exec", "bash", *arguments]


def stage_check_command(image: Path, project: Path) -> list[str]:
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/check_image_stage.sh"), wsl_path(image)]


def graphics_update_command(selection: Selection, project: Path, *, gpu: bool = False) -> list[str]:
    if not selection.original_image or not Path(selection.original_image).is_file():
        raise ValueError("Original-CF-Image auswählen, damit das Laufzeit-Update nur die Arbeitskopie ändert")
    if Path(selection.original_image).resolve() == Path(selection.image).resolve():
        raise ValueError("Original und Arbeitskopie müssen verschieden sein")
    if Path(selection.image).exists() and Path(selection.original_image).samefile(selection.image):
        raise ValueError("Original und Arbeitskopie müssen verschieden sein (derselbe Dateiknoten)")
    paths = [project / "scripts/update_runtime_graphics.sh",
             Path(selection.original_image), Path(selection.image),
             project / COMPONENTS["bootstrap"][0], project / COMPONENTS["d3d9"][0],
             project / COMPONENTS["irrklang"][0], project / COMPONENTS["fbwf"][0],
             project / COMPONENTS["shim"][0]]
    command = ["wsl.exe", "--user", "root", "--exec", "bash", *(wsl_path(path) for path in paths)]
    # GPU copies keep their QEMU-3dfx d3d9.dll; audio, SRAM and service still update.
    return command + ["--gpu"] if gpu else command


def loader_idle_setup_command(selection: Selection, project: Path) -> list[str]:
    original, image = Path(selection.original_image), Path(selection.image)
    if not selection.original_image or not original.is_file() or not image.is_file():
        raise ValueError("Original und vorbereitete Arbeitskopie für das Loader-Update auswählen")
    if original.resolve() == image.resolve() or original.samefile(image):
        raise ValueError("Original und Arbeitskopie müssen verschieden sein")
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/stage_loader_idle.sh"),
            wsl_path(original), wsl_path(image)]


def audio_setup_command(selection: Selection, project: Path) -> list[str]:
    if not selection.original_image:
        raise ValueError("Original-CF-Image für die sichere Audiovorbereitung auswählen")
    return [selection.python, str(project / "scripts/audio_setup_runner.py"),
            "--original", selection.original_image, "--image", selection.image,
            "--qemu", selection.qemu_x86, "--project", str(project)]


def audio_bridge_setup_command(selection: Selection, project: Path) -> list[str]:
    if not selection.original_image or not Path(selection.original_image).is_file():
        raise ValueError("Original-CF-Image für die sichere Audio-Umschaltung auswählen")
    original, image = Path(selection.original_image), Path(selection.image)
    if original.resolve() == image.resolve() or (image.exists() and original.samefile(image)):
        raise ValueError("Original und Arbeitskopie müssen verschieden sein")
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/stage_audio_bridge.sh"),
            wsl_path(original), wsl_path(image), "bridge"]


def finalize_command(image: Path, project: Path) -> list[str]:
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/finalize_image_stage.sh"),
            wsl_path(image), wsl_path(project / COMPONENTS["bootstrap"][0])]


def stage_display_verify_command(
    image: Path, project: Path, *, repair_ready: bool = False,
) -> list[str]:
    command = ["wsl.exe", "--user", "root", "--exec", "bash",
               wsl_path(project / "scripts/stage_display_verify.sh"),
               wsl_path(image), wsl_path(project / COMPONENTS["display_verify"][0])]
    if repair_ready:
        command.append("--repair-ready")
    return command


def retry_qxl_command(image: Path, project: Path) -> list[str]:
    return ["wsl.exe", "--user", "root", "--exec", "bash",
            wsl_path(project / "scripts/retry_qxl_install.sh"),
            wsl_path(image), wsl_path(project / COMPONENTS["qxl_installer"][0])]


def guest_setup_command(selection: Selection, project: Path, *, verify: bool = False) -> list[str]:
    command = [
        selection.python, str(project / "scripts/qxl_setup_runner.py"),
        "--qemu", selection.qemu_x86, "--image", selection.image,
        "--stderr-log", str(project / "logs/qxl-setup-qemu.stderr.log"),
    ]
    if verify:
        command.append("--verify")
    if selection.swap_displays:
        command.append("--swap-displays")
    if selection.graphics_backend == "qemu3dfx":
        command.append("--gpu-runtime")
    return command


def gpu_stage_command(selection: Selection, project: Path, *, status: bool = False) -> list[str]:
    original, image = Path(selection.original_image), Path(selection.image)
    if not selection.original_image or not original.is_file():
        raise ValueError("Original-CF-Image für die sichere GPU-Einrichtung auswählen")
    if original.resolve() == image.resolve() or (image.exists() and original.samefile(image)):
        raise ValueError("Original und GPU-Arbeitskopie müssen verschieden sein")
    bundle = Path(selection.qemu_x86).resolve().parent.parent
    command = ["wsl.exe", "--user", "root", "--exec", "bash",
               wsl_path(project / "scripts/stage_qemu3dfx.sh"),
               wsl_path(original), wsl_path(image), wsl_path(bundle), str(image.resolve())]
    if status:
        command.append("--status")
    return command
