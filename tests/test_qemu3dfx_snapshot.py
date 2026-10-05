from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qemu3dfx_modern_port import (
    GPU_SNAPSHOT, PASSTHROUGH_STATE, capture_gpu_present, capture_qmp_gpu,
)
from cabinet_control_panel import ControlPanel


class SnapshotTests(unittest.TestCase):
    def test_compiled_readback_preserves_gl_state_and_flips_exact_pixels(self):
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('cc') or shutil.which('gcc')
        if not compiler:
            self.skipTest('C compiler not available')
        fixture = Path(__file__).with_name('fixtures') / 'gpu_snapshot_mock.c'
        with tempfile.TemporaryDirectory(prefix='gpu-snapshot-') as directory:
            root = Path(directory)
            source = root / 'snapshot.c'
            source.write_text(fixture.read_text().replace('SNAPSHOT_FUNCTION', GPU_SNAPSHOT))
            binary = root / ('snapshot.exe' if os.name == 'nt' else 'snapshot')
            result = subprocess.run([compiler, '-std=c11', str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('GPU_SNAPSHOT_STATE_AND_PIXELS_OK', result.stdout)

    def test_native_snapshot_precedes_scaling_and_swap(self):
        fixed = capture_gpu_present('''int MGLSwapBuffers(void)
{
    MesaBlitScale();
    SDL_GL_SwapWindow(window);
}
''')
        swap = fixed.split('int MGLSwapBuffers(void)', 1)[1]
        self.assertLess(swap.index('m90_gpu_snapshot();'), swap.index('MesaBlitScale();'))
        self.assertLess(swap.index('MesaBlitScale();'), swap.index('SDL_GL_SwapWindow'))

    def test_readback_is_on_demand_and_preserves_pixel_pack_and_read_state(self):
        self.assertLess(GPU_SNAPSHOT.index('!qemu_console_gpu_snapshot_requested'),
                        GPU_SNAPSHOT.index('pixman_image_create_bits'))
        for state in ('GL_READ_FRAMEBUFFER_BINDING', 'GL_READ_BUFFER',
                      'GL_PIXEL_PACK_BUFFER_BINDING', 'GL_PACK_ALIGNMENT',
                      'GL_PACK_ROW_LENGTH', 'GL_PACK_SKIP_ROWS', 'GL_PACK_SKIP_PIXELS'):
            self.assertIn(state, GPU_SNAPSHOT)
        self.assertIn('glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo)', GPU_SNAPSHOT)
        self.assertIn('glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer)', GPU_SNAPSHOT)
        self.assertIn('glReadBuffer(read_buffer)', GPU_SNAPSHOT)
        self.assertIn('glPixelStorei(pack_names[i], pack[i])', GPU_SNAPSHOT)
        self.assertIn('(height - 1 - y) * stride', GPU_SNAPSHOT)
        self.assertIn('width > 4096', GPU_SNAPSHOT)

    def test_qmp_does_not_fall_back_to_stale_qxl_during_gpu_present(self):
        fixed = capture_qmp_gpu('''qmp_screendump(void) {
    surface = qemu_console_surface(con);
    if (!surface) { return; }
    image = pixman_image_ref(surface->image);
    object_unref(con);
}
''')
        gpu, qxl = fixed.split('    } else {', 1)
        self.assertIn('GPU frame pending; retry screendump', gpu)
        self.assertIn('return;', gpu)
        self.assertNotIn('qemu_console_surface', gpu)
        self.assertIn('qemu_console_surface', qxl)
        self.assertIn('pixman_image_ref(con->gpu_snapshot)', PASSTHROUGH_STATE)
        self.assertIn('con->gpu_snapshot = NULL', PASSTHROUGH_STATE)

    def test_click_before_first_frame_cannot_inject_touch(self):
        panel = ControlPanel.__new__(ControlPanel)
        panel.photo = None
        panel._send = lambda _: self.fail('Touch sent without a frame')
        panel._touch_press(object())

    def test_unrecognized_gpu_source_is_refused(self):
        with self.assertRaises(ValueError):
            capture_gpu_present('unknown revision')


if __name__ == '__main__':
    unittest.main()
