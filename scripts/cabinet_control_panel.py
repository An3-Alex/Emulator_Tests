"""Visible operator controls and mouse-to-touch bridge for the M90 emulator."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk

from cabinet_controls import CONTROL_PORT, TOUCH_HEIGHT, TOUCH_WIDTH, send_command
from virtual_touch import TARGETS


BUTTONS = (
    ("Menü", "menu"), ("Autostart", "autostart"),
    ("Einsatz", "einsatz"), ("Maxeinsatz", "maxeinsatz"),
    ("Start", "start"), ("Auszahlung", "auszahlung"),
    ("Service", "service"),
)
SCREEN_CAPTURE_INTERVAL_SECONDS = 2.0


def qmp_screendump(port: int, destination: Path) -> None:
    """Capture the game's lower cabinet display; never inject VM input."""
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        sock.settimeout(2.0)
        stream = sock.makefile("rwb")
        greeting = json.loads(stream.readline())
        if "QMP" not in greeting:
            raise RuntimeError("not a QMP endpoint")
        for command in (
            {"execute": "qmp_capabilities"},
            {"execute": "screendump", "arguments": {
                "filename": str(destination), "device": "lower", "format": "png"
            }},
        ):
            stream.write((json.dumps(command) + "\n").encode("ascii"))
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if "event" in response:
                    continue
                if "error" in response:
                    raise RuntimeError(str(response["error"]))
                break


class Point(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class Rect(ctypes.Structure):
    _fields_ = (("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long))


def video_touch_point(point: tuple[int, int], client_size: tuple[int, int],
                      image_size: tuple[int, int], backend: str = "gtk",
                      title: str = "") -> tuple[int, int] | None:
    """Map a client-area point; SDL console 1 must never control console 0."""
    if backend not in ("gtk", "sdl"):
        raise ValueError("Unknown display backend")
    if backend == "sdl" and not title.startswith("QEMU (M90-3dfx-0)"):
        return None
    client_w, client_h = client_size
    source_w, source_h = image_size
    header = 45 if backend == "gtk" else 0
    if min(client_w, client_h - header, source_w, source_h) <= 0:
        return None
    video_w = min(client_w, int((client_h - header) * source_w / source_h))
    video_h = int(video_w * source_h / source_w)
    video_left = (client_w - video_w) // 2
    video_top = client_h - video_h if backend == "gtk" else (client_h - video_h) // 2
    x, y = point
    if video_w <= 0 or video_h <= 0 or not (
        video_left <= x < video_left + video_w and video_top <= y < video_top + video_h
    ):
        return None
    return (min(TOUCH_WIDTH - 1, int((x - video_left) * TOUCH_WIDTH / video_w)),
            min(TOUCH_HEIGHT - 1, int((y - video_top) * TOUCH_HEIGHT / video_h)))


def qemu_cursor_position(
    qemu_pid: int, image_size: tuple[int, int] = (TOUCH_WIDTH, TOUCH_HEIGHT),
    backend: str = "gtk",
) -> tuple[int, int] | None:
    """Return lower-screen pixels when the pointer is in QEMU's video area."""
    if os.name != "nt" or not qemu_pid:
        return None
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)
    )
    user32.GetCursorPos.argtypes = (ctypes.POINTER(Point),)
    user32.ScreenToClient.argtypes = (ctypes.c_void_p, ctypes.POINTER(Point))
    user32.GetClientRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(Rect))
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value != qemu_pid:
        return None
    point = Point()
    rect = Rect()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    if not user32.ScreenToClient(hwnd, ctypes.byref(point)):
        return None
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    title = ctypes.create_unicode_buffer(256)
    if backend == "sdl":
        user32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        user32.GetWindowTextW(hwnd, title, len(title))
    return video_touch_point((point.x, point.y),
                             (rect.right - rect.left, rect.bottom - rect.top),
                             image_size, backend, title.value)


