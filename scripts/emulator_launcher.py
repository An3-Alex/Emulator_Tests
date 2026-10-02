"""Windows front end for owner-supplied M90 emulator inputs.

Fresh CF images are prepared in a separate working copy. Protected game and
database files remain outside the bundled application.
"""

from __future__ import annotations

from dataclasses import asdict
import os
import json
import ctypes
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
from emulator_processes import EmulatorProcesses
from image_setup import refresh_qxl_installer_command
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from image_setup import (
    COMPONENTS, check_file, check_preparation, finalize_command, guest_setup_command,
    graphics_update_command, audio_setup_command, audio_bridge_setup_command,
    retry_qxl_command, stage_display_verify_command,
    stage_check_command, stage_command,
)
from portable_launcher_model import (
    EMULATION_FIELDS, Selection, check_runtime, launch_command, validate_emulation,
)
from runtime_bundle import (
    OWN_BINARIES, REQUIRED_PYTHON_FILES, SHELL_FILES, project_for_launcher,
)


PROJECT = project_for_launcher()
SETTINGS = Path(os.environ.get("APPDATA", str(Path.home()))) / "M90 Emulator" / "settings.json"
DATABASE_FILES = {
    "database": "Magie_90_CC4.bin",
    "loader": "Loader_61640403_L5.0b_2MB.bin",
    "factory": "FactoryReset_61640403.xc",
    "config": "M90_Las_Vegas.bin",
}
FIELDS = (
    ("original_image", "Frisches Original-CF-Image"),
    ("image", "Neue Arbeitskopie / Start-Image"),
    ("swiftshader", "SwiftShader-DLL (eigene Datei)"),
    ("qxl_driver_dir", "QXL-Treiberordner"),
    ("database", "Datenbank (M90)"),
    ("loader", "Loader"),
    ("factory", "Factory"),
    ("config", "Konfiguration"),
    ("admission_eeprom", "Zulassungskarte (EEPROM)"),
    ("qemu_x86", "QEMU Spiel-PC"),
    ("qemu_m68k", "QEMU Datenbank"),
    ("python", "Python"),
)


