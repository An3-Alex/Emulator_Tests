from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from qemu3dfx_modern_port import GPU_REVISION, pinned_file, capture_gpu_present, GPU_SNAPSHOT
from qemu3dfx_dual_output import patch_guest, patch_wgl, patch_transport, patch_sdl, patch_blit


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class DualOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        checkout = Path(__file__).resolve().parents[2] / '.tools/qemu-3dfx'
        if not (checkout / '.git').exists():
            raise unittest.SkipTest('Pinned upstream checkout not installed')
        cls.wgl = patch_wgl(pinned_file(checkout, GPU_REVISION, 'qemu-1/hw/mesa/mglcntx_mingw.c'))
        cls.guest = patch_guest(pinned_file(checkout, GPU_REVISION, 'wrappers/mesa/src/wrapgl32.c'))
        cls.transport = patch_transport(pinned_file(checkout, GPU_REVISION, 'qemu-1/hw/mesa/mesapt_mm.c'))
        cls.blit = patch_blit(pinned_file(checkout, GPU_REVISION, 'qemu-1/hw/mesa/mesagl_blit.c'))
        cls.definitions = pinned_file(checkout, GPU_REVISION, 'qemu-1/hw/mesa/mglfuncs.h')

    def test_six_protocol_slots_fit_both_base_contexts(self):
        magic = int(re.search(r'#define MESAGL_MAGIC\s+(0x[0-9a-f]+)', self.definitions)[1], 16)
        self.assertEqual((magic & 15) + 1, 6)
        self.assertIn('mesa_current_output() * 3', self.wgl)
        self.assertNotIn('mesa_current_output() * 8', self.wgl)
        for slot in (0, 3):
            self.assertEqual((magic - slot) & ~15, magic & ~15)

    def test_old_fifo_is_drained_before_selecting_another_output(self):
        selector = self.transport.split('if (addr == 0xFB4 && val < 2)', 1)[1].split('if (addr == 0xFF4', 1)[0]
        self.assertLess(selector.index('processFifo(s)'), selector.index('mesa_select_output(val)'))
        self.assertIn('cntxRC[1] = mesa_current_output() * 3', self.transport)
        self.assertIn('One context release must not shut down the other monitor', self.transport)

    def test_guest_routes_monitor_and_keeps_actual_context_handle(self):
        self.assertIn('MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST)', self.guest)
        self.assertIn('MONITORINFOF_PRIMARY) ? 0 : 1', self.guest)
        self.assertIn('if (output != previous)', self.guest)
        self.assertIn('currGLRC = arg1;', self.guest)
        self.assertIn('return currGLRC;', self.guest)
        self.assertIn('level = argsp[1];', self.guest)
        self.assertIn('m90_configured & (1U << slot)', self.guest)
        self.assertEqual(patch_guest(self.guest), self.guest)

    def test_each_output_has_private_scaler_and_window_callback(self):
        self.assertIn('output_blit[2]', self.blit)
        self.assertIn('blit output_blit[mesa_current_output()]', self.blit)
        self.assertIn('output_ready[output]', self.wgl)
        self.assertIn('output_timers[2]', self.wgl)
        self.assertIn('if (opaque) { mesa_select_output((uintptr_t)opaque - 1); }', self.wgl)
        self.assertIn('output_dc[output] = GetDC(output_hwnd[output])', self.wgl)
        self.assertIn('mesa_current_output()', GPU_SNAPSHOT)

    def test_blit_uses_canonical_console_prototype(self):
        # ui/console.h declares int mesa_gui_fullscreen(int *); a file-local
        # const void * declaration is a hard "conflicting types" error.
        self.assertIn('#include "ui/console.h"', self.blit)
        self.assertNotRegex(self.blit, r'\bint\s+mesa_gui_fullscreen\s*\(')

    def test_snapshot_resolves_every_gl_call_through_transport_table(self):
        # mglcntx_mingw.c has no GL prototypes/import library; a bare call is
        # an implicit declaration, which QEMU builds treat as an error.
        unresolved = re.sub(r'PFN_CALL\(gl\w+\(|MESA_PFN\([^)]*\)', '', GPU_SNAPSHOT)
        self.assertNotRegex(unresolved, r'(?<![\w])gl[A-Z]\w*\s*\(')

    def test_windows_capture_precedes_scale_and_swap(self):
        fixed = capture_gpu_present(self.wgl)
        swap = function(fixed, 'int MGLSwapBuffers(void)')
        self.assertLess(swap.index('m90_gpu_snapshot();'), swap.index('MesaBlitScale();'))
        self.assertLess(swap.index('MesaBlitScale();'), swap.index('SwapBuffers(hDC)'))
        self.assertIn('last_by_output[2]', swap)

    def test_compiled_contexts_are_independent_and_release_is_scoped(self):
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('gcc')
        if not compiler:
            self.skipTest('C compiler not available')
        code = (Path(__file__).with_name('fixtures') / 'gpu_dual_context_mock.c').read_text()
        functions = '\n'.join(function(self.wgl, signature) for signature in (
            'int MGLCreateContext(uint32_t gDC)', 'int MGLMakeCurrent(uint32_t cntxRC, int level)',
            'void MGLDeleteContext(int level)', 'int DrawableContext(void)'))
        with tempfile.TemporaryDirectory(prefix='gpu-dual-') as directory:
            source = Path(directory) / 'dual.c'
            source.write_text(code.replace('CONTEXT_FUNCTIONS', functions))
            binary = source.with_suffix('.exe' if os.name == 'nt' else '')
            result = subprocess.run([compiler, '-std=c11', '-Wall', str(source), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('DUAL_GPU_CONTEXTS_OK', result.stdout)


class SdlRoutingTests(unittest.TestCase):
    def test_both_callbacks_fire_and_second_window_gets_its_own_context(self):
        source = '''} scon_cb;
    s->scon = &sdl2_console[0];
    s->opaque = 0;
    s->cwnd_fn = (void (*)(void *, void *, void *))cwnd_fn;
    if (!SDL_GetHint(SDL_HINT_RENDER_DRIVER) || x) {}
    s->scon->winctx = SDL_GL_GetCurrentContext();
    if (!s->scon->winctx)
        s->scon->winctx = SDL_GL_CreateContext(s->scon->real_window);
    if (!s->opaque)
        s->cwnd_fn(s->scon->real_window, s->hnwnd, s->opaque);
'''
        fixed = patch_sdl(source)
        self.assertIn('scon_cbs[2]', fixed)
        self.assertIn('sdl2_console[m90_output]', fixed)
        self.assertIn('if (s->cwnd_fn)', fixed)
        self.assertNotIn('SDL_GL_GetCurrentContext()', fixed)
        self.assertIn('SDL_WINDOW_OPENGL', fixed)


if __name__ == '__main__':
    unittest.main()