class ControlPanel:
    def __init__(self, root: tk.Tk, *, port: int, qmp_port: int,
                 qemu_pid: int, capture_path: Path, door_open: bool,
                 display_backend: str = "gtk") -> None:
        self.root = root
        self.port = port
        self.qmp_port = qmp_port
        self.qemu_pid = qemu_pid
        self.capture_path = capture_path
        self.display_backend = display_backend
        self.stop = threading.Event()
        self.frames: queue.Queue[Path] = queue.Queue(maxsize=2)
        self.status = tk.StringVar(value="Verbinde mit Datenbank …")
        self.door_open = tk.BooleanVar(value=door_open)
        self.native_mouse = tk.BooleanVar(value=True)
        self.photo: tk.PhotoImage | None = None
        self.image_size = (TOUCH_WIDTH, TOUCH_HEIGHT)
        self.display_size = (400, 300)
        self.held_buttons: set[str] = set()
        self.pad_touch: tuple[int, int] | None = None
        self.qemu_touch: tuple[int, int] | None = None
        self.previous_left_down = False
        self.calibration_window: tk.Toplevel | None = None

        root.title("M90 Emulator – Bedienfeld")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=10)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Unterer Automatenbildschirm – hier mit der Maus berühren").grid(
            row=0, column=0, columnspan=5, sticky="w")
        self.canvas = tk.Canvas(frame, width=400, height=300, bg="#11131a",
                                highlightthickness=1, highlightbackground="#777")
        self.canvas.grid(row=1, column=0, columnspan=5, pady=(4, 10))
        self.canvas.create_text(200, 150, fill="white",
                                text="Warte auf QEMU-Bildschirm …")
        # The packaged GPU host exposes the native game frame through QMP too.
        # Enable preview input only once a real frame has been received.
        self.canvas.bind("<ButtonPress-1>", self._touch_press)
        self.canvas.bind("<B1-Motion>", self._touch_move)
        self.canvas.bind("<ButtonRelease-1>", self._touch_release)

        for index, (caption, name) in enumerate(BUTTONS):
            row, column = divmod(index, 5)
            button = ttk.Button(frame, text=caption, width=15)
            button.grid(row=2 + row, column=column, padx=2, pady=3)
            button.bind("<ButtonPress-1>", lambda _e, key=name: self._button(key, True))
            button.bind("<ButtonRelease-1>", lambda _e, key=name: self._button(key, False))

        ttk.Button(frame, text="+1 € (MP)", width=15,
                   command=self._coin).grid(row=3, column=2, padx=2, pady=3)
        ttk.Button(frame, text="Touch kalibrieren", width=18,
                   command=self._calibrate_touch).grid(row=3, column=3, columnspan=2, padx=2, pady=3)

        ttk.Checkbutton(frame, text="Tür offen", variable=self.door_open,
                        command=self._door).grid(row=4, column=0, columnspan=2,
                                                  sticky="w", pady=(8, 2))
        ttk.Checkbutton(frame, text="Mausklicks im QEMU-Fenster als Touch",
                        variable=self.native_mouse).grid(row=4, column=2,
                                                        columnspan=3, sticky="w")
        ttk.Label(frame, text=("QEMU: Fenster M90-3dfx-0 verwenden; Vorschau wird alle 2 Sekunden aktualisiert."
                              if display_backend == "sdl" else
                              "QEMU: Reiter lower verwenden; Tasten gedrückt halten wie am Automaten.")).grid(
            row=5, column=0, columnspan=5, sticky="w")
        ttk.Label(frame, textvariable=self.status).grid(row=6, column=0,
                                                        columnspan=5, sticky="w", pady=(5, 0))
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.capture_thread = threading.Thread(target=self._capture_loop,
                                               name="qemu-lower-capture", daemon=True)
        self.capture_thread.start()
        root.after(50, self._tick)

    def _send(self, command: dict) -> bool:
        try:
            send_command(command, self.port)
            self.status.set(
                "1 € beim virtuellen MP vorgemerkt – Ergebnis im Live-Protokoll"
                if command["type"] == "coin" else
                "Touch-Kalibrierung vorgemerkt – Bestätigung im Live-Protokoll"
                if command["type"] == "touch_calibration" else
                "Datenbank verbunden – Eingabe vorgemerkt"
            )
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(f"Eingabe nicht gesendet: {exc}")
            return False

    def _calibrate_touch(self) -> None:
        if self.calibration_window is not None:
            self.calibration_window.lift()
            return
        # Release existing contacts before the modal surface intercepts input.
        self._release_pad_if_button_up(False)
        if self.qemu_touch is not None:
            x, y = self.qemu_touch
            self.qemu_touch = None
            self._send({"type": "touch", "x": x, "y": y, "down": False})
        for name in tuple(self.held_buttons):
            self._button(name, False)
        window = self.calibration_window = tk.Toplevel(self.root)
        window.title("Virtuellen Touch kalibrieren")
        window.resizable(False, False)
        window.transient(self.root)
        instruction = ttk.Label(window, text="Ziel 1 von 2 anklicken und loslassen (unten links).", padding=10)
        instruction.pack()
        canvas = tk.Canvas(window, width=800, height=600, bg="#11131a", highlightthickness=0)
        canvas.pack()
        ttk.Label(window, text="Gilt für dieses Arbeitsimage. Abbrechen behält die bisherigen Werte.", padding=8).pack()
        points: list[tuple[int, int]] = []
        contact: list[tuple[int, int]] = []
        def close():
            self.calibration_window = None
            window.grab_release()
            window.destroy()
        def draw_target():
            canvas.delete("target")
            x, y = TARGETS[len(points)]
            canvas.create_oval(x - 18, y - 18, x + 18, y + 18, outline="#ffcc33", width=2, tags="target")
            canvas.create_line(x - 30, y, x + 30, y, fill="white", tags="target")
            canvas.create_line(x, y - 30, x, y + 30, fill="white", tags="target")
        def press(event):
            if not contact: contact.append((event.x, event.y))
        def move(event):
            if contact: contact[0] = (event.x, event.y)
        def release(event):
            if not contact: return
            point = contact.pop()
            x, y = TARGETS[len(points)]
            if abs(point[0] - x) > 80 or abs(point[1] - y) > 60:
                instruction.configure(text="Bitte direkt das Fadenkreuz anklicken und loslassen.")
                return
            points.append(point)
            if len(points) == 2:
                if self._send({"type": "touch_calibration", "points": [list(p) for p in points]}):
                    close()
                else:
                    points.clear(); draw_target()
                    instruction.configure(text="Nicht verbunden. Bitte neu versuchen oder abbrechen.")
            else:
                instruction.configure(text="Ziel 2 von 2 anklicken und loslassen (oben rechts).")
                draw_target()
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", move)
        canvas.bind("<ButtonRelease-1>", release)
        ttk.Button(window, text="Abbrechen", command=close).pack(pady=8)
        window.protocol("WM_DELETE_WINDOW", close)
        window.grab_set()
        draw_target()

    def _button(self, name: str, down: bool) -> None:
        if down:
            if name in self.held_buttons:
                return
            self.held_buttons.add(name)
        else:
            self.held_buttons.discard(name)
        self._send({"type": "button", "name": name, "down": down})

    def _door(self) -> None:
        self._send({"type": "door", "open": self.door_open.get()})

    def _coin(self) -> None:
        # A coin is a single event, not a held cabinet switch.
        self._send({"type": "coin", "cents": 100})

    def _pad_point(self, event: tk.Event) -> tuple[int, int]:
        display_w, display_h = self.display_size
        return (max(0, min(TOUCH_WIDTH - 1, int(event.x * TOUCH_WIDTH / display_w))),
                max(0, min(TOUCH_HEIGHT - 1, int(event.y * TOUCH_HEIGHT / display_h))))

    def _touch_press(self, event: tk.Event) -> None:
        if getattr(self, "photo", None) is None:
            return
        if self.pad_touch is not None or self.qemu_touch is not None:
            return
        self.pad_touch = self._pad_point(event)
        self._send({"type": "touch", "x": self.pad_touch[0],
                    "y": self.pad_touch[1], "down": True})

    def _touch_move(self, event: tk.Event) -> None:
        if self.pad_touch is None:
            return
        point = self._pad_point(event)
        if point != self.pad_touch:
            self.pad_touch = point
            self._send({"type": "touch", "x": point[0], "y": point[1], "down": True})

    def _touch_release(self, event: tk.Event) -> None:
        if self.pad_touch is None:
            return
        point = self._pad_point(event)
        self.pad_touch = None
        self._send({"type": "touch", "x": point[0], "y": point[1], "down": False})

    def _release_pad_if_button_up(self, left_down: bool) -> None:
        if left_down or self.pad_touch is None:
            return
        x, y = self.pad_touch
        self.pad_touch = None
        self._send({"type": "touch", "x": x, "y": y, "down": False})

    def _poll_qemu_touch(self, left_down: bool, point: tuple[int, int] | None,
                         enabled: bool) -> None:
        enabled = enabled and getattr(self, "calibration_window", None) is None
        if self.qemu_touch is not None and (not left_down or not enabled):
            x, y = self.qemu_touch
            self.qemu_touch = None
            self._send({"type": "touch", "x": x, "y": y, "down": False})
        elif enabled and left_down and point is not None and self.pad_touch is None:
            if ((self.qemu_touch is None and not self.previous_left_down)
                    or (self.qemu_touch is not None and point != self.qemu_touch)):
                self.qemu_touch = point
                self._send({"type": "touch", "x": point[0], "y": point[1], "down": True})
        self.previous_left_down = left_down

    def _capture_loop(self) -> None:
        index = 0
        while not self.stop.is_set():
            destination = self.capture_path.with_name(
                f"{self.capture_path.stem}-{index}{self.capture_path.suffix}"
            )
            try:
                qmp_screendump(self.qmp_port, destination)
                try:
                    self.frames.put_nowait(destination)
                except queue.Full:
                    pass
                index ^= 1
            except (OSError, ValueError, RuntimeError):
                pass
            self.stop.wait(SCREEN_CAPTURE_INTERVAL_SECONDS)

    def _tick(self) -> None:
        try:
            while True:
                path = self.frames.get_nowait()
                original = tk.PhotoImage(file=str(path))
                source_w, source_h = original.width(), original.height()
                factor = max(1, (source_w + 639) // 640,
                             (source_h + 511) // 512)
                photo = original.subsample(factor, factor)
                self.photo = photo
                self.image_size = (source_w, source_h)
                self.display_size = (photo.width(), photo.height())
                self.canvas.configure(width=photo.width(), height=photo.height())
                self.canvas.delete("screen")
                self.canvas.create_image(0, 0, anchor="nw", image=photo,
                                         tags="screen")
        except queue.Empty:
            pass
        except tk.TclError as exc:
            self.status.set(f"Bildschirm nicht lesbar: {exc}")

        if os.name == "nt":
            left_down = bool(ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
            # Tk can miss ButtonRelease when the cursor leaves the preview.
            # The physical button state is an independent release fallback.
            self._release_pad_if_button_up(left_down)
            enabled = bool(self.qemu_pid and self.native_mouse.get())
            point = qemu_cursor_position(self.qemu_pid, self.image_size,
                                         self.display_backend) if enabled and left_down else None
            self._poll_qemu_touch(left_down, point, enabled)
        if not self.stop.is_set():
            self.root.after(40, self._tick)

    def _close(self) -> None:
        self.stop.set()
        if self.pad_touch is not None:
            x, y = self.pad_touch
            self.pad_touch = None
            self._send({"type": "touch", "x": x, "y": y, "down": False})
        if self.qemu_touch is not None:
            x, y = self.qemu_touch
            self.qemu_touch = None
            self._send({"type": "touch", "x": x, "y": y, "down": False})
        for name in tuple(self.held_buttons):
            self._button(name, False)
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=CONTROL_PORT)
    parser.add_argument("--qmp-port", type=int, default=4444)
    parser.add_argument("--qemu-pid", type=int, default=0)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--door-open", action="store_true")
    parser.add_argument("--display-backend", choices=("gtk", "sdl"), default="gtk")
    args = parser.parse_args()
    root = tk.Tk()
    ControlPanel(root, port=args.port, qmp_port=args.qmp_port,
                 qemu_pid=args.qemu_pid, capture_path=args.capture,
                 door_open=args.door_open, display_backend=args.display_backend)
    root.mainloop()


if __name__ == "__main__":
    main()
