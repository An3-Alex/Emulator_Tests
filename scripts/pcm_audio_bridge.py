"""Bounded, loopback-only PCM output for the XP irrKlang bridge.

Wire protocol (little endian): M9A1/rate/channels/bits, then DATA/sequence/size
and PCM bytes. OPEN and DONE/sequence acknowledge device open and playback.
M9A2 announces mono IMA ADPCM DATA blocks (see adpcm_decode) instead of PCM.
Disconnect cancels queued output; no guest-supplied paths or commands exist.
"""
from __future__ import annotations

import argparse
import array
import ctypes as C
from collections import deque
import json
from pathlib import Path
import select
import socket
import struct
import sys
import threading
import time

HELLO = struct.Struct('<4sIII')
BLOCK = struct.Struct('<4sII')
ACK = struct.Struct('<4sI')
MAX_STREAMS = 4
HOST_PORT = 4765


def validate_format(rate: int, channels: int, bits: int) -> int:
    if not 8000 <= rate <= 96000 or channels not in (1, 2) or bits != 16:
        raise ValueError('Unsupported PCM format')
    return channels * 2


def mono_pcm(data: bytes, channels: int) -> bytes:
    if channels not in (1, 2) or len(data) % (channels * 2):
        raise ValueError('Incomplete PCM frame')
    if channels == 1:
        return data
    values = array.array('h')
    values.frombytes(data)
    if sys.byteorder != 'little':
        values.byteswap()
    mono = array.array('h', ((values[i] + values[i + 1]) // 2
                            for i in range(0, len(values), 2)))
    if sys.byteorder != 'little':
        mono.byteswap()
    return mono.tobytes()


ADPCM_HEADER = struct.Struct('<IhBx')
ADPCM_STEPS = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767)
ADPCM_INDEX_CHANGE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


def adpcm_decode(block: bytes) -> bytes:
    """Decode one guest block: u32 count, s16 first sample, u8 index, u8 0,
    then 4-bit codes of samples 1..count-1, low nibble first."""
    if len(block) < ADPCM_HEADER.size:
        raise ValueError('Short ADPCM block')
    count, predictor, index = ADPCM_HEADER.unpack_from(block)
    if not count or index > 88 or len(block) != ADPCM_HEADER.size + count // 2:
        raise ValueError('Invalid ADPCM block')
    steps, changes = ADPCM_STEPS, ADPCM_INDEX_CHANGE
    out = array.array('h', [predictor])
    append = out.append
    remaining = count - 1
    for byte in memoryview(block)[ADPCM_HEADER.size:]:
        for code in (byte & 15, byte >> 4):
            if not remaining:
                break
            remaining -= 1
            step = steps[index]
            delta = step >> 3
            if code & 4:
                delta += step
            if code & 2:
                delta += step >> 1
            if code & 1:
                delta += step >> 2
            predictor = predictor - delta if code & 8 else predictor + delta
            predictor = -32768 if predictor < -32768 else 32767 if predictor > 32767 else predictor
            index += changes[code]
            index = 0 if index < 0 else 88 if index > 88 else index
            append(predictor)
    if sys.byteorder != 'little':
        out.byteswap()
    return out.tobytes()


def recv_exact(connection: socket.socket, length: int, stop: threading.Event) -> bytes:
    result = bytearray()
    while len(result) < length and not stop.is_set():
        try:
            chunk = connection.recv(length - len(result))
        except socket.timeout:
            continue
        if not chunk:
            raise EOFError('Audio connection closed')
        result.extend(chunk)
    if len(result) != length:
        raise EOFError('Audio bridge stopped')
    return bytes(result)


class AudioSpec(C.Structure):
    _fields_ = [('freq', C.c_int), ('format', C.c_uint16),
                ('channels', C.c_uint8), ('silence', C.c_uint8),
                ('samples', C.c_uint16), ('padding', C.c_uint16),
                ('size', C.c_uint32), ('callback', C.c_void_p), ('userdata', C.c_void_p)]


