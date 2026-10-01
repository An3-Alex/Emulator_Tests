"""Install the matching AC'97 driver before any database/game-side processes."""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from audio_diagnostics import capture_guest, write_report
from audio_driver_package import ensure
from image_setup import wsl_path
from qmp_capture import connect_with_retry
from qxl_setup_runner import qemu_command, require_free_port


def require_stopped() -> None:
    result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-Command",
        "@(Get-Process qemu-system* -ErrorAction SilentlyContinue).Count"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or result.stdout.strip() != "0":
        raise RuntimeError("Vor der Audiovorbereitung alle QEMU-Instanzen schließen")


def boot(qemu: Path, image: Path, phase: str, log: Path, timeout: int = 300,
         *, diagnostics: Path | None = None) -> None:
    require_stopped()
    for port in (4654, 4466):
        require_free_port(port)
    command = qemu_command(qemu, image, 4654, 4466, swap_displays=True)
    # Show XP so an installer dialog or boot failure is no longer invisible.
    # The fixed setup hardware remains independent of the database, inaudible.
    command += ["-audiodev", "none,id=audio0,in.voices=0", "-device", "AC97,audiodev=audio0"]
    tag = "SETUP" if phase == "install" else "VERIFY"
    success = f"M90-AUDIO-{tag}-OK\n".encode()
    failure = f"M90-AUDIO-{tag}-FAILED\n".encode()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stderr:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        connection = None
        qmp = None
        started = time.monotonic()
        clean = False
        received = bytearray()
        try:
            print(f"Audio-{phase}: XP-Einrichtung im QEMU-Fenster sichtbar; "
                  "Installationsdialoge können dort bestätigt werden.", flush=True)
            qmp = connect_with_retry("127.0.0.1", 4466, timeout=20)
            connection = socket.create_connection(("127.0.0.1", 4654), timeout=10)
            connection.settimeout(1)
            last_progress = 0
            while time.monotonic() - started < timeout:
                if process.poll() is not None:
                    raise RuntimeError(f"Audio-Gast vorzeitig beendet (Code {process.returncode})")
                elapsed = time.monotonic() - started
                if elapsed - last_progress >= 15:
                    print(f"Audio-{phase}: {elapsed:.0f} Sekunden vergangen…", flush=True)
                    last_progress = elapsed
                try:
                    data = connection.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise RuntimeError("Audio-Gast hat die Ergebnisleitung geschlossen")
                received.extend(data)
                if failure in received:
                    raise RuntimeError("XP-Audiovorbereitung fehlgeschlagen; Gastprotokoll in NVRAM/m90_audio_*.log")
                if success in received:
                    print(f"Audio-{phase}: Gast meldet Erfolg; XP wird sauber beendet…", flush=True)
                    qmp.execute("system_powerdown")
                    if process.wait(timeout=120) != 0:
                        raise RuntimeError("Audio-Gast wurde nicht sauber beendet")
                    clean = True
                    return
                del received[:-8192]
            raise TimeoutError("Audio-Gast meldet kein Ergebnis; kein Spielstart freigegeben")
        except Exception as exc:
            if diagnostics is not None:
                try:
                    capture_guest(qmp, diagnostics, phase, exc, bytes(received))
                except Exception as capture_error:
                    print(f"Audio-Bildschirmdiagnose unvollständig: {capture_error}", flush=True)
            raise
        finally:
            if connection:
                connection.close()
            if not clean and process.poll() is None:
                # Never leave an owned setup VM locking the image on errors.
                if qmp:
                    try:
                        qmp.execute("system_powerdown")
                    except Exception:
                        pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if qmp:
                        try:
                            qmp.execute("quit")
                        except Exception:
                            pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
            if qmp:
                try:
                    qmp.close()
                except Exception:
                    pass


def diagnostic_boot(original: Path, image: Path, qemu: Path, project: Path, phase: str) -> None:
    root = project / "logs/audio-diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f"{phase}-", dir=root))
    log = project / f"logs/audio-{'setup' if phase == 'install' else 'verify'}-qemu.stderr.log"
    try:
        boot(qemu, image, phase, log, diagnostics=destination)
    except Exception as exc:
        # boot's finally stops our VM before any image access. Fail closed if
        # cleanup failed or another VM appeared; never mount a live image.
        try:
            require_stopped()
            command = ["wsl.exe", "--user", "root", "--exec", "bash",
                       *(wsl_path(p) for p in (project / "scripts/export_audio_diagnostics.sh",
                                               original, image, destination))]
            result = subprocess.run(command, capture_output=True, text=True, errors="replace",
                timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                                   or f"Gastprotokoll-Export fehlgeschlagen (Code {result.returncode})")
        except Exception as export_error:
            try:
                write_report(destination, "export-error.json", {"error": str(export_error)})
            except OSError as report_error:
                print(f"Audio-Exportdiagnose konnte nicht gesichert werden: {report_error}", flush=True)
        if not (destination / "failure.json").exists():
            try:
                capture_guest(None, destination, phase, exc)
            except OSError as report_error:
                print(f"Audio-Fehlerbericht konnte nicht gesichert werden: {report_error}", flush=True)
        try:
            if log.is_file():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 4 * 1024 * 1024))
                    (destination / "qemu.stderr.log").write_bytes(stream.read(4 * 1024 * 1024))
        except OSError as log_error:
            print(f"QEMU-Protokoll konnte nicht gesichert werden: {log_error}", flush=True)
        print(f"Audio-Diagnose gespeichert: {destination}", flush=True)
        raise RuntimeError(f"{exc}\nAudio-Diagnose: {destination}") from exc
    else:
        # Successful runs need no separate report folder.
        destination.rmdir()


def prepare(original: Path, image: Path, qemu: Path, project: Path) -> None:
    if not original.is_file() or not image.is_file() or original.samefile(image):
        raise ValueError("Original und Arbeitskopie müssen verschiedene vorhandene Dateien sein")
    driver = project / "downloads/sigmatel-xp"
    paths = [project / "scripts/stage_audio_image.sh", original, image]
    parts = [project / "build/audio-installer.exe", project / "build/audio-verify.exe", driver]
    prefix = ["wsl.exe", "--user", "root", "--exec", "bash", *(wsl_path(p) for p in paths)]
    suffix = [wsl_path(p) for p in parts]
    def step(action: str) -> str:
        require_stopped()
        result = subprocess.run([*prefix, action, *suffix], capture_output=True, text=True,
            errors="replace", timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Audio-{action} fehlgeschlagen")
        return result.stdout.strip()
    state = step("check")
    if state == "ready":
        print("XP-Audio bereits eingerichtet und geprüft.", flush=True)
        return
    ensure(driver)
    if state in ("required", "staging", "install"):
        print("Passender SigmaTel-XP-Treiber wird eingerichtet…", flush=True)
        step("install")
        diagnostic_boot(original, image, qemu, project, "install")
        step("verify")
    print("XP-Audioausgang und Wiedergabepuffer werden geprüft…", flush=True)
    diagnostic_boot(original, image, qemu, project, "verify")
    step("finish")
    print("XP-Audio eingerichtet; ursprünglicher Spielstarter wiederhergestellt.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.original, args.image, args.qemu, args.project)
