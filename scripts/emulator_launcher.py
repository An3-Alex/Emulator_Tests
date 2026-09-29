"""Small Windows front end for the owner-supplied M90 emulator inputs.

The UI does not modify CF images or download any protected game/database files.
"""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from portable_launcher_model import Selection, check_runtime, launch_command
from github_update import UpdateError, check_updates, install_updates


PROJECT = Path(__file__).resolve().parents[1]
SETTINGS = Path(os.environ.get("APPDATA", str(Path.home()))) / "M90 Emulator" / "settings.json"
DATABASE_FILES = {
    "database": "Magie_90_CC4.bin",
    "loader": "Loader_61640403_L5.0b_2MB.bin",
    "factory": "FactoryReset_61640403.xc",
    "config": "M90_Las_Vegas.bin",
}
FIELDS = (
    ("image", "Vorbereitetes CF-Image"),
    ("database", "Datenbank (M90)"),
    ("loader", "Loader"),
    ("factory", "Factory"),
    ("config", "Konfiguration"),
    ("admission_eeprom", "Zulassungskarte (EEPROM)"),
    ("qemu_x86", "QEMU Spiel-PC"),
    ("qemu_m68k", "QEMU Datenbank"),
    ("python", "Python"),
)


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
        values["python"] = sys.executable if Path(sys.executable).name.lower() == "python.exe" else shutil.which("python.exe") or ""
    return Selection(**values)


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("M90 Emulator – Einrichtung und Start")
        self.geometry("940x690")
        self.minsize(790, 600)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running: subprocess.Popen[str] | None = None
        self.checking = False

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
        self.status = tk.StringVar(value="Dateien auswählen und prüfen.")
        self._build()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="M90 Emulator", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Eigene Dateien auswählen. Die Dateien bleiben an ihrem Speicherort; das Programm prüft sie vor dem Start.",
            wraplength=870,
        ).pack(anchor="w", pady=(2, 10))
        notice = ttk.Label(
            outer,
            text="Wichtig: Derzeit startet nur ein bereits vorbereitetes CF-Image. "
                 "Die automatische Einrichtung eines unveränderten Images ist noch in Arbeit.",
            foreground="#9b3500", wraplength=870,
        )
        notice.pack(anchor="w", pady=(0, 8))

        form = ttk.Frame(outer)
        form.pack(fill="x")
        for row, (key, label) in enumerate(FIELDS):
            ttk.Label(form, text=label, width=28).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.variables[key]).grid(
                row=row, column=1, sticky="ew", padx=(4, 6), pady=3
            )
            ttk.Button(form, text="Auswählen…", command=lambda selected=key: self._browse(selected)).grid(
                row=row, column=2, pady=3
            )
        form.columnconfigure(1, weight=1)
        ttk.Button(
            outer, text="Datenbank-Dateien aus einem Ordner übernehmen…",
            command=self._choose_database_directory,
        ).pack(anchor="w", pady=(8, 8))

        ttk.Checkbutton(
            outer, text="Live-Protokoll in einem eigenen Fenster anzeigen",
            variable=self.show_log,
        ).pack(anchor="w")
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(12, 6))
        self.check_button = ttk.Button(controls, text="Dateien und Programme prüfen", command=self._check)
        self.check_button.pack(side="left")
        self.start_button = ttk.Button(controls, text="Emulator starten", command=self._start)
        self.start_button.pack(side="left", padx=8)
        self.install_button = ttk.Button(controls, text="QEMU installieren", command=self._install_qemu)
        self.install_button.pack(side="left")
        self.update_button = ttk.Button(controls, text="Updates prüfen", command=self._check_updates)
        self.update_button.pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.status).pack(side="left", padx=10)

        ttk.Label(outer, text="Startmeldungen", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        output_frame = ttk.Frame(outer)
        output_frame.pack(fill="both", expand=True)
        self.output = tk.Text(output_frame, height=8, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _browse(self, key: str) -> None:
        current = self.variables[key].get()
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
        return Selection(**{key: value.get().strip() for key, value in self.variables.items()},
                         show_live_log=self.show_log.get())

    def _save(self, selection: Selection) -> bool:
        try:
            selection.save(SETTINGS)
            return True
        except OSError as exc:
            messagebox.showerror("Einstellungen", f"Auswahl konnte nicht gespeichert werden:\n{exc}")
            return False

    def _check(self) -> None:
        if self.checking:
            return
        selection = self._selection()
        if not self._save(selection):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.status.set("Prüfung läuft…")
        threading.Thread(target=self._check_worker, args=(selection, False), daemon=True).start()

    def _start(self) -> None:
        if self.checking or (self.running and self.running.poll() is None):
            return
        selection = self._selection()
        if not self._save(selection):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Vorprüfung vor dem Start…")
        threading.Thread(target=self._check_worker, args=(selection, True), daemon=True).start()

    def _check_worker(self, selection: Selection, start: bool) -> None:
        try:
            issues = check_runtime(selection)
        except Exception as exc:
            issues = [f"Prüfung fehlgeschlagen: {exc}"]
        self.events.put(("checked", (selection, start, issues)))

    def _install_qemu(self) -> None:
        if self.checking or self.running and self.running.poll() is None:
            return
        winget = shutil.which("winget.exe")
        if not winget:
            messagebox.showerror(
                "QEMU installieren", "Windows-Paketmanager winget wurde nicht gefunden. "
                "Bitte QEMU manuell installieren und danach die beiden EXE-Dateien auswählen."
            )
            return
        if not messagebox.askyesno(
            "QEMU installieren",
            "QEMU über den Windows-Paketmanager installieren? Die Installation kann "
            "eine Windows-Freigabe erfordern. Es werden keine Image-/Datenbankdateien verändert.",
        ):
            return
        self.checking = True
        self.check_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.install_button.configure(state="disabled")
        self.status.set("QEMU-Installation läuft…")
        threading.Thread(target=self._install_qemu_worker, args=(winget,), daemon=True).start()

    def _install_qemu_worker(self, winget: str) -> None:
        command = [
            winget, "install", "--exact", "--id", "SoftwareFreedomConservancy.QEMU",
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
            self.events.put(("line", f"QEMU-Installation konnte nicht gestartet werden: {exc}\n"))
            result = -1
        self.events.put(("installed", result))

    def _check_updates(self) -> None:
        if self.checking or self.running and self.running.poll() is None:
            messagebox.showinfo("Updates", "Den Emulator vor einem Quellcode-Update beenden.")
            return
        self.checking = True
        self.update_button.configure(state="disabled")
        self.status.set("GitHub-Updates werden geprüft…")
        threading.Thread(target=self._update_worker, args=(False,), daemon=True).start()

    def _update_worker(self, install: bool) -> None:
        try:
            result = install_updates(PROJECT) if install else check_updates(PROJECT)
            self.events.put(("updated" if install else "update_check", result))
        except UpdateError as exc:
            self.events.put(("update_error", str(exc)))

    def _launch(self, selection: Selection) -> None:
        command = launch_command(selection, PROJECT)
        try:
            self.running = subprocess.Popen(
                command, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.status.set("Start fehlgeschlagen")
            self._write(f"Start fehlgeschlagen: {exc}\n")
            self.start_button.configure(state="normal")
            return
        self.status.set("Emulator läuft; zum Beenden QEMU-Fenster schließen.")
        self._write(f"Startprozess: PID {self.running.pid}\n")
        threading.Thread(target=self._read_launcher, args=(self.running,), daemon=True).start()

    def _read_launcher(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("line", line))
        self.events.put(("ended", process.wait()))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line":
                    self._write(str(payload))
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
                    self.running = None
                    self.status.set(f"Startprozess beendet (Code {payload}).")
                    self.start_button.configure(state="normal")
                elif kind == "installed":
                    self.checking = False
                    self.check_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.install_button.configure(state="normal")
                    self.status.set("QEMU installiert." if payload == 0 else
                                    f"QEMU-Installation fehlgeschlagen (Code {payload}).")
                    if payload == 0:
                        refreshed = suggested_selection(self._selection())
                        for key in ("qemu_x86", "qemu_m68k"):
                            self.variables[key].set(getattr(refreshed, key))
                elif kind == "update_check":
                    self.checking = False
                    self.update_button.configure(state="normal")
                    count = int(payload)
                    if count == 0:
                        self.status.set("Quellcode ist aktuell.")
                    elif messagebox.askyesno(
                        "Update verfügbar",
                        f"{count} neue(r) GitHub-Commit(s) verfügbar. Jetzt laden? "
                        "Das Startfenster muss danach neu geöffnet werden.",
                    ):
                        self.checking = True
                        self.update_button.configure(state="disabled")
                        self.status.set("Update wird installiert…")
                        threading.Thread(target=self._update_worker, args=(True,), daemon=True).start()
                elif kind == "updated":
                    self.checking = False
                    self.update_button.configure(state="normal")
                    self.status.set("Update installiert; bitte Startfenster neu öffnen.")
                    self._write(str(payload) + "\n")
                elif kind == "update_error":
                    self.checking = False
                    self.update_button.configure(state="normal")
                    self.status.set("Update nicht möglich.")
                    self._write(f"Update: {payload}\n")
                    messagebox.showerror("GitHub-Update", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _write(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _on_close(self) -> None:
        if self.running and self.running.poll() is None:
            if not messagebox.askyesno(
                "Emulator läuft", "Der Emulator läuft weiter, wenn dieses Fenster geschlossen wird.\n"
                "Zum Beenden bitte das QEMU-Fenster schließen. Startfenster trotzdem schließen?",
            ):
                return
        self.destroy()


if __name__ == "__main__":
    Launcher().mainloop()
