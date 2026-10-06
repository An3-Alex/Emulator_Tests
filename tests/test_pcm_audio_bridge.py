import array
import io
import math
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcm_audio_bridge import (AudioBridge, HELLO, BLOCK, ACK, ADPCM_HEADER, adpcm_decode,
                              mono_pcm, validate_format)


class Output:
    def __init__(self):
        self.pending = threading.Event()
        self.queued_data = threading.Event()
        self.pending.set()
        self.data = []
        self.closed = []
        self.stopped = False

    def open(self, rate):
        return 1, .001

    def queue(self, device, data):
        self.data.append(data)
        self.queued_data.set()

    def queued(self, device):
        return 32 if self.pending.is_set() else 0

    def close(self, device):
        self.closed.append(device)

    def shutdown(self):
        self.stopped = True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.output = Output()
        self.report = io.StringIO()
        self.bridge = AudioBridge(self.output, port=0, report=self.report).start()
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        self.bridge.close()
        self.assertTrue(self.output.stopped)

    def connect(self):
        self.client = socket.create_connection(('127.0.0.1', self.bridge.port), timeout=1)
        self.client.sendall(HELLO.pack(b'M9A1', 44100, 2, 16))
        self.assertEqual(self.client.recv(4), b'OPEN')
        return self.client

    def test_waits_for_playback_and_downmixes(self):
        connection = self.connect()
        data = struct.pack('<hhhh', 30000, 30000, -30000, 30000)
        connection.sendall(BLOCK.pack(b'DATA', 7, len(data)) + data)
        self.assertTrue(self.output.queued_data.wait(1))
        connection.settimeout(.05)
        with self.assertRaises(socket.timeout):
            connection.recv(8)
        self.assertEqual(self.output.data, [struct.pack('<hh', 30000, 0)])
        self.output.pending.clear()
        connection.settimeout(1)
        self.assertEqual(connection.recv(8), ACK.pack(b'DONE', 7))

    def test_adpcm_stream_is_decoded_and_acknowledged(self):
        self.client = socket.create_connection(('127.0.0.1', self.bridge.port), timeout=1)
        self.client.sendall(HELLO.pack(b'M9A2', 44100, 1, 16))
        self.assertEqual(self.client.recv(4), b'OPEN')
        # Three samples: first stored exactly, two 4-bit codes in one byte.
        block = ADPCM_HEADER.pack(3, 1000, 0) + bytes([0x47])
        self.client.sendall(BLOCK.pack(b'DATA', 1, len(block)) + block)
        self.assertTrue(self.output.queued_data.wait(1))
        self.assertEqual(self.output.data, [adpcm_decode(block)])
        self.assertEqual(len(self.output.data[0]), 6)
        self.output.pending.clear()
        self.assertEqual(self.client.recv(8), ACK.pack(b'DONE', 1))
        self.assertIn('ima-adpcm', self.report.getvalue())

    def test_adpcm_rejects_inconsistent_block(self):
        for block in (ADPCM_HEADER.pack(4, 0, 0) + b'\0',      # 4 samples need 2 code bytes
                      ADPCM_HEADER.pack(0, 0, 0),              # empty
                      ADPCM_HEADER.pack(1, 0, 89)):            # step index out of range
            with self.assertRaises(ValueError):
                adpcm_decode(block)

    def test_guest_encoder_round_trips_through_host_decoder(self):
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('gcc')
        if not compiler:
            self.skipTest('C compiler not available')
        header = (Path(__file__).resolve().parents[1] / 'src/pcm_wave_bridge.h').read_text()
        start = header.index('static const short pcm_adpcm_steps[89]')
        end = header.index('\n}\n', header.index('static BYTE pcm_adpcm_code(')) + 3
        program = '''#include <stdio.h>
#include <math.h>
typedef unsigned char BYTE;
typedef unsigned long DWORD;
''' + header[start:end] + '''
int main(void) {
    int predictor = 0, index = 0, count = 2000, i;
    short samples[2000];
    BYTE codes[1000] = {0};
    (void)PCM_ADPCM_HEADER;
    for (i = 0; i < count; ++i) samples[i] = (short)(12000 * sin(i * 0.05) + 3000 * sin(i * 0.31));
    predictor = samples[0];
    for (i = 1; i < count; ++i) {
        BYTE code = pcm_adpcm_code(samples[i], &predictor, &index);
        if ((i - 1) & 1) codes[(i - 1) / 2] |= (BYTE)(code << 4); else codes[(i - 1) / 2] = code;
    }
    for (i = 0; i < count; ++i) printf("%d ", samples[i]);
    printf("\\n");
    for (i = 0; i < count / 2; ++i) printf("%02x", codes[i]);
    printf("\\n");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='adpcm-') as directory:
            source = Path(directory) / 'adpcm.c'
            source.write_text(program)
            binary = source.with_suffix('.exe' if os.name == 'nt' else '')
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Werror', str(source), '-o', str(binary), '-lm'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
        original_line, codes_line = result.stdout.split('\n')[:2]
        original = [int(value) for value in original_line.split()]
        block = ADPCM_HEADER.pack(len(original), original[0], 0) + bytes.fromhex(codes_line)
        decoded = array.array('h', adpcm_decode(block))
        self.assertEqual(len(decoded), len(original))
        signal = sum(value * value for value in original)
        noise = sum((a - b) ** 2 for a, b in zip(original, decoded))
        self.assertGreater(10 * math.log10(signal / noise), 25)  # dB
        self.assertLess(len(block) * 4, len(original) * 2 + 64)   # ~4x fewer UART bytes

    def test_disconnect_cancels_playback(self):
        connection = self.connect()
        connection.sendall(BLOCK.pack(b'DATA', 1, 4) + b'\1\0\1\0')
        self.assertTrue(self.output.queued_data.wait(1))
        connection.shutdown(socket.SHUT_RDWR)
        connection.close()
        self.client = None
        deadline = time.monotonic() + 1
        while not self.output.closed and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.output.closed, [1])

    def test_rejects_oversized_payload_before_allocation(self):
        connection = self.connect()
        connection.sendall(BLOCK.pack(b'DATA', 1, 0xFFFFFFFF))
        try:
            self.assertEqual(connection.recv(1), b'')
        except ConnectionResetError:
            pass  # Windows may reset a rejected packet with unread payload.
        self.assertEqual(self.output.data, [])

    def test_stop_cancels_blocked_read(self):
        self.connect()
        self.bridge.close()
        self.assertFalse(self.bridge.workers)
        self.assertTrue(self.output.stopped)

    def test_fragmented_header(self):
        self.client = socket.create_connection(('127.0.0.1', self.bridge.port), timeout=1)
        for byte in HELLO.pack(b'M9A1', 44100, 1, 16):
            self.client.sendall(bytes([byte]))
        self.assertEqual(self.client.recv(4), b'OPEN')

    def test_unknown_protocol_closes_connection(self):
        self.client = socket.create_connection(('127.0.0.1', self.bridge.port), timeout=1)
        self.client.sendall(HELLO.pack(b'BAD!', 44100, 1, 16))
        self.assertEqual(self.client.recv(1), b'')

    def test_serial_session_can_close_and_reopen_on_same_connection(self):
        connection = self.connect()
        connection.sendall(BLOCK.pack(b'END!', 0, 0))
        self.assertEqual(connection.recv(4), b'SHUT')
        self.assertEqual(self.output.closed, [1])
        connection.sendall(HELLO.pack(b'M9A1', 44100, 1, 16))
        self.assertEqual(connection.recv(4), b'OPEN')
        self.output.pending.clear()
        connection.sendall(BLOCK.pack(b'DATA', 1, 2) + b'\1\0')
        self.assertEqual(connection.recv(8), ACK.pack(b'DONE', 1))

    def test_zero_packet_needs_no_payload_but_waits_for_real_playback(self):
        connection = self.connect()
        connection.sendall(BLOCK.pack(b'ZERO', 1, 512))
        self.assertTrue(self.output.queued_data.wait(1))
        self.assertEqual(self.output.data, [bytes(256)])  # stereo -> mono
        connection.settimeout(.03)
        with self.assertRaises(socket.timeout):
            connection.recv(8)
        self.output.pending.clear()
        connection.settimeout(1)
        self.assertEqual(connection.recv(8), ACK.pack(b'DONE', 1))
        deadline = time.monotonic() + 1
        while '"wire_bytes": 12' not in self.report.getvalue() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertIn('"wire_bytes": 12', self.report.getvalue())

    def test_oversized_zero_packet_is_rejected_before_expansion(self):
        connection = self.connect()
        connection.sendall(BLOCK.pack(b'ZERO', 1, 44100 * 4 * 2 + 4))
        self.assertEqual(connection.recv(1), b'')
        self.assertEqual(self.output.data, [])

    def test_receives_ahead_without_waiting_for_first_buffer_to_play(self):
        connection = self.connect()
        packet = b'\1\0\1\0'
        connection.sendall(BLOCK.pack(b'DATA', 1, 4) + packet + BLOCK.pack(b'DATA', 2, 4) + packet)
        deadline = time.monotonic() + 1
        while len(self.output.data) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(len(self.output.data), 2)
        connection.settimeout(.03)
        with self.assertRaises(socket.timeout):
            connection.recv(8)
        connection.sendall(BLOCK.pack(b'END!', 0, 0))
        connection.settimeout(1)
        self.assertEqual(connection.recv(4), b'SHUT')
        self.assertEqual(self.output.closed, [1])

    def test_repeated_sequence_is_rejected(self):
        connection = self.connect()
        packet = BLOCK.pack(b'DATA', 1, 4) + b'\1\0\1\0'
        connection.sendall(packet + packet)
        try:
            self.assertEqual(connection.recv(1), b'')
        except ConnectionResetError:
            pass

    def test_bind_failure_releases_audio_backend(self):
        output = Output()
        value = AudioBridge(output, port=self.bridge.port)
        with self.assertRaises(OSError):
            value.start()
        self.assertTrue(output.stopped)
        value.close()


class FormatTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(validate_format(44100, 2, 16), 4)
        for values in [(1, 2, 16), (44100, 6, 16), (44100, 2, 8)]:
            with self.assertRaises(ValueError):
                validate_format(*values)

    def test_downmix_without_overflow(self):
        data = struct.pack('<hhhh', -32768, -32768, 32767, 32767)
        self.assertEqual(mono_pcm(data, 2), struct.pack('<hh', -32768, 32767))
        self.assertEqual(mono_pcm(b'\1\0', 1), b'\1\0')
        with self.assertRaises(ValueError):
            mono_pcm(b'\0', 2)

if __name__ == '__main__':
    unittest.main()
