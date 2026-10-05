"""Open and close QEMU's SDL playback device silently, without any VM/image.

Usage: python tests/sdl_playback_probe.py "C:/Program Files/qemu/SDL2.dll"
The output device remains paused. No capture device is opened.
"""
import ctypes as c
import json
import os
from pathlib import Path
import sys


class AudioSpec(c.Structure):
    _fields_ = [("freq", c.c_int), ("format", c.c_uint16), ("channels", c.c_uint8),
                ("silence", c.c_uint8), ("samples", c.c_uint16),
                ("padding", c.c_uint16), ("size", c.c_uint32),
                ("callback", c.c_void_p), ("userdata", c.c_void_p)]


def probe(path, channels=2):
    if channels not in (1, 2):
        raise ValueError("Playback channels must be 1 or 2")
    path = Path(path).resolve(strict=True)
    with os.add_dll_directory(str(path.parent)):
        sdl = c.CDLL(str(path))
        sdl.SDL_InitSubSystem.argtypes = [c.c_uint32]
        sdl.SDL_InitSubSystem.restype = c.c_int
        sdl.SDL_QuitSubSystem.argtypes = [c.c_uint32]
        sdl.SDL_GetError.restype = c.c_char_p
        sdl.SDL_GetCurrentAudioDriver.restype = c.c_char_p
        sdl.SDL_GetNumAudioDevices.argtypes = [c.c_int]
        sdl.SDL_GetNumAudioDevices.restype = c.c_int
        sdl.SDL_OpenAudioDevice.argtypes = [c.c_char_p, c.c_int,
            c.POINTER(AudioSpec), c.POINTER(AudioSpec), c.c_int]
        sdl.SDL_OpenAudioDevice.restype = c.c_uint32
        sdl.SDL_CloseAudioDevice.argtypes = [c.c_uint32]
        if sdl.SDL_InitSubSystem(0x10):
            raise RuntimeError(sdl.SDL_GetError().decode(errors="replace"))
        device = 0
        try:
            wanted = AudioSpec(freq=44100, format=0x8010, channels=channels, samples=512)
            obtained = AudioSpec()
            # capture=0. SDL opens devices paused; no sample data is submitted.
            device = sdl.SDL_OpenAudioDevice(None, 0, c.byref(wanted), c.byref(obtained), 0)
            if not device:
                raise RuntimeError(sdl.SDL_GetError().decode(errors="replace"))
            print(json.dumps({"playback_opened": True, "capture_opened": False,
                "driver": sdl.SDL_GetCurrentAudioDriver().decode(),
                "capture_devices": sdl.SDL_GetNumAudioDevices(1),
                "frequency": obtained.freq, "channels": obtained.channels,
                "vm_started": False}))
        finally:
            if device:
                sdl.SDL_CloseAudioDevice(device)
            sdl.SDL_QuitSubSystem(0x10)


if __name__ == "__main__":
    probe(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 2)
