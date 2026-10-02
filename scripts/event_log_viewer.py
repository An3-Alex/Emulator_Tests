"""Live, read-only event view of the original database bridge's console log."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time


TX_BYTE = re.compile(r"^DB_TO_COM3 ([0-9A-F]{2}) pc=")
RX_BYTES = re.compile(r"^COM3_TO_DB ((?:[0-9A-F]{2})(?: [0-9A-F]{2})*)$")
FRAME = re.compile(r"^DB_FRAME data=((?:[0-9A-F]{2})(?: [0-9A-F]{2})*)$")
SCC_FRAME = re.compile(r"^DB_SCC_B_TX_FRAME data=((?:[0-9A-F]{2})(?: [0-9A-F]{2})*)$")
MP_REPLY = re.compile(r"^DB_VIRTUAL_MP_REPLY data=((?:[0-9A-F]{2})(?: [0-9A-F]{2})*)$")

# Names and numeric IDs come from the owner's commandointerpreter.h.
VIDCOM_COMMANDS = {
    0x22: "INITVIDEO", 0x23: "FLIP", 0x24: "BLTBMP",
    0x25: "COPYAREA", 0x26: "WBUFF", 0x27: "RBUFF",
    0x28: "RABUFF", 0x29: "SDPEN", 0x2A: "DRAWLINE",
    0x2B: "DRAWREC", 0x2C: "DRAWELLIP", 0x2D: "BMPTRANSCOL",
    0x2E: "STXTBK", 0x2F: "FONT", 0x30: "DRAWTXT",
    0x31: "TRANSPARENTBLT", 0x32: "WABUFF", 0x33: "SETPROPERTY",
    0x34: "SYSERR", 0x35: "VIEWSBMP", 0x36: "VIEWMBMP",
    0x37: "RUN", 0x38: "STOP", 0x39: "CREATE",
    0x3A: "DESTROY", 0x3B: "SETVALUE", 0x3C: "SETVALUE_CRYPT",
    0x3D: "STOREBACK", 0x3E: "VIEW", 0x3F: "GAMECOMMAND",
    0x40: "COMMAND_VARIPARA", 0x41: "TOUCHCLICKDOWN",
    0x42: "PLAYSOUND", 0x43: "STOPSOUND", 0x44: "PAUSESOUND",
    0x45: "REQUEST_KEY", 0x46: "STARTUPTXT", 0x47: "RESETVIDEO",
    0x48: "DRAWTXTSTR", 0x49: "GRAPHICCHECKSUM",
    0x4A: "CREATESCREENSHOT", 0x4B: "GETPREVIOUSCOMMAND",
    0x4C: "EVENT_HW_BTN", 0x4D: "FADESOUNDOUT",
    0x4E: "RESTARTMACHINE", 0x4F: "SYSTEMINFO",
}


@dataclass(frozen=True)
class Event:
    direction: str
    title: str
    details: str
    level: str = "normal"


def describe_frame(data: bytes, *, reconstructed: bool = False) -> Event:
    hex_data = data.hex(" ").upper()
    qualifier = " (aus Konsolenausgabe rekonstruiert)" if reconstructed else ""
    details = f"{len(data)} Byte{qualifier}\n{hex_data}"
    if len(data) >= 3 and data[:2] == b"\x01\x02":
        command = data[2]
        if command == 0x42 and len(data) >= 7 and data[4:6] == b"\xED\x00":
            return Event(
                "DB → PC",
                "Bereitschaftssignal: Turbobuchen-Ton angefordert (ID 237)",
                details + "\nTonanforderung, keine Bestaetigung der Audiowiedergabe",
                "ready",
            )
        if command == 0x22:
            return Event("DB → PC", "INITVIDEO: Boardprofil gesendet", details)
        if command == 0x46 and len(data) >= 11:
            # STARTUPTXT is length (2), clear/R/G/B (4), then UTF-16LE.
            # The previous offset included green/blue as a bogus character.
            text_bytes = data[10:-1]
            text_bytes = text_bytes[: len(text_bytes) & ~1]
            message = text_bytes.decode("utf-16-le", errors="replace").strip()
            if message and all(ch.isprintable() for ch in message):
                return Event("DB → PC", f"Anzeigetext: {message}", details)
        name = VIDCOM_COMMANDS.get(command, "unbekannt")
        return Event("DB → PC", f"{name} (0x{command:02X})", details)
    return Event("DB → PC", "Serielle Daten gesendet", details)


def describe_reply(data: bytes) -> Event:
    details = f"{len(data)} Byte\n{data.hex(' ').upper()}"
    if data.startswith(b"\x06"):
        return Event("PC → DB", "Bestätigung (ACK)", details)
    if data.startswith(b"\x15"):
        return Event("PC → DB", "Ablehnung / Wiederholung (NAK)", details, "warning")
    if data.startswith(b"\x07"):
        printable = "".join(chr(value) for value in data[1:] if 32 <= value < 127)
        if len(printable) >= 4:
            return Event("PC → DB", f"Antwort: {printable[:80]}", details)
    return Event("PC → DB", "Serielle Antwort", details)


class BridgeLogParser:
    """Turn byte-wise technical output into bounded, human-readable events."""

    def __init__(self) -> None:
        self.tx = bytearray()
        self.legacy_frame: Event | None = None
        self.rx = bytearray()
        self.rx_updated_at = 0.0
        self.have_exact_frames = False
        self.initvideo_count = 0
        self.pending_initvideo: int | None = None
        self.last_idle_notice_at = float("-inf")
        self.last_mp_command = "unbekannt"
        self.ready_signal_count = 0

    def _record_frame(self, event: Event) -> Event:
        if event.level == "ready":
            self.ready_signal_count += 1
        if not event.title.startswith("INITVIDEO:"):
            return event
        self.initvideo_count += 1
        self.pending_initvideo = self.initvideo_count
        return Event(
            event.direction,
            f"INITVIDEO {self.initvideo_count}: {event.title.split(': ', 1)[1]}",
            event.details,
            event.level,
        )

    def _flush_legacy(self) -> list[Event]:
        if self.legacy_frame is None:
            return []
        event = self.legacy_frame
        self.legacy_frame = None
        return [] if self.have_exact_frames else [self._record_frame(event)]

    def _flush_reply(self) -> list[Event]:
        if not self.rx:
            return []
        data = bytes(self.rx)
        self.rx.clear()
        event = describe_reply(data)
        if self.pending_initvideo is not None and data.startswith(b"\x06\x03\x00\x00"):
            event = Event(
                event.direction,
                f"INITVIDEO {self.pending_initvideo}: vom PC bestätigt (ACK)",
                event.details,
            )
            self.pending_initvideo = None
        return [event]

    def flush_idle(self, now: float | None = None) -> list[Event]:
        now = time.monotonic() if now is None else now
        if self.rx and now - self.rx_updated_at >= 0.5:
            return self._flush_reply()
        return []

    def feed(self, line: str, *, now: float | None = None) -> list[Event]:
        line = line.strip()
        now = time.monotonic() if now is None else now
        match = RX_BYTES.fullmatch(line)
        if match:
            # A reply can follow a completed DB frame immediately. Publish
            # that frame first so its ACK is attributed to the right send.
            events = self._flush_legacy()
            self.rx.extend(bytes.fromhex(match.group(1)))
            self.rx_updated_at = now
            if len(self.rx) > 4096:
                events.extend(self._flush_reply())
            return events

        events = self._flush_reply()
        match = FRAME.fullmatch(line)
        if match:
            self.have_exact_frames = True
            self.legacy_frame = None
            self.tx.clear()
            events.append(self._record_frame(describe_frame(bytes.fromhex(match.group(1)))))
            return events

        match = SCC_FRAME.fullmatch(line)
        if match:
            frame = bytes.fromhex(match.group(1))
            packet = frame[1:] if frame and frame[0] == len(frame) - 1 else frame
            if packet.startswith(b"\x7F\x26"):
                name = "Prüfer-Statusabfrage (7F 26)"
            elif packet.startswith(b"\x7F\x27"):
                name = "Prüfer-Schlüsselaustausch (7F 27)"
            elif packet.startswith(b"\x7F\x00"):
                name = "Prüfer-Identitätsabfrage (7F 00)"
            elif packet.startswith((b"\x7F\x02", b"\x7F\x04")):
                name = "Prüfer-Typabfrage"
            elif packet.startswith(b"\x7B"):
                name = "Prüfer-Sessionpaket (7B, ohne erwartete Antwort)"
            else:
                name = f"Prüfer-Befehl {packet[:2].hex(' ').upper()}"
            self.last_mp_command = name
            events.append(Event("DB → Prüfer", name, line))
            return events

        if line.startswith("DB_VIRTUAL_MP_COIN_"):
            labels = {
                "QUEUED": "1 € beim virtuellen MP vorgemerkt",
                "SENT": "Virtueller MP sendet einen 1-Euro-Münzeinwurf",
                "CREDITED": "Datenbank hat 1 € gebucht",
                "REJECTED": "1-Euro-Münzeinwurf abgelehnt",
                "EXPIRED": "1-Euro-Einwurf nicht gesendet: Annahme nicht verfügbar",
                "UNCONFIRMED": "1-Euro-Einwurf gesendet, Buchung nicht bestätigt",
            }
            outcome = line.split()[0].removeprefix("DB_VIRTUAL_MP_COIN_")
            events.append(Event("Prüfer → DB", labels.get(outcome, "Virtueller Münzeinwurf"), line,
                                "warning" if outcome in ("REJECTED", "EXPIRED", "UNCONFIRMED") else "normal"))
            return events

        match = MP_REPLY.fullmatch(line)
        if match:
            data = bytes.fromhex(match.group(1))
            identity = data[:3].decode("ascii", errors="ignore")
            if identity == "NRI" and data[10:15] == b"eagle" and data[20:24] == b"FT30":
                title = "Virtueller Prüfer: NRI eagle / FT30 (Diagnoseprofil)"
            elif identity in ("NRI", "WHM"):
                title = f"Virtueller Prüfer meldet {identity}"
            else:
                title = f"Prüfer-Antwort auf {self.last_mp_command}"
            events.append(Event("Prüfer → DB", title, line))
            return events

        match = TX_BYTE.match(line)
        if match:
            value = int(match.group(1), 16)
            if value == 1 and not self.tx:
                events.extend(self._flush_legacy())
            if self.tx or value == 1:
                self.tx.append(value)
                if len(self.tx) > 8192:
                    self.tx.clear()
                elif value == 4:
                    self.legacy_frame = describe_frame(
                        bytes(self.tx), reconstructed=True
                    )
                    self.tx.clear()
            return events

        if line.startswith("DB_INITVIDEO_BOARD_PROFILE_COMPLETED"):
            # The first frame's byte log precedes this authoritative completed
            # frame. Do not show or count its incomplete reconstruction twice.
            self.legacy_frame = None
            self.tx.clear()
            events.append(self._record_frame(Event("DB → PC", "INITVIDEO: Originalprofil gesendet", line)))
        else:
            events.extend(self._flush_legacy())
        if line.startswith("DB_INITVIDEO_WIRE_RETRY"):
            attempt = re.search(r"attempt=(\d+)", line)
            label = attempt.group(1) if attempt else "?"
            events.append(Event("DB → PC", f"INITVIDEO erneut gesendet (Versuch {label})", line))
        elif line.startswith("DB_INITVIDEO_RETRY_REFRESHED"):
            events.append(Event("DB → PC", "Zweites Original-INITVIDEO: Wiederholung vorgemerkt", line))
        elif line.startswith("DB_INITVIDEO_RETRY_DISARMED"):
            events.append(Event("Status", "INITVIDEO-Wiederholung nach PC-Daten beendet (ACK nicht sicher)", line))
        elif line.startswith("DB_INITVIDEO_CLOCK_COMPLETED"):
            events.append(Event("DB → PC", "INITVIDEO-Uhr auf 2012 ergänzt", line))
        elif line.startswith("DB_RTC4543_ENABLED"):
            events.append(Event("Board", "R4543-Echtzeituhr aktiv (Startdatum 2012)", line))
        elif line.startswith("DB_RTC4543_READ"):
            events.append(Event("Board", "R4543-Kalender wurde gelesen", line))
        elif line.startswith("DB_RTC4543_WRITE"):
            events.append(Event("Board", "R4543-Kalender wurde gestellt", line))
        elif line.startswith("DB_CONFIG_RAM_PROGRAMMED"):
            events.append(Event("Board", "Las-Vegas-Config im 2-MB-RAM eingerichtet", line))
        elif line.startswith("DB_AUX_TRANSACTION_START"):
            command = re.search(r"command=([0-9A-F]{2})", line)
            code = command.group(1) if command else "??"
            events.append(Event("DB → Board", f"Nebengeräte-Abfrage {code}", line))
        elif line.startswith("DB_VIRTUAL_AUX_REPLY"):
            command = re.search(r"command=([0-9A-F]{2})", line)
            code = command.group(1) if command else "??"
            events.append(Event("Board → DB", f"Nebengeräte-Antwort {code}", line))
        elif line.startswith("DB_MP_STATE_WRITE"):
            events.append(Event("Status", "Münzprüfer-Zustand geändert", line))
        elif line.startswith("DB_DEVICE_DISCOVERY_TIMER_EXPIRED"):
            events.append(Event("Status", "Gerätesuche: Antwortzeit abgelaufen", line))
        elif line.startswith("DB_SCC_A_TX_SERVICE"):
            events.append(Event("Board", "Serieller Kanal A: Sendung wird abgearbeitet", line))
        elif line.startswith("DB_VIRTUAL_MP_CHALLENGE_COMPARE"):
            if "register_override=False" in line and "wire_match=True" in line:
                events.append(Event("Prüfer → DB", "Prüfer-Challenge auf der Leitung bestätigt", line))
            elif "register_override=True" in line:
                events.append(Event("Diagnose", "Prüfer-Challenge per Registereingriff überbrückt", line, "warning"))
            else:
                events.append(Event("Warnung", "Prüfer-Challenge ohne gültige Antwort", line, "warning"))
        elif line.startswith("DB_VIRTUAL_MP_CHALLENGE_FAILED"):
            events.append(Event("Warnung", "Prüfer-Schlüsselaustausch fehlgeschlagen", line, "warning"))
        elif line.startswith("DB_NESTED_SCC_A_TX_SERVICE"):
            events.append(Event("Board", "Serieller Kanal A unterbricht wartenden Timer", line))
        elif line.startswith("DB_NESTED_UART_REPLY"):
            events.append(Event("PC → DB", "PC-Antwort erreicht wartende Datenbank", line))
        elif line.startswith("DB_ACTIVE_BOARD_ISR_SNAPSHOT"):
            # The normal board timer is already active during boot. Surface
            # only the separate SCC-A service here; timer samples remain in
            # the raw log and in the live line counter.
            if "return=000751BA" in line:
                events.append(Event("Board", "SCC-A-Interrupt aktiv (Diagnose)", line))
        elif line.startswith("DB_IDLE_PROTOCOL_SNAPSHOT"):
            if now - self.last_idle_notice_at >= 10.0:
                events.append(
                    Event("Status", "Datenbank läuft; derzeit keine serielle Nachricht", line)
                )
                self.last_idle_notice_at = now
        elif line.startswith("DB_CABINET_INPUT_STATE"):
            closed = "door=closed" in line
            events.append(Event("Board", f"Türschalter: {'geschlossen' if closed else 'offen'}", line))
        elif line.startswith("DB_CABINET_BUTTON "):
            events.append(Event("Eingabe", "Kabinettaste betätigt", line))
        elif line.startswith("DB_CABINET_BUTTON_MAP"):
            events.append(Event("Board", "Kabinettasten aus Firmwareprofil geladen", line))
        elif line.startswith("DB_TIMER_ENABLED"):
            events.append(Event("Board", "Board-Timer aktiv / CPU-Drossel eingeschaltet", line))
        elif line.startswith("DB_TOUCH_RESPONSE"):
            events.append(Event("Board", "Touch-Controller hat geantwortet", line))
        elif line.startswith("DB_TOUCH_REQUEST"):
            events.append(Event("Eingabe", "Maus-/Touch-Eingabe angefordert", line))
        elif line.startswith("DB_TOUCH_INPUT"):
            events.append(Event("Eingabe", "Touchpaket an Datenbank geliefert", line))
        elif line.startswith("DB_INITVIDEO_RETRY_LIMIT_REACHED"):
            events.append(Event("Warnung", "INITVIDEO ohne PC-Antwort", line, "warning"))
        elif line.startswith("DB_COM3_DISCONNECTED"):
            events.append(Event("Status", "COM3-Verbindung zum Spiel-PC beendet", line))
        elif line.startswith(("Traceback", "RuntimeError:", "DB_ERROR")):
            events.append(Event("Fehler", line[:100], line, "warning"))
        return events


def detect_log_encoding(path: Path) -> str:
    with path.open("rb") as probe:
        prefix = probe.read(4)
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Virtuelle Datenbank – Ereignislog")
    root.geometry("960x600")
    root.minsize(680, 400)
    view = BridgeLogParser()
    source = None
    historical_until = 0
    sequence = 0
    event_details: dict[str, str] = {}
    lines_seen = 0
    last_line_at: datetime | None = None

    heading = ttk.Frame(root, padding=10)
    heading.pack(fill="x")
    ttk.Label(
        heading,
        text="Datenbank ↔ PC – Live-Ereignisse",
        font=("Segoe UI", 13, "bold"),
    ).pack(anchor="w")
    ttk.Label(heading, text=str(args.log), foreground="#666666").pack(anchor="w")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10)
    table_frame = ttk.Frame(notebook, padding=(0, 4, 0, 6))
    notebook.add(table_frame, text="Ereignisse")
    table = ttk.Treeview(
        table_frame,
        columns=("time", "direction", "event"),
        show="headings",
        selectmode="browse",
    )
    for key, title, width in (
        ("time", "Zeit", 90),
        ("direction", "Richtung", 115),
        ("event", "Ereignis", 690),
    ):
        table.heading(key, text=title)
        table.column(key, width=width, stretch=(key == "event"))
    table.tag_configure("warning", foreground="#A33020")
    table.tag_configure("ready", foreground="#087A36")
    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    table.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    raw_frame = ttk.Frame(notebook, padding=(0, 4, 0, 6))
    notebook.add(raw_frame, text="Rohprotokoll · jede Zeile")
    raw_view = tk.Text(raw_frame, wrap="none", font=("Consolas", 9))
    raw_scroll = ttk.Scrollbar(raw_frame, orient="vertical", command=raw_view.yview)
    raw_view.configure(yscrollcommand=raw_scroll.set, state="disabled")
    raw_view.pack(side="left", fill="both", expand=True)
    raw_scroll.pack(side="right", fill="y")

    ttk.Label(root, text="Details / Rohbytes", padding=(10, 0)).pack(anchor="w")
    details = tk.Text(root, height=7, wrap="word", font=("Consolas", 10))
    details.pack(fill="x", padx=10, pady=(0, 8))
    details.configure(state="disabled")
    status = ttk.Label(root, text="Warte auf Protokoll …", padding=(10, 0, 10, 8))
    status.pack(anchor="w")
    readiness = ttk.Label(root, text="Bereitschaftston von DB noch nicht angefordert", padding=(10, 0, 10, 8))
    readiness.pack(anchor="w")

    def add_event(event: Event, *, historical: bool) -> None:
        nonlocal sequence
        sequence += 1
        stamp = "vor Start" if historical else datetime.now().strftime("%H:%M:%S")
        item = table.insert(
            "",
            "end",
            values=(stamp, event.direction, event.title),
            tags=(event.level,),
        )
        event_details[item] = event.details
        if sequence > 3000:
            oldest = table.get_children()[0]
            event_details.pop(oldest, None)
            table.delete(oldest)
        table.see(item)
        if event.level == "ready":
            readiness.configure(
                text=f"DB hat Turbobuchen-Ton angefordert ({view.ready_signal_count}×) – Audiowiedergabe nicht bestätigt"
            )

    def show_details(_event=None) -> None:
        selected = table.selection()
        value = event_details.get(selected[0], "") if selected else ""
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", value)
        details.configure(state="disabled")

    table.bind("<<TreeviewSelect>>", show_details)

    def poll() -> None:
        nonlocal source, historical_until, lines_seen, last_line_at
        try:
            if source is None and args.log.exists():
                source = args.log.open(
                    "r", encoding=detect_log_encoding(args.log), errors="replace"
                )
                source.seek(0, 2)
                historical_until = source.tell()
                source.seek(0)
                status.configure(text="Live verbunden")
            if source is not None:
                raw_batch: list[str] = []
                for _ in range(1200):
                    line = source.readline()
                    if not line:
                        break
                    raw_batch.append(line)
                    lines_seen += 1
                    last_line_at = datetime.now()
                    historical = source.tell() <= historical_until
                    for event in view.feed(line):
                        add_event(event, historical=historical)
                if raw_batch:
                    follow_tail = raw_view.yview()[1] >= 0.98
                    raw_view.configure(state="normal")
                    raw_view.insert("end", "".join(raw_batch))
                    # The disk log remains complete. Bound only the GUI text
                    # widget so a long-running firmware loop stays responsive.
                    if int(raw_view.index("end-1c").split(".")[0]) > 20000:
                        raw_view.delete("1.0", "2001.0")
                    raw_view.configure(state="disabled")
                    if follow_tail:
                        raw_view.see("end")
                for event in view.flush_idle():
                    add_event(event, historical=False)
                if last_line_at is not None:
                    age = (datetime.now() - last_line_at).total_seconds()
                    activity = (
                        "liest neue Zeilen"
                        if age < 3
                        else f"seit {int(age)} s keine neuen Zeilen"
                    )
                    status.configure(
                        text=f"Live verbunden · {lines_seen} Logzeilen · "
                        f"letzte Aktualisierung {last_line_at:%H:%M:%S} · {activity}"
                    )
        except (OSError, UnicodeError) as exc:
            status.configure(text=f"Protokoll nicht lesbar: {exc}")
        root.after(250, poll)

    def close() -> None:
        if source is not None:
            source.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