class SDLOutput:
    def __init__(self, dll: Path):
        # QEMU ships SDL2 already; do not search the working directory/PATH.
        self.dll = C.CDLL(str(dll.resolve(strict=True)))
        signatures = {
            'SDL_InitSubSystem': ([C.c_uint32], C.c_int),
            'SDL_QuitSubSystem': ([C.c_uint32], None),
            'SDL_GetError': ([], C.c_char_p),
            'SDL_OpenAudioDevice': ([C.c_char_p, C.c_int, C.POINTER(AudioSpec), C.POINTER(AudioSpec), C.c_int], C.c_uint32),
            'SDL_CloseAudioDevice': ([C.c_uint32], None),
            'SDL_PauseAudioDevice': ([C.c_uint32, C.c_int], None),
            'SDL_QueueAudio': ([C.c_uint32, C.c_void_p, C.c_uint32], C.c_int),
            'SDL_GetQueuedAudioSize': ([C.c_uint32], C.c_uint32),
            'SDL_ClearQueuedAudio': ([C.c_uint32], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result
        if self.dll.SDL_InitSubSystem(0x10):
            raise RuntimeError(self.error())

    def error(self):
        return (self.dll.SDL_GetError() or b'SDL audio error').decode('utf-8', 'replace')

    def open(self, rate):
        desired = AudioSpec(freq=rate, format=0x8010, channels=1, samples=512)
        obtained = AudioSpec()
        device = self.dll.SDL_OpenAudioDevice(None, 0, C.byref(desired), C.byref(obtained), 0)
        if not device:
            raise RuntimeError(self.error())
        if (obtained.freq, obtained.format, obtained.channels) != (rate, 0x8010, 1):
            self.dll.SDL_CloseAudioDevice(device)
            raise RuntimeError('SDL output changed the required PCM format')
        self.dll.SDL_PauseAudioDevice(device, 0)
        return device, obtained.samples / rate

    def queue(self, device, data):
        buffer = C.create_string_buffer(data)
        if self.dll.SDL_QueueAudio(device, buffer, len(data)):
            raise RuntimeError(self.error())

    def queued(self, device):
        return self.dll.SDL_GetQueuedAudioSize(device)

    def close(self, device):
        self.dll.SDL_ClearQueuedAudio(device)
        self.dll.SDL_CloseAudioDevice(device)

    def shutdown(self):
        self.dll.SDL_QuitSubSystem(0x10)


class SilentOutput:
    """Mute host output without instantly completing guest audio buffers."""
    def open(self, rate):
        return {'rate': rate, 'until': 0.0}, 0.0

    def queue(self, device, data):
        device['until'] = max(device['until'], time.monotonic()) + len(data) / (device['rate'] * 2)

    def queued(self, device):
        return max(0, int((device['until'] - time.monotonic()) * device['rate'] * 2))

    def close(self, device):
        device['until'] = 0.0

    def shutdown(self):
        pass


class AudioBridge:
    def __init__(self, output, *, port=HOST_PORT, report=None):
        self.output, self.port, self.report = output, port, report
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.workers = set()
        self.listener = None
        self.thread = None
        self.stream_number = 0
        self.closed = False

    def log(self, event, **values):
        line = json.dumps({'event': event, 'time': time.monotonic(), **values})
        with self.lock:
            if self.report:
                self.report.write(line + '\n')
                self.report.flush()

    def start(self):
        listener = socket.socket()
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', self.port))
            listener.listen(MAX_STREAMS)
            listener.settimeout(.25)
        except BaseException:
            listener.close()
            self.output.shutdown()
            self.closed = True
            raise
        self.listener = listener
        self.port = listener.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, name='pcm-listener')
        self.thread.start()
        self.log('AUDIO_BRIDGE_READY', port=self.port)
        return self

    def _accept(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(.25)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                if self.stop.is_set() or len(self.connections) >= MAX_STREAMS:
                    connection.close()
                    continue
                self.connections.add(connection)
                self.stream_number += 1
                number = self.stream_number
                worker = threading.Thread(target=self._stream, args=(connection, number), name=f'pcm-{number}')
                self.workers.add(worker)
            worker.start()

    def _stream(self, connection, number):
        try:
            while not self.stop.is_set():
                self._session(connection, number)
        except (OSError, EOFError, ValueError, RuntimeError) as exc:
            self.log('AUDIO_STREAM_END', stream=number, reason=str(exc))
        finally:
            connection.close()
            with self.lock:
                self.connections.discard(connection)
                self.workers.discard(threading.current_thread())

    def _session(self, connection, number):
        device = None
        try:
            magic, rate, channels, bits = HELLO.unpack(recv_exact(connection, HELLO.size, self.stop))
            if magic not in (b'M9A1', b'M9A2'):
                raise ValueError('Unsupported audio protocol')
            adpcm = magic == b'M9A2'
            if adpcm and channels != 1:
                raise ValueError('ADPCM stream must be mono')
            align = validate_format(rate, channels, bits)
            device, latency = self.output.open(rate)
            self.log('AUDIO_STREAM_OPEN', stream=number, rate=rate, channels=channels, bits=bits,
                     codec='ima-adpcm' if adpcm else 'pcm')
            connection.sendall(b'OPEN')
            pending = deque()
            submitted = 0
            last_sequence = 0
            while not self.stop.is_set():
                readable, _, _ = select.select([connection], [], [], .002)
                if readable:
                    magic, sequence, size = BLOCK.unpack(recv_exact(connection, BLOCK.size, self.stop))
                    if magic == b'END!' and sequence == 0 and size == 0:
                        self.output.close(device)
                        device = None
                        connection.sendall(b'SHUT')
                        self.log('AUDIO_SESSION_CLOSED', stream=number, cancelled_blocks=len(pending))
                        return
                    if adpcm:
                        # Header plus at most two seconds of 4-bit codes.
                        invalid = magic != b'DATA' or not ADPCM_HEADER.size <= size <= ADPCM_HEADER.size + rate
                    else:
                        invalid = (magic not in (b'DATA', b'ZERO') or not size or size % align
                                   or size > rate * align * 2)
                    if invalid or sequence <= last_sequence or len(pending) >= 32:
                        raise ValueError('Invalid, oversized or out-of-order audio block')
                    compressed_silence = magic == b'ZERO'
                    if adpcm:
                        data = adpcm_decode(recv_exact(connection, size, self.stop))
                    elif compressed_silence:
                        data = bytes(size // channels)
                    else:
                        data = mono_pcm(recv_exact(connection, size, self.stop), channels)
                    if self.output.queued(device) + len(data) > rate * 2 * 3:
                        raise ValueError('Audio queue exceeds three seconds')
                    samples = array.array('h')
                    samples.frombytes(data)
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    self.output.queue(device, data)
                    submitted += len(data)
                    last_sequence = sequence
                    pending.append([sequence, len(data), submitted, time.monotonic(), None,
                                    sum(value != 0 for value in samples), max(map(abs, samples), default=0),
                                    BLOCK.size if compressed_silence else BLOCK.size + size])
                played = submitted - self.output.queued(device)
                now = time.monotonic()
                for item in pending:
                    if played >= item[2] and item[4] is None:
                        item[4] = now
                while pending and pending[0][4] is not None and now - pending[0][4] >= latency:
                    sequence, size, _, started, _, nonzero, peak, wire_bytes = pending.popleft()
                    connection.sendall(ACK.pack(b'DONE', sequence))
                    self.log('AUDIO_BLOCK_PLAYED', stream=number, sequence=sequence, bytes=size,
                             seconds=now - started, nonzero_samples=nonzero, peak=peak, wire_bytes=wire_bytes)
                if pending and now - pending[0][3] > 8:
                    raise TimeoutError('Windows audio output did not drain')
        finally:
            if device is not None:
                self.output.close(device)

    def close(self):
        if self.closed:
            return
        self.stop.set()
        if self.listener:
            self.listener.close()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread:
            self.thread.join(2)
        with self.lock:
            workers = list(self.workers)
        deadline = time.monotonic() + 5
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Audio workers did not stop; SDL remains allocated')
        self.log('AUDIO_BRIDGE_STOPPED')
        self.output.shutdown()
        self.closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdl', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--muted', action='store_true')
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('a', encoding='utf-8') as report:
        output = SilentOutput() if args.muted else SDLOutput(args.sdl)
        bridge = AudioBridge(output, report=report)
        try:
            with bridge:
                if args.ready_file:
                    args.ready_file.write_text('ready\n', encoding='ascii')
                print('AUDIO_BRIDGE_READY', flush=True)
                while sys.stdin.buffer.read(1):
                    pass
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
