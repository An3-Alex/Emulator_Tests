"""Windows USB flasher for the isolated M90 ESP32-S3 bench prototype."""

from __future__ import annotations

import queue
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from usb_flash_core import (
    BUILD, DEFAULT_SEED, IDF_REPO, GUI_CONFIG, PROJECT,
    check_probe_output, idf_command,
    esptool_available, flash_command, gui_sdkconfig,
    make_flash_plan, probe_command, record_firmware_profile, seed_command,
    seed_destination, source_files, temporary_seed_path,
    validate_firmware_profile, validate_port, validate_seed,
)
from usb_log_core import stream_serial_log


def com_ports() -> list[str]:
    ports: set[str] = set()
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
                index = 0
                while True:
                    try:
                        _, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    try:
                        ports.add(validate_port(str(value)))
                    except ValueError:
                        pass
                    index += 1
        except OSError:
            pass
    try:
        from serial.tools import list_ports

        for item in list_ports.comports():
            try:
                ports.add(validate_port(item.device))
            except ValueError:
                pass
    except ImportError:
        pass
    return sorted(ports, key=lambda port: int(port[3:]))


class UsbFlasher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("M90 Datenbank – ESP32-S3 USB-Flasher")
        self.geometry("850x610")
        self.minsize(680, 480)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.monitor_stop = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.port = tk.StringVar()
        originals = Path.home() / "Desktop" / "Merkur DB"
        self.source_folder = tk.StringVar(value=str(originals) if originals.is_dir() else "")
        initial_seed = DEFAULT_SEED
        if originals.is_dir():
            try:
                candidate = seed_destination(source_files(originals))
                if candidate.is_file():
                    validate_seed(candidate)
                    initial_seed = candidate
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        self.seed = tk.StringVar(value=str(initial_seed))
        self.uart_test = tk.BooleanVar(value=False)
        self.usb_bridge = tk.BooleanVar(value=False)
        self.tx_gpio = tk.StringVar(value="17")
        self.rx_gpio = tk.StringVar(value="18")
        self.status = tk.StringVar(value="Bereit – nur für einen losen ESP32-S3 N16R8.")

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ESP32-S3 N16R8 per USB flashen",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Bench-Prototyp: noch kein einsatzfähiger Ersatz im Automaten. "
                 "Keine Auszahl- oder Geldgeräte anschließen.",
            foreground="#9a3500", wraplength=790,
        ).pack(anchor="w", pady=(3, 14))

        port_row = ttk.Frame(frame)
        port_row.pack(fill="x", pady=4)
        ttk.Label(port_row, text="USB-/COM-Port:", width=18).pack(side="left")
        self.port_picker = ttk.Combobox(port_row, textvariable=self.port, width=24)
        self.port_picker.pack(side="left")
        ttk.Button(port_row, text="Ports aktualisieren",
                   command=self.refresh_ports).pack(side="left", padx=8)

        source_row = ttk.Frame(frame)
        source_row.pack(fill="x", pady=4)
        ttk.Label(source_row, text="Originaldateien:", width=18).pack(side="left")
        ttk.Entry(source_row, textvariable=self.source_folder).pack(
            side="left", fill="x", expand=True)
        ttk.Button(source_row, text="Ordner wählen…",
                   command=self.choose_source_folder).pack(side="left", padx=(8, 0))

        seed_row = ttk.Frame(frame)
        seed_row.pack(fill="x", pady=4)
        ttk.Label(seed_row, text="Datenbank-Abbild:", width=18).pack(side="left")
        ttk.Entry(seed_row, textvariable=self.seed).pack(side="left", fill="x", expand=True)
        ttk.Button(seed_row, text="Auswählen…", command=self.choose_seed).pack(
            side="left", padx=(8, 0))

        ttk.Label(frame, text=f"Firmware-Build: {BUILD}", wraplength=790).pack(
            anchor="w", pady=(9, 3))
        ttk.Label(
            frame,
            text="Beim ersten Mal Firmware + Datenbank; für ein späteres Abbild "
                 "nur Datenbank. db_a/db_b werden nicht beschrieben.",
            wraplength=790,
        ).pack(anchor="w", pady=(0, 8))

        uart_row = ttk.Frame(frame)
        uart_row.pack(fill="x", pady=(0, 5))
        ttk.Checkbutton(uart_row, text="UART-Test 9600 Baud aktivieren",
                        variable=self.uart_test).pack(side="left", padx=(0, 10))
        ttk.Label(uart_row, text="ESP TX GPIO").pack(side="left")
        ttk.Entry(uart_row, textvariable=self.tx_gpio, width=4).pack(side="left", padx=(4, 10))
        ttk.Label(uart_row, text="ESP RX GPIO").pack(side="left")
        ttk.Entry(uart_row, textvariable=self.rx_gpio, width=4).pack(side="left", padx=4)
        ttk.Checkbutton(
            frame, text="QEMU-USB-Brücke aktivieren (statt GPIO-UART; Startmodus separat)",
            variable=self.usb_bridge,
        ).pack(anchor="w", pady=(0, 5))

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=5)
        self.setup_button = ttk.Button(actions, text="Toolchain einrichten",
                                       command=self.start_setup)
        self.setup_button.pack(side="left", padx=(0, 8))
        self.seed_build_button = ttk.Button(actions, text="Datenbank-Abbild erzeugen",
                                            command=self.start_seed_build)
        self.seed_build_button.pack(side="left", padx=(0, 8))
        self.build_button = ttk.Button(actions, text="Abbild + Firmware vorbereiten",
                                       command=self.start_build)
        self.build_button.pack(side="left", padx=(0, 8))
        flash_actions = ttk.Frame(frame)
        flash_actions.pack(fill="x", pady=(0, 5))
        self.full_button = ttk.Button(flash_actions, text="Firmware + Datenbank flashen",
                                      command=lambda: self.start_flash(True))
        self.full_button.pack(side="left", padx=(0, 8))
        self.seed_button = ttk.Button(flash_actions, text="Nur Datenbank flashen",
                                      command=lambda: self.start_flash(False))
        self.seed_button.pack(side="left")

        monitor_actions = ttk.Frame(frame)
        monitor_actions.pack(fill="x", pady=(5, 0))
        self.monitor_button = ttk.Button(monitor_actions, text="Live-Log starten",
                                         command=self.toggle_monitor)
        self.monitor_button.pack(side="left", padx=(0, 8))
        ttk.Button(monitor_actions, text="Log speichern…",
                   command=self.save_log).pack(side="left")
        ttk.Label(monitor_actions, text="USB-Log: 115200 Baud, nur lesen").pack(
            side="left", padx=12)

        ttk.Label(frame, textvariable=self.status, wraplength=790).pack(
            anchor="w", pady=(10, 5))
        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", state="disabled",
                           font=("Consolas", 10), height=15)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        self.refresh_ports()
        self.after(100, self.drain_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_ports(self) -> None:
        found = com_ports()
        self.port_picker["values"] = found
        if self.port.get() not in found:
            self.port.set("")
        if not found:
            self.write_log("Kein COM-Port erkannt. USB-Kabel/Treiber prüfen.\n")

    def choose_seed(self) -> None:
        path = filedialog.askopenfilename(
            title="Geprüftes ESP32-Datenbank-Abbild wählen",
            initialdir=str(DEFAULT_SEED.parent),
            filetypes=[("ESP32-Datenbank-Abbild", "*.bin"), ("Alle Dateien", "*.*")],
        )
        if path:
            self.seed.set(path)

    def choose_source_folder(self) -> None:
        path = filedialog.askdirectory(title="Ordner mit originalen Datenbankdateien wählen",
                                       initialdir=self.source_folder.get() or str(Path.home()))
        if path:
            self.source_folder.set(path)

    def write_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line)
        self.log.see("end")
        self.log.configure(state="disabled")

    def drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.write_log(value)
                elif kind == "seed_ready":
                    self.seed.set(value)
                elif kind == "done":
                    self.busy = False
                    self.set_buttons(True)
                    self.status.set(value)
                    messagebox.showinfo("ESP32-Flasher", value)
                elif kind == "error":
                    self.busy = False
                    self.set_buttons(True)
                    self.status.set("Fehler – siehe Log")
                    self.write_log(f"FEHLER: {value}\n")
                    messagebox.showerror("ESP32-Flasher", value)
                elif kind == "monitor_stopped":
                    self.monitor_thread = None
                    self.monitor_button.configure(text="Live-Log starten", state="normal")
                    self.set_buttons(not self.busy)
                    self.status.set(value)
                    self.write_log(value + "\n")
                elif kind == "monitor_error":
                    self.monitor_thread = None
                    self.monitor_button.configure(text="Live-Log starten", state="normal")
                    self.set_buttons(not self.busy)
                    self.status.set("USB-Log Fehler – siehe Log")
                    self.write_log(f"USB-LOG FEHLER: {value}\n")
        except queue.Empty:
            pass
        self.after(100, self.drain_events)

    def set_buttons(self, enabled: bool) -> None:
        monitoring = self.monitor_thread is not None and self.monitor_thread.is_alive()
        state = "normal" if enabled and not monitoring else "disabled"
        for button in (self.setup_button, self.seed_build_button, self.build_button,
                       self.full_button, self.seed_button):
            button.configure(state=state)

    def dispatch(self, action, status: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.set_buttons(False)
        self.status.set(status)

        def worker() -> None:
            try:
                result = action()
                self.events.put(("done", result))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def run_command(self, command: list[str], cwd: Path | None = None,
                    *, capture: bool = True) -> str:
        self.events.put(("log", "> " + subprocess.list2cmdline(command) + "\n"))
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            if capture:
                output.append(line)
            self.events.put(("log", line))
        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"Werkzeug beendet mit Fehlercode {returncode}; siehe Log")
        return "".join(output)

    def prepare_seed(self, folder: Path) -> Path:
        files = source_files(folder)
        destination = seed_destination(files)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            validate_seed(destination)
            self.events.put(("log", f"Bereits geprüftes Abbild: {destination}\n"))
        else:
            temporary = temporary_seed_path(destination)
            try:
                self.run_command(seed_command(files, temporary), PROJECT.parent)
                validate_seed(temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        self.events.put(("seed_ready", str(destination)))
        self.events.put(("log", "FactoryReset wurde per SHA-256 geprüft, "
                                  "aber nicht in dieses Kaltstart-Abbild eingespielt.\n"))
        return destination

    def start_setup(self) -> None:
        if self.monitor_thread is not None:
            messagebox.showerror("COM-Port belegt", "Bitte zuerst das Live-Log stoppen.")
            return
        if not messagebox.askyesno(
            "ESP-IDF einrichten",
            "Offizielle Espressif-Toolchain 5.5.1 für ESP32-S3 herunterladen "
            "und installieren? Das kann mehrere GB und einige Minuten benötigen. "
            "Es wird kein Board geflasht.",
        ):
            return

        def setup() -> str:
            if (IDF_REPO / "tools" / "idf.py").is_file():
                try:
                    self.run_command(idf_command("check"), PROJECT)
                    return "ESP-IDF 5.5.1 ist bereits einsatzbereit."
                except RuntimeError:
                    self.events.put(("log", "ESP-IDF wird vervollständigt …\n"))
            else:
                self.events.put(("log", "Offizielle ESP-IDF-Quellen werden geholt …\n"))
            self.run_command(idf_command("install"), PROJECT, capture=False)
            self.run_command(idf_command("check"), PROJECT)
            return "ESP-IDF 5.5.1 für ESP32-S3 ist eingerichtet."

        self.dispatch(setup, "Offizielle ESP-IDF-Werkzeuge werden eingerichtet …")

    def start_seed_build(self) -> None:
        if self.monitor_thread is not None:
            messagebox.showerror("COM-Port belegt", "Bitte zuerst das Live-Log stoppen.")
            return
        folder = Path(self.source_folder.get()).expanduser()

        def build() -> str:
            destination = self.prepare_seed(folder)
            return f"Datenbank-Abbild geprüft: {destination.name}. Es wurde nichts geflasht."

        self.dispatch(build, "Originaldateien werden geprüft und Abbild erzeugt …")

    def start_build(self) -> None:
        if self.monitor_thread is not None:
            messagebox.showerror("COM-Port belegt", "Bitte zuerst das Live-Log stoppen.")
            return
        try:
            uart_enabled = self.uart_test.get()
            tx_gpio = int(self.tx_gpio.get())
            rx_gpio = int(self.rx_gpio.get())
            usb_bridge = self.usb_bridge.get()
            config = gui_sdkconfig(uart_enabled, tx_gpio, rx_gpio, usb_bridge)
        except ValueError as error:
            messagebox.showerror("UART-Konfiguration ungültig", str(error))
            return
        folder = Path(self.source_folder.get()).expanduser()

        def build() -> str:
            if not (IDF_REPO / "tools" / "idf.py").is_file():
                raise RuntimeError("Lokale ESP-IDF-Installation fehlt. "
                                   "Bitte zuerst 'Toolchain einrichten' wählen.")
            if not (PROJECT / "generated" / "m68kops.c").is_file():
                raise RuntimeError("Musashi-Tabellen fehlen: erst den Host-Build erzeugen")
            seed = self.prepare_seed(folder)
            GUI_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            GUI_CONFIG.write_text(config, encoding="utf-8")
            self.run_command(idf_command("build"), PROJECT, capture=False)
            firmware_files = make_flash_plan(seed, True)
            record_firmware_profile(uart_enabled, tx_gpio, rx_gpio,
                                    usb_bridge=usb_bridge)
            self.events.put(("log", f"Geprüfte Flash-Dateien: {len(firmware_files)}\n"))
            return "Abbild und Firmware bereit. Das Board wurde noch nicht beschrieben."

        self.dispatch(build, "Abbild und Firmware werden vorbereitet …")

    def start_flash(self, include_firmware: bool) -> None:
        if self.monitor_thread is not None:
            messagebox.showerror("COM-Port belegt", "Bitte zuerst das Live-Log stoppen.")
            return
        try:
            port = validate_port(self.port.get())
            seed = Path(self.seed.get()).expanduser()
            files = make_flash_plan(seed, include_firmware)
            profile = validate_firmware_profile() if include_firmware else None
            if not esptool_available():
                raise ValueError(
                    "esptool fehlt in dieser Python-Umgebung. "
                    "Installiere esptool 5.x oder starte aus dem IDF Terminal."
                )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            messagebox.showerror("Vorprüfung fehlgeschlagen", str(error))
            return
        targets = "\n".join(f"0x{item.offset:06X}  {item.path.name}"
                            for item in files)
        firmware_mode = ""
        if profile is not None:
            if profile.get("usb_bridge"):
                firmware_mode = "QEMU-USB-Brücke: EIN; UART0 nur Diagnose\n\n"
            elif profile["uart_enabled"]:
                firmware_mode = ("UART-Test: GPIO%d TX / GPIO%d RX\n\n" %
                                 (profile["tx_gpio"], profile["rx_gpio"]))
            else:
                firmware_mode = "UART-Test und QEMU-USB-Brücke: AUS\n\n"
        if not messagebox.askyesno(
            "Flashen bestätigen",
            f"Nur mit losem ESP32-S3 N16R8 fortfahren.\n\n"
            f"COM-Port: {port}\n\n{firmware_mode}Schreibbereiche:\n{targets}\n\n"
            "Vorhandene Daten an diesen Adressen werden überschrieben. Fortfahren?",
        ):
            return

        def flash() -> str:
            result = self.run_command(probe_command(port))
            check_probe_output(result)
            validate_seed(seed)
            if include_firmware:
                validate_firmware_profile()
            self.events.put(("log", "Chip und 16-MB-Flash bestätigt.\n"))
            self.run_command(flash_command(port, files))
            return ("Flashen abgeschlossen und von esptool geprüft. "
                    "Das ist noch kein Nachweis für Betrieb im Automaten.")

        self.dispatch(flash, "USB-Verbindung und Flash werden geprüft …")

    def toggle_monitor(self) -> None:
        if self.monitor_thread is not None:
            self.monitor_stop.set()
            self.monitor_button.configure(state="disabled")
            return
        if self.busy:
            return
        try:
            port = validate_port(self.port.get())
            import serial
        except (ValueError, ImportError) as error:
            messagebox.showerror(
                "USB-Log nicht verfügbar",
                f"{error}\n\nFür das Live-Log wird pyserial benötigt "
                "(im IDF-Terminal oder via python -m pip install pyserial).",
            )
            return
        self.monitor_stop.clear()

        def worker() -> None:
            try:
                self.events.put(("log", f"--- USB-Log {port} gestartet ---\n"))
                stream_serial_log(
                    port, self.monitor_stop,
                    lambda message: self.events.put(("log", message)),
                    serial.Serial,
                )
                self.events.put(("monitor_stopped", "USB-Log beendet"))
            except Exception as error:
                self.events.put(("monitor_error", str(error)))

        self.monitor_thread = threading.Thread(target=worker, daemon=True)
        self.monitor_thread.start()
        self.monitor_button.configure(text="Live-Log stoppen", state="normal")
        self.set_buttons(False)
        self.status.set(f"Live-Log auf {port} – Flashen erst nach Stopp möglich")

    def save_log(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="ESP32-Log speichern", defaultextension=".txt",
            filetypes=[("Textdatei", "*.txt"), ("Alle Dateien", "*.*")],
        )
        if not destination:
            return
        try:
            Path(destination).write_text(self.log.get("1.0", "end-1c"),
                                         encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Speichern fehlgeschlagen", str(error))

    def on_close(self) -> None:
        if self.busy:
            messagebox.showwarning("Vorgang läuft", "Bitte warten, bis der Vorgang beendet ist.")
            return
        self.destroy()

    def destroy(self) -> None:
        self.monitor_stop.set()
        super().destroy()


def main() -> int:
    if sys.platform != "win32":
        print("Diese Oberfläche ist für Windows-COM-Ports vorgesehen.", file=sys.stderr)
        return 2
    UsbFlasher().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
