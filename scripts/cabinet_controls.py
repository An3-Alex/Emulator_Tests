"""Local cabinet input protocol and the original 3M touch wire format."""

from __future__ import annotations

import json
import queue
import socket
import threading
from typing import Any


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 4554
TOUCH_WIDTH = 800
TOUCH_HEIGHT = 600

# IDs are from the owner's key-name table at 0x5B8CA..0x5B937.  The
# per-cabinet switch locations are in 16-word rows at 0x5B942.
KEY_IDS = {
    "menu": 2,
    "autostart": 3,
    "einsatz": 4,
    "maxeinsatz": 5,
    "start": 6,
    "auszahlung": 9,  # Rueckgabe
    "service": 11,    # Schlossschalter / service switch
}
KEY_TABLE_BASE = 0x0005B942
KEY_TABLE_STRIDE = 0x20
KEY_TABLE_PROFILES = 13
KEY_CURRENT_BASE = 0x001E247B
KEY_EVENT_BASE = 0x001E2503


def validate_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("command must be a JSON object")
    kind = value.get("type")
    if kind == "ping" and set(value) == {"type"}:
        return {"type": "ping"}
    if kind == "door" and set(value) == {"type", "open"}:
        if type(value["open"]) is not bool:
            raise ValueError("door.open must be boolean")
        return {"type": "door", "open": value["open"]}
    if kind == "button" and set(value) == {"type", "name", "down"}:
        if value["name"] not in KEY_IDS or type(value["down"]) is not bool:
            raise ValueError("invalid button command")
        return {"type": "button", "name": value["name"], "down": value["down"]}
    if kind == "touch" and set(value) == {"type", "x", "y", "down"}:
        x, y, down = value["x"], value["y"], value["down"]
        if (type(x) is not int or type(y) is not int or type(down) is not bool
                or not 0 <= x < TOUCH_WIDTH or not 0 <= y < TOUCH_HEIGHT):
            raise ValueError("invalid touch coordinates or state")
        return {"type": "touch", "x": x, "y": y, "down": down}
    raise ValueError("unsupported cabinet command")


def format_tablet_packet(x: int, y: int, down: bool) -> bytes:
    """3M Format Tablet: status, low/high 7-bit X, low/high 7-bit Y."""
    if (type(x) is not int or type(y) is not int or type(down) is not bool
            or not 0 <= x < TOUCH_WIDTH or not 0 <= y < TOUCH_HEIGHT):
        raise ValueError("touch point outside lower cabinet display")
    raw_x = round(x * 16383 / (TOUCH_WIDTH - 1))
    raw_y = round(y * 16383 / (TOUCH_HEIGHT - 1))
    return bytes((0xC0 if down else 0x80,
                  raw_x & 0x7F, (raw_x >> 7) & 0x7F,
                  raw_y & 0x7F, (raw_y >> 7) & 0x7F))


def key_location(mapping: int) -> tuple[int, int, int] | None:
    """Resolve a firmware mapping word into current/event bytes and bit."""
    mask, offset = mapping >> 8, mapping & 0xFF
    if mask == 0 or mask & (mask - 1) or offset >= 0x80:
        return None
    return KEY_CURRENT_BASE + offset, KEY_EVENT_BASE + offset, mask


class CabinetControlServer:
    """Loopback-only JSON-line ingress; the bridge applies events on its CPU thread."""

    def __init__(self, port: int = CONTROL_PORT) -> None:
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((CONTROL_HOST, port))
        self._socket.listen(4)
        self._socket.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, name="cabinet-controls", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(1.0)
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) <= 512:
                        chunk = client.recv(512)
                        if not chunk:
                            break
                        data.extend(chunk)
                    if b"\n" not in data or len(data) > 512:
                        raise ValueError("missing or oversized JSON line")
                    event = validate_command(json.loads(data.split(b"\n", 1)[0]))
                    if event["type"] != "ping":
                        self.events.put_nowait(event)
                    reply = {"ok": True}
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError, queue.Full, socket.timeout) as exc:
                    reply = {"ok": False, "error": str(exc)}
                try:
                    client.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=1.0)


def send_command(command: dict[str, Any], port: int = CONTROL_PORT) -> dict[str, Any]:
    command = validate_command(command)
    with socket.create_connection((CONTROL_HOST, port), timeout=1.0) as sock:
        sock.settimeout(1.0)
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))
        data = sock.recv(512)
    if not data:
        raise ConnectionError("cabinet controller closed without a reply")
    reply = json.loads(data.split(b"\n", 1)[0])
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "cabinet control rejected"))
    return reply