def format_elapsed(seconds: float) -> str:
    """Display a monotonic elapsed duration without implying a finish ETA."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def suggested_selection(saved: Selection) -> Selection:
    """Fill only missing executable paths; never guess owner data files."""
    values = asdict(saved)
    qemu_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "qemu"
    for key, executable in (
        ("qemu_x86", "qemu-system-x86_64.exe"),
        ("qemu_m68k", "qemu-system-m68k.exe"),
    ):
        if not values[key]:
            candidate = qemu_dir / executable
            values[key] = str(candidate if candidate.is_file() else shutil.which(executable) or "")
    if not values["python"]:
        values["python"] = find_python_executable()
    return Selection(**values)


def find_python_executable() -> str:
    candidates = []
    if Path(sys.executable).name.lower() == "python.exe":
        candidates.append(sys.executable)
    candidates.append(shutil.which("python.exe"))
    candidates.append(str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python314" / "python.exe"))
    py_launcher = shutil.which("py.exe")
    if py_launcher:
        try:
            result = subprocess.run(
                [py_launcher, "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                candidates.insert(0, result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    for candidate in dict.fromkeys(path for path in candidates if path):
        path = Path(candidate)
        if "windowsapps" in str(path).casefold():
            continue
        if not path.is_file() or not path.with_name("pythonw.exe").is_file():
            continue
        try:
            result = subprocess.run(
                [str(path), "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return str(path)
    return ""


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("M90 Emulator – Einrichtung und Start")
        self.geometry("1000x800")
        self.minsize(850, 680)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running: subprocess.Popen[str] | None = None
        self.process_group: EmulatorProcesses | None = None
        self.stopping = False
        self.close_after_stop = False
        self.checking = False
        self.preparing = False
        self.prepare_started_at: float | None = None
        self.prepare_phase = ""

        try:
            saved = Selection.from_json(SETTINGS)
        except (OSError, ValueError, TypeError) as exc:
            saved = Selection()
            self.after(50, lambda error=str(exc): messagebox.showwarning(
                "Einstellungen", f"Gespeicherte Auswahl konnte nicht gelesen werden:\n{error}"
            ))
        selection = suggested_selection(saved)
        self.variables = {
            key: tk.StringVar(value=getattr(selection, key)) for key, _ in FIELDS
        }
        self.show_log = tk.BooleanVar(value=selection.show_live_log)
        self.swap_displays = tk.BooleanVar(value=selection.swap_displays)
        self.emulation_variables = {
            key: (self.show_log if key == "show_live_log" else
                  self.swap_displays if key == "swap_displays" else
                  tk.BooleanVar(value=getattr(selection, key)) if kind is bool else
                  tk.StringVar(value=str(getattr(selection, key))))
            for key, _group, _label, kind, _limits, _help in EMULATION_FIELDS
        }
        self.prepared_copy = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Dateien auswählen und prüfen.")
        self.prepare_timer = tk.StringVar(value="Image-Einrichtung: noch nicht gestartet")
        self._build()
        self.after(100, self._drain_events)
        self.after(1000, self._tick_prepare_timer)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="M90 Emulator", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=(6, 0))
        setup_tab = ttk.Frame(notebook, padding=8)
        settings_tab = ttk.Frame(notebook, padding=8)
        notebook.add(setup_tab, text="Einrichtung und Start")
        notebook.add(settings_tab, text="Emulationseinstellungen")
        self._build_emulation_settings(settings_tab)
        # Keep the start actions and output visible below both tabs.
        content = setup_tab
        guide = ttk.LabelFrame(content, text="Kurzanleitung", padding=(10, 6))
        guide.pack(fill="x", pady=(4, 8))
        ttk.Label(
            guide,
            text="1. QEMU, Python und WSL/NTFS-Werkzeuge prüfen oder installieren.\n"
                 "2. Original-CF-Image, neuen Dateinamen für die Arbeitskopie, M90-Dateien, "
                 "Zulassungskarten-EEPROM, SwiftShader und QXL auswählen.\n"
                 "3. „Frisches Image einrichten“ abwarten; das Original bleibt unverändert.\n"
                 "4. „Dateien und Programme prüfen“, dann „Emulator starten“. "
                 "Das Live-Protokoll ist optional.",
            wraplength=870, justify="left",
        ).pack(anchor="w")
        ttk.Checkbutton(
            content,
            text="Ich habe eine bereits vorbereitete Arbeitskopie gewählt (nicht das Original).",
            variable=self.prepared_copy,
        ).pack(anchor="w", pady=(0, 8))

        picker = ttk.Frame(content)
        picker.pack(fill="both", expand=True, pady=(0, 5))
        picker_canvas = tk.Canvas(picker, highlightthickness=0, borderwidth=0)
        picker_scroll = ttk.Scrollbar(picker, orient="vertical", command=picker_canvas.yview)
        picker_canvas.configure(yscrollcommand=picker_scroll.set)
        picker_scroll.pack(side="right", fill="y")
        picker_canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(picker_canvas)
        form_window = picker_canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: picker_canvas.configure(
            scrollregion=picker_canvas.bbox("all")))
        picker_canvas.bind("<Configure>", lambda event: picker_canvas.itemconfigure(
            form_window, width=event.width))
        ttk.Button(
            form, text="Datenbank-Dateien aus einem Ordner übernehmen…",
            command=self._choose_database_directory,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 5))
        for row, (key, label) in enumerate(FIELDS):
            ttk.Label(form, text=label, width=28).grid(row=row + 1, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.variables[key]).grid(
                row=row + 1, column=1, sticky="ew", padx=(4, 6), pady=3
            )
            ttk.Button(form, text="Auswählen…", command=lambda selected=key: self._browse(selected)).grid(
                row=row + 1, column=2, pady=3
            )
        form.columnconfigure(1, weight=1)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(12, 6))
        self.prepare_button = ttk.Button(
            controls, text="Frisches Image einrichten", command=self._prepare,
        )
        self.prepare_button.pack(side="left")
        self.check_button = ttk.Button(controls, text="Dateien und Programme prüfen", command=self._check)
        self.check_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(controls, text="Emulator starten", command=self._start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="Alles beenden", command=self._stop_all, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        install_controls = ttk.Frame(outer)
        install_controls.pack(fill="x", pady=(0, 4))
        self.install_button = ttk.Button(install_controls, text="QEMU installieren", command=self._install_qemu)
        self.install_button.pack(side="left")
        self.python_button = ttk.Button(install_controls, text="Python installieren", command=self._install_python)
        self.python_button.pack(side="left", padx=(8, 0))
        ttk.Label(outer, textvariable=self.status, wraplength=870).pack(anchor="w", pady=(2, 0))
        ttk.Label(outer, textvariable=self.prepare_timer).pack(anchor="w", pady=(3, 0))
        self.prepare_progress = ttk.Progressbar(outer, mode="indeterminate")
        self.prepare_progress.pack(fill="x", pady=(3, 5))
        ttk.Button(
            outer, text="WSL/NTFS-Werkzeuge für frische Images einrichten",
            command=self._install_wsl,
        ).pack(anchor="w", pady=(0, 5))

        ttk.Label(outer, text="Startmeldungen", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        output_frame = ttk.Frame(outer)
        output_frame.pack(fill="x")
        self.output = tk.Text(output_frame, height=5, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_emulation_settings(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Änderungen gelten beim nächsten Emulatorstart, nicht für die Image-Einrichtung.",
                  wraplength=840).pack(anchor="w", pady=(0, 4))
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Einstellungen speichern", command=self._save_options).pack(side="left")
        ttk.Button(actions, text="Emulations-Standardwerte", command=self._reset_options).pack(side="left", padx=8)
        canvas = tk.Canvas(parent, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        groups = {}
        rows = {}
        for key, group, label, kind, limits, help_text in EMULATION_FIELDS:
            if group not in groups:
                frame = ttk.LabelFrame(form, text=group, padding=10)
                frame.pack(fill="x", pady=(0, 8))
                frame.columnconfigure(1, weight=1)
                groups[group] = frame
                rows[group] = 0
            frame, row = groups[group], rows[group]
            variable = self.emulation_variables[key]
            if kind is bool:
                ttk.Checkbutton(frame, text=label, variable=variable).grid(row=row, column=0, columnspan=2, sticky="w")
            else:
                ttk.Label(frame, text=label, width=38).grid(row=row, column=0, sticky="w")
                if key in ("acceleration", "qxl_vram_mib", "db_icount_shift", "guest_vcpus"):
                    widget = ttk.Combobox(frame, textvariable=variable, values=limits, state="readonly", width=23)
                else:
                    widget = ttk.Entry(frame, textvariable=variable, width=25)
                widget.grid(row=row, column=1, sticky="w", padx=6)
            ttk.Label(frame, text=help_text, wraplength=760, foreground="#555555").grid(
                row=row + 1, column=0, columnspan=2, sticky="w", pady=(2, 9))
            rows[group] += 2
        ttk.Label(form, wraplength=790, text=(
            "Fest verdrahtet: MC68331/CPU32, Firmware-SRAM 2 MiB, Board-Adressen und lokale Schnittstellen "
            "(COM3 4553, Bedienung 4554, QMP 4444, GDB 1235). Die Zulassungskarte ist kein eigener CPU-Prozess. "
            "Sound verwendet AC97/WinMM; der Münzprüfer hat keine separat konfigurierbare Emulation. "
            "Es gibt kein Windows-CPU-Prozentlimit. Ungeeignete Werte können Bootfehler oder Abstürze verursachen."
        )).pack(anchor="w", pady=8)

    def _save_options(self) -> None:
        selection = self._read_selection()
        if selection is not None and self._save(selection):
            self.status.set("Einstellungen gespeichert; wirksam beim nächsten Start.")

    def _reset_options(self) -> None:
        if not messagebox.askyesno("Standardwerte", "Nur die Emulationsoptionen zurücksetzen? Dateipfade bleiben erhalten. Anschließend speichern."):
            return
        defaults = Selection()
        for key, _group, _label, _kind, _limits, _help in EMULATION_FIELDS:
            self.emulation_variables[key].set(getattr(defaults, key))
        self.status.set("Standardwerte eingesetzt; noch nicht gespeichert.")

    def _browse(self, key: str) -> None:
        current = self.variables[key].get()
        if key == "image":
            selected = filedialog.asksaveasfilename(
                title="Name der neuen Arbeitskopie", defaultextension=".img",
                filetypes=[("CF-Image", "*.img"), ("Alle Dateien", "*.*")],
                initialdir=str(Path(current).parent) if current else None,
            )
        elif key == "qxl_driver_dir":
            selected = filedialog.askdirectory(title=dict(FIELDS)[key])
        else:
            selected = filedialog.askopenfilename(
                title=dict(FIELDS)[key], initialdir=str(Path(current).parent) if current else None,
            )
        if selected:
            self.variables[key].set(selected)

    def _choose_database_directory(self) -> None:
        directory = filedialog.askdirectory(title="Ordner mit M90-Dateien auswählen")
        if not directory:
            return
        available = {path.name.casefold(): path for path in Path(directory).iterdir() if path.is_file()}
        found = 0
        for key, filename in DATABASE_FILES.items():
            path = available.get(filename.casefold())
            if path:
                self.variables[key].set(str(path))
                found += 1
        self._write(f"Datenbank-Ordner: {found} von {len(DATABASE_FILES)} erwarteten Dateien gefunden.\n")

    def _selection(self) -> Selection:
        options = {}
        for key, _group, label, kind, _limits, _help in EMULATION_FIELDS:
            raw = self.emulation_variables[key].get()
            try:
                options[key] = kind(raw.strip()) if kind is not bool else raw
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{label}: gültigen Zahlenwert eingeben") from exc
        selection = Selection(**{key: value.get().strip() for key, value in self.variables.items()}, **options)
        issues = validate_emulation(selection)
        if issues:
            raise ValueError("\n".join(issues))
        return selection

    def _read_selection(self) -> Selection | None:
        try:
            return self._selection()
        except (ValueError, TypeError, tk.TclError) as exc:
            messagebox.showerror("Emulationseinstellungen", str(exc))
            return None

    def _save(self, selection: Selection) -> bool:
        try:
            selection.save(SETTINGS)
            return True
        except (OSError, ValueError) as exc:
            messagebox.showerror("Einstellungen", f"Auswahl konnte nicht gespeichert werden:\n{exc}")
            return False

    def _check(self) -> None:
        if self.checking:
            return
        selection = self._read_selection()
        if selection is None:
            return
        if not self._save(selection):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.status.set("Prüfung läuft…")
        threading.Thread(target=self._check_worker, args=(selection, False), daemon=True).start()

    def _start(self) -> None:
        if self.stopping or self.preparing or self.checking or (self.running and self.running.poll() is None):
            return
        if not self.prepared_copy.get():
            messagebox.showerror(
                "Image-Kopie erforderlich",
                "Ein unverändertes oder einziges Original-Image darf hier noch nicht gestartet "
                "werden. Bitte eine vorbereitete Kopie auswählen und den Hinweis bestätigen.",
            )
            return
        selection = self._read_selection()
        if selection is None:
            return
        if not self._save(selection):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Vorprüfung vor dem Start…")
        threading.Thread(target=self._check_worker, args=(selection, True), daemon=True).start()

    def _prepare(self) -> None:
        if self.checking or self.preparing or (self.running and self.running.poll() is None):
            return
        selection = self._read_selection()
        if selection is None:
            return
        if not self._save(selection):
            return
        if not messagebox.askyesno(
            "Frisches Image einrichten",
            "Eine neue, etwa 16 GB große Arbeitskopie wird angelegt. "
            "Danach startet ein temporärer Windows-Gast und installiert den von dir "
            "ausgewählten, unsignierten QXL-Treiber. "
            "Das gewählte Original bleibt schreibgeschützt. Fortfahren?",
        ):
            return
        self.checking = True
        self.preparing = True
        self.prepare_started_at = time.monotonic()
        self.prepare_phase = "Vorprüfung"
        self.prepare_timer.set("Image-Einrichtung · Vorprüfung · 00:00:00 vergangen")
        self.prepare_progress.start(100)
        self.prepare_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.status.set("Image-Vorbereitung wird geprüft…")
        threading.Thread(target=self._prepare_worker, args=(selection,), daemon=True).start()

    def _run_step(
        self, command: list[str], label: str, *, allowed_exit_codes: tuple[int, ...] = (),
    ) -> int:
        self.events.put(("status", label))
        process = subprocess.Popen(
            command, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdout is not None
        output_tail: list[str] = []
        for line in process.stdout:
            self.events.put(("line", line))
            output_tail.append(line.strip())
            output_tail = output_tail[-6:]
        result = process.wait()
        if result != 0 and result not in allowed_exit_codes:
            details = "\n".join(line for line in output_tail if line)
            suffix = f"\n{details}" if details else ""
            raise RuntimeError(f"{label} fehlgeschlagen (Code {result}){suffix}")
        return result

    def _prepare_worker(self, selection: Selection) -> None:
        try:
            self.events.put(("prepare_phase", "Bestehende Arbeitskopie prüfen"))
            image = Path(selection.image) if selection.image else None
            stage = "new"
            if image and image.is_file():
                result = subprocess.run(
                    stage_check_command(image, PROJECT), capture_output=True, text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"Arbeitskopie konnte nicht geprüft werden: {result.stderr.strip()}")
                stage = result.stdout.strip()
                if stage == "ready":
                    self._update_graphics(selection)
                    self.events.put(("prepared", str(image)))
                    return
                if stage not in ("qxl-pnp", "qxl-verify", "ready-unverified"):
                    raise RuntimeError("Bestehendes Image ist keine fortsetzbare M90-Arbeitskopie")
            issues = check_preparation(selection, PROJECT, resume=stage != "new")
            if issues:
                raise RuntimeError("\n".join(issues))
            if stage == "new":
                self._run_step(stage_command(selection, PROJECT), "Arbeitskopie wird eingerichtet")
            if stage in ("new", "qxl-pnp"):
                if stage == "qxl-pnp":
                    self._run_step(refresh_qxl_installer_command(Path(selection.image), PROJECT),
                                   "QXL-Einrichtungshelfer wird aktualisiert")
                self._run_step(guest_setup_command(selection, PROJECT), "QXL-Gastinstallation läuft")
                self._run_step(stage_display_verify_command(Path(selection.image), PROJECT),
                               "QXL-Anzeigeprüfung wird vorbereitet")
            elif stage == "ready-unverified":
                self._run_step(stage_display_verify_command(Path(selection.image), PROJECT,
                                                            repair_ready=True),
                               "Bisherige QXL-Einrichtung wird geprüft")
            verify_result = self._run_step(
                guest_setup_command(selection, PROJECT, verify=True),
                "QXL-Anzeigen werden im Gast geprüft", allowed_exit_codes=(17,),
            )
            if verify_result == 17:
                self._run_step(retry_qxl_command(Path(selection.image), PROJECT),
                               "QXL-Treiber wird erneut vorbereitet")
                self._run_step(guest_setup_command(selection, PROJECT),
                               "QXL-Treiber wird erneut installiert")
                self._run_step(stage_display_verify_command(Path(selection.image), PROJECT),
                               "QXL-Anzeigeprüfung wird wiederholt")
                self._run_step(guest_setup_command(selection, PROJECT, verify=True),
                               "QXL-Anzeigen werden erneut geprüft")
            self._run_step(finalize_command(Path(selection.image), PROJECT), "Image wird abgeschlossen")
            self.events.put(("prepare_phase", "Abschluss wird geprüft"))
            result = subprocess.run(
                stage_check_command(Path(selection.image), PROJECT),
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 or result.stdout.strip() != "ready":
                raise RuntimeError("Abschlussmarker im Image fehlt")
            self._update_graphics(selection)
            self.events.put(("prepared", selection.image))
        except Exception as exc:
            self.events.put(("prepare_failed", str(exc)))

    def _check_worker(self, selection: Selection, start: bool) -> None:
        try:
            issues = check_runtime(selection)
            if not issues:
                result = subprocess.run(
                    stage_check_command(Path(selection.image), PROJECT),
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    issues.append("CF-Image: Vorbereitungsstatus nicht lesbar; WSL/NTFS prüfen")
                elif result.stdout.strip() not in ("ready", "legacy-ready"):
                    issues.append(
                        f"CF-Image: nicht startbereit ({result.stdout.strip() or 'unbekannt'}); "
                        "zuerst ‚Frisches Image einrichten‘ ausführen"
                    )
                if start and not issues:
                    self._update_graphics(selection)
        except Exception as exc:
            issues = [f"Prüfung fehlgeschlagen: {exc}"]
        self.events.put(("checked", (selection, start, issues)))

    def _update_graphics(self, selection: Selection) -> None:
        # Refuse to mount a working image while any QEMU instance may own it.
        probe = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command",
             "@(Get-Process qemu-system* -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if probe.returncode != 0 or probe.stdout.strip() != "0":
            raise RuntimeError("Vor dem Laufzeit-Update alle QEMU-Instanzen schließen")
        if selection.audio_output == "ac97":
            self._run_step(audio_setup_command(selection, PROJECT),
                           "XP-Audiotreiber und Wiedergabeausgang werden eingerichtet")
        self._run_step(graphics_update_command(selection, PROJECT),
                       "Grafik- und Audiodateien der Arbeitskopie werden aktualisiert")
        self._run_step(audio_bridge_setup_command(selection, PROJECT),
                       "Audio-Ausgabe der Arbeitskopie wird eingestellt")

    def _install_qemu(self) -> None:
        self._install_package("qemu", "QEMU", "SoftwareFreedomConservancy.QEMU")

    def _install_python(self) -> None:
        self._install_package("python", "Python 3.14", "Python.Python.3.14")

    def _install_wsl(self) -> None:
        if self.checking or self.preparing or self.running and self.running.poll() is None:
            return
        wsl = shutil.which("wsl.exe")
        if not wsl:
            messagebox.showerror("WSL", "wsl.exe fehlt. Windows 10/11 mit WSL-Unterstützung wird benötigt.")
            return
        try:
            probe = subprocess.run(
                [wsl, "--exec", "sh", "-lc", "echo WSL_READY"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            probe = None
        if probe is None or "WSL_READY" not in probe.stdout:
            if not messagebox.askyesno(
                "WSL installieren",
                "Für die automatische CF-Vorbereitung wird WSL/Ubuntu benötigt. "
                "Ubuntu mit Windows-Administratorfreigabe installieren? "
                "Danach können ein Neustart und eine einmalige Ubuntu-Ersteinrichtung nötig sein.",
            ):
                return
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", wsl, "--install -d Ubuntu", None, 1,
            )
            if result <= 32:
                messagebox.showerror("WSL", f"WSL-Installation konnte nicht gestartet werden (Code {result}).")
            else:
                self._write("WSL/Ubuntu-Installation gestartet. Nach Abschluss ggf. Windows neu starten.\n")
            return
        if not messagebox.askyesno(
            "WSL-Werkzeuge installieren",
            "Die Linux-Pakete ntfs-3g und python3-hivex jetzt in deiner WSL-Distribution "
            "installieren? Dafür werden Paketquellen aus dem Internet verwendet.",
        ):
            return
        self.checking = True
        self.status.set("WSL-Werkzeuge werden installiert…")
        self.check_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.prepare_button.configure(state="disabled")
        threading.Thread(target=self._install_wsl_worker, args=(wsl,), daemon=True).start()

    def _install_wsl_worker(self, wsl: str) -> None:
        try:
            self._run_step(
                [wsl, "--user", "root", "--exec", "bash", "-lc",
                 "command -v apt-get >/dev/null && apt-get update && "
                 "DEBIAN_FRONTEND=noninteractive apt-get install -y ntfs-3g python3-hivex"],
                "WSL-Werkzeuge werden installiert",
            )
        except Exception as exc:
            self.events.put(("wsl_installed", str(exc)))
        else:
            self.events.put(("wsl_installed", ""))

    def _install_package(self, key: str, label: str, package_id: str) -> None:
        if self.checking or self.running and self.running.poll() is None:
            return
        winget = shutil.which("winget.exe")
        if not winget:
            messagebox.showerror(
                f"{label} installieren", "Windows-Paketmanager winget wurde nicht gefunden. "
                f"Bitte {label} manuell installieren und danach die EXE-Datei auswählen."
            )
            return
        if not messagebox.askyesno(
            f"{label} installieren",
            f"{label} über den Windows-Paketmanager installieren? Die Installation kann "
            "eine Windows-Freigabe erfordern. Es werden keine Image-/Datenbankdateien verändert.",
        ):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.install_button.configure(state="disabled")
        self.python_button.configure(state="disabled")
        self.status.set(f"{label}-Installation läuft…")
        threading.Thread(
            target=self._install_package_worker,
            args=(winget, key, label, package_id), daemon=True,
        ).start()

    def _install_package_worker(self, winget: str, key: str, label: str, package_id: str) -> None:
        command = [
            winget, "install", "--exact", "--id", package_id,
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert process.stdout is not None
            for line in process.stdout:
                self.events.put(("line", line))
            result = process.wait()
        except OSError as exc:
            self.events.put(("line", f"{label}-Installation konnte nicht gestartet werden: {exc}\n"))
            result = -1
        self.events.put(("installed", (key, label, result)))

    def _launch(self, selection: Selection) -> None:
        command = launch_command(selection, PROJECT)
        try:
            self.process_group = EmulatorProcesses.launch(
                command, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.running = self.process_group.process
        except OSError as exc:
            self.status.set("Start fehlgeschlagen")
            self._write(f"Start fehlgeschlagen: {exc}\n")
            self.start_button.configure(state="normal")
            return
        self.stop_button.configure(state="normal")
        self.status.set("Emulator läuft; „Alles beenden“ beendet diesen Lauf samt Bridges.")
        self._write(f"Startprozess: PID {self.running.pid}\n")
        threading.Thread(target=self._read_launcher, args=(self.process_group,), daemon=True).start()

    def _read_launcher(self, group: EmulatorProcesses) -> None:
        process = group.process
        # A failed launcher can leave descendants holding stdout open. Watch
        # its exit independently so those children cannot stall cleanup.
        def reap():
            process.wait()
            group.close()
        threading.Thread(target=reap, daemon=True).start()
        assert process.stdout is not None
        for line in process.stdout:
            match = re.search(r"QEMU_PID=(\d+)", line)
            if match:
                group.qemu_pid = int(match[1])
            self.events.put(("line", line))
        result = process.wait()
        group.close()
        self.events.put(("ended", (group, result)))

    def _stop_all(self) -> None:
        if self.stopping or self.process_group is None:
            return
        self.stopping = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.status.set("Emulator wird beendet; XP erhält zuerst eine Abschaltanforderung…")
        group = self.process_group
        def worker():
            try:
                graceful = group.stop()
                self.events.put(("line", "Alle Prozesse dieses Emulatorlaufs beendet.\n" if graceful else
                                 "Emulatorlauf samt Bridges beendet (XP-Abschaltung nicht bestätigt).\n"))
            except Exception as exc:
                self.events.put(("line", f"Beenden: {exc}\n"))
            finally:
                self.events.put(("stopped", group))
        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line":
                    self._write(str(payload))
                elif kind == "status":
                    self.status.set(str(payload))
                    if self.preparing:
                        self.prepare_phase = str(payload)
                        self._show_prepare_elapsed()
                elif kind == "prepare_phase":
                    if self.preparing:
                        self.prepare_phase = str(payload)
                        self._show_prepare_elapsed()
                elif kind == "prepared":
                    self._finish_prepare_timer("Fertig")
                    self.preparing = False
                    self.checking = False
                    self.prepared_copy.set(True)
                    self.prepare_button.configure(state="normal")
                    self.start_button.configure(state="disabled" if self.stopping else "normal")
                    self.check_button.configure(state="normal")
                    self.status.set("Arbeitskopie vorbereitet; Emulator kann gestartet werden.")
                    self._write(f"Arbeitskopie fertig: {payload}\n")
                elif kind == "prepare_failed":
                    self._finish_prepare_timer("Nicht abgeschlossen")
                    self.preparing = False
                    self.checking = False
                    self.prepare_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.check_button.configure(state="normal")
                    self.status.set("Image-Vorbereitung nicht abgeschlossen.")
                    self._write(f"Vorbereitung fehlgeschlagen: {payload}\n")
                    messagebox.showerror("Image-Vorbereitung", str(payload)[:2000])
                elif kind == "wsl_installed":
                    self.checking = False
                    self.check_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.prepare_button.configure(state="normal")
                    if payload:
                        self.status.set("WSL-Werkzeuge fehlen noch.")
                        self._write(f"WSL-Installation fehlgeschlagen: {payload}\n")
                    else:
                        self.status.set("WSL-Werkzeuge installiert.")
                        self._write("WSL-Werkzeuge installiert.\n")
                elif kind == "checked":
                    selection, start, issues = payload
                    self.checking = False
                    self.check_button.configure(state="normal")
                    if issues:
                        self.status.set(f"{len(issues)} Problem(e) gefunden")
                        self._write("Vorprüfung:\n" + "\n".join(f"• {issue}" for issue in issues) + "\n")
                        if start:
                            messagebox.showerror("Start nicht möglich", "\n".join(issues[:8]))
                    else:
                        self.status.set("Dateien und Programme geprüft.")
                        self._write("Vorprüfung erfolgreich.\n")
                        if start:
                            self._launch(selection)
                    if not self.running or self.running.poll() is not None:
                        self.start_button.configure(state="normal")
                elif kind == "ended":
                    group, result = payload
                    if group is not self.process_group:
                        continue
                    self.running = None
                    self.status.set(f"Emulatorlauf beendet (Code {result}); Unterprozesse aufgeräumt.")
                    self.start_button.configure(state="disabled" if self.stopping else "normal")
                    self.stop_button.configure(state="disabled")
                    if not self.stopping:
                        self.process_group = None
                elif kind == "stopped":
                    if payload is not self.process_group:
                        continue
                    self.process_group = None
                    self.running = None
                    self.stopping = False
                    self.status.set("Emulator und alle zugehörigen Unterprozesse beendet.")
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if self.close_after_stop:
                        self.destroy()
                        return
                elif kind == "installed":
                    key, label, result = payload
                    self.checking = False
                    self.check_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.install_button.configure(state="normal")
                    self.python_button.configure(state="normal")
                    self.status.set(f"{label} installiert." if result == 0 else
                                    f"{label}-Installation fehlgeschlagen (Code {result}).")
                    if result == 0:
                        refreshed = suggested_selection(Selection(**{
                            field: variable.get().strip() for field, variable in self.variables.items()
                        }))
                        for field in (("qemu_x86", "qemu_m68k") if key == "qemu" else ("python",)):
                            self.variables[field].set(getattr(refreshed, field))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _show_prepare_elapsed(self) -> None:
        if self.prepare_started_at is not None:
            elapsed = format_elapsed(time.monotonic() - self.prepare_started_at)
            self.prepare_timer.set(
                f"Image-Einrichtung · {self.prepare_phase} · {elapsed} vergangen"
            )

    def _finish_prepare_timer(self, result: str) -> None:
        if self.prepare_started_at is not None:
            elapsed = format_elapsed(time.monotonic() - self.prepare_started_at)
            self.prepare_timer.set(f"Image-Einrichtung · {result} nach {elapsed}")
            self.prepare_started_at = None
        self.prepare_progress.stop()

    def _tick_prepare_timer(self) -> None:
        if self.preparing:
            self._show_prepare_elapsed()
        self.after(1000, self._tick_prepare_timer)

    def _write(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _on_close(self) -> None:
        if self.preparing:
            messagebox.showwarning(
                "Vorbereitung läuft", "Bitte die Image-Vorbereitung abwarten. "
                "Ein Abbruch während des Kopierens oder der Gastinstallation wäre unsicher.",
            )
            return
        if self.process_group is not None:
            if not messagebox.askyesno(
                "Emulator beenden", "Emulator samt Bridges beenden und das Startfenster schließen?",
            ):
                return
            self.close_after_stop = True
            self._stop_all()
            return
        self.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-bundle":
        report = {
            "runtime": str(PROJECT),
            "scripts": len(list((PROJECT / "scripts").glob("*.py"))),
            "required_python_files": {
                name: (PROJECT / "scripts" / name).is_file()
                for name in REQUIRED_PYTHON_FILES
            },
            "powershell_files": {
                name: (PROJECT / name).is_file()
                for name in (
                    "program-and-start-emulator.ps1", "start-real-database.ps1",
                    "test-swiftshader.ps1",
                )
            },
            "shell_files": {
                name: (PROJECT / "scripts" / name).is_file() for name in SHELL_FILES
            },
            "own_binaries": {
                str(name): (PROJECT / name).is_file() for name in OWN_BINARIES
            },
            "component_hashes": {
                name: check_file(PROJECT / relative, digest, name) is None
                for name, (relative, digest) in COMPONENTS.items()
            },
        }
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2), encoding="utf-8")
        if (not all(report["required_python_files"].values())
                or not all(report["powershell_files"].values())
                or not all(report["shell_files"].values())
                or not all(report["own_binaries"].values())
                or not all(report["component_hashes"].values())
                or report["scripts"] < 5):
            raise SystemExit(1)
    else:
        Launcher().mainloop()
