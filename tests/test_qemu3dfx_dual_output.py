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
from qemu3dfx_dual_output import patch_guest, patch_wgl, patch_transport, patch_sdl, patch_blit, patch_slots


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

    def test_sixteen_protocol_slots_fit_both_base_contexts(self):
        # WineD3D needs one context per swapchain and rendering thread.
        definitions = patch_slots(self.definitions)
        self.assertEqual(patch_slots(definitions), definitions)
        magic = int(re.search(r'#define MESAGL_MAGIC\s+(0x[0-9a-f]+)', definitions)[1], 16)
        self.assertEqual((magic & 15) + 1, 16)
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

    def test_client_state_follows_the_current_context_on_both_sides(self):
        # Both displays alternate contexts every frame. One binding tracker per
        # device made buffer offsets of one context read as guest memory.
        write = function(self.transport, 'static void mesapt_write(void *opaque, hwaddr addr, uint64_t val, unsigned size)\n{')
        for call in ('s->mglCntxCurrent = MGLMakeCurrent(ptVer[0], level)', '                        MGLMakeCurrent(ptVer[0], level);'):
            before = write[:write.index(call)]
            self.assertTrue(before.rstrip().endswith('m90_client_switch(s, ptVer[0], level);'), call)
        self.assertLess(write.index('m90_client_created(s, argsp[1]);'), write.index('ContextCreateCommon(s);\n                        }\n                        DPRINTF_COND'))
        self.assertIn('m90_client_created(s, cntxRC[1]);', write)
        self.assertIn('(0 == NumPbuffer()) && (level % 3))', write)
        host_switch = function(self.guest, 'static void m90_host_switch(uint32_t dc, uint32_t rc)\n{')
        self.assertIn('if (rc) { m90_client_switch(MESAGL_MAGIC - rc); }', host_switch)
        self.assertIn('m90_client_created(level);', function(self.guest, 'wglCreateContextAttribsARB(HDC hDC,'))
        self.assertIn('m90_client_created(cntxDC[1]);', function(self.guest, 'uint32_t PT_CALL COMPACT\nmglCreateContext (uint32_t arg0)'))
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('gcc')
        if not compiler:
            self.skipTest('C compiler not available')
        start = self.transport.index('typedef struct {\n    vtxarry_t Color, EdgeFlag, Normal, Index')
        end = self.transport.index('static void mesapt_write(')
        code = (Path(__file__).with_name('fixtures') / 'host_client_state_mock.c').read_text()
        with tempfile.TemporaryDirectory(prefix='gpu-client-') as directory:
            source = Path(directory) / 'client.c'
            source.write_text(code.replace('/*M90_HOST_CODE*/', self.transport[start:end]))
            binary = source.with_suffix('.exe' if os.name == 'nt' else '')
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Werror', str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('HOST_CLIENT_STATE_OK', result.stdout)

    def test_only_the_lower_display_waits_for_vblank(self):
        make_current = function(self.wgl, 'int MGLMakeCurrent(uint32_t cntxRC, int level)')
        self.assertIn('if (mesa_current_output() && wglFuncs.SwapIntervalEXT', make_current)
        self.assertIn('if (mesa_current_output()) { argsp[0] = 0; }', self.wgl)

    def test_current_context_is_per_thread_and_host_follows_lazily(self):
        # Two cabinet devices are driven from several game threads. A global
        # current context made WineD3D save/restore on every call.
        for name in ('pt', 'mfifo', 'mdata'):
            self.assertIn(f'#define {name} (*m90_shm(&m90_raw_{name}))', self.guest)
        self.assertIn('#define currGLRC (m90_thread()->rc)', self.guest)
        self.assertNotIn('static uint32_t currDC, currGLRC;', self.guest)
        for signature in ('int WINAPI wglSwapBuffers (HDC hdc)\n{',
                          'int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd)\n{',
                          'wglSetPixelFormat(HDC hdc, int format, const PIXELFORMATDESCRIPTOR *ppfd)\n{'):
            body = function(self.guest, signature)
            self.assertLess(body.index('m90_dispatch();'), body.index('m90_select_output(hdc);'), signature)
        create = function(self.guest, 'wglCreateContextAttribsARB(HDC hDC,')
        self.assertLess(create.index('m90_dispatch();'), create.index('m90_select_output(hDC);'))
        self.assertLess(create.index('m90_reclaim_slot();'), create.index('WGL_FUNCP("wglCreateContextAttribsARB")'))
        self.assertIn('m90_host_rc = 0;', create)
        self.assertIn('m90_host_rc = 0;', function(self.guest, 'uint32_t PT_CALL COMPACT\nmglCreateContext (uint32_t arg0)'))
        self.assertIn('m90_host_rc = 0;', function(self.guest, 'uint32_t PT_CALL COMPACT\nmglDeleteContext (uint32_t arg0)'))
        make_current = function(self.guest, 'uint32_t PT_CALL COMPACT\nmglMakeCurrent (uint32_t arg0, uint32_t arg1)')
        self.assertIn('if (!m90_any_dc && !mglCreateContext(arg0))', make_current)
        self.assertLess(make_current.index('currGLRC = arg1;'), make_current.index('m90_host_switch(arg0, arg1);'))
        self.assertLess(make_current.index('m90_host_switch(arg0, arg1);'), make_current.index('m90_configure(arg1);'))
        self.assertIn('M90_CONTEXT_SWITCHES', self.transport)
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('gcc')
        if not compiler or os.name != 'nt':
            self.skipTest('Windows C compiler not available')
        state_start = self.guest.index('static volatile uint32_t *pt0;\n/* M90: WGL')
        state_end = self.guest.index('#define currGLRC (m90_thread()->rc)\n') + len('#define currGLRC (m90_thread()->rc)\n')
        slots_start = self.guest.index('\n/* M90: WineD3D destroys the contexts of exited threads')
        slots_end = self.guest.index('static void m90_reclaim_slot(void)')
        slots = self.guest[slots_start:slots_end] + function(self.guest, 'static void m90_reclaim_slot(void)')
        code = (Path(__file__).with_name('fixtures') / 'guest_threads_mock.c').read_text()
        code = code.replace('/*M90_THREAD_STATE*/', self.guest[state_start:state_end])
        code = code.replace('/*M90_THREAD_SLOTS*/', slots)
        with tempfile.TemporaryDirectory(prefix='gpu-threads-') as directory:
            source = Path(directory) / 'threads.c'
            source.write_text(code)
            binary = source.with_suffix('.exe')
            result = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Werror', str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GUEST_THREADS_OK', result.stdout)

    def test_second_output_window_exists_before_its_first_context(self):
        # WineD3D never calls ChoosePixelFormat for the second monitor's window;
        # without this handshake the host creates that output's context on DC 0.
        select = function(self.guest, 'static void m90_select_output(HDC dc)\n{')
        self.assertIn('if (!m90_output_window[output] && !m90_output_init)', select)
        self.assertLess(select.index('ptm[0xFB4 >> 2] = output'), select.index('wglChoosePixelFormat(dc, &pfd)'))
        self.assertLess(select.index('m90_output_init = 1'), select.index('wglChoosePixelFormat(dc, &pfd)'))
        choose = function(self.guest, 'int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd)\n{')
        self.assertLess(choose.index('m90_select_output(hdc)'), choose.index('ptm[0xFB8 >> 2]'))
        self.assertLess(self.guest.index('int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd);'),
                        self.guest.index('static void m90_select_output(HDC dc)\n{'))

    def test_guest_slot_is_released_by_its_last_handle(self):
        # WineD3D replaces its probe context on the same slot (same handle) and
        # then deletes the probe; that must not destroy the live replacement.
        self.assertIn('m90_slot_refs[level]++', function(self.guest, 'wglCreateContextAttribsARB(HDC hDC,'))
        self.assertIn('m90_slot_refs[cntxDC[1]]++', function(self.guest, 'uint32_t PT_CALL COMPACT\nmglCreateContext (uint32_t arg0)'))
        compiler = os.environ.get('M90_TEST_CC') or shutil.which('gcc')
        if not compiler:
            self.skipTest('C compiler not available')
        code = (Path(__file__).with_name('fixtures') / 'guest_slot_refs_mock.c').read_text()
        delete = function(self.guest, 'uint32_t PT_CALL COMPACT\nmglDeleteContext (uint32_t arg0)')
        with tempfile.TemporaryDirectory(prefix='gpu-refs-') as directory:
            source = Path(directory) / 'refs.c'
            source.write_text(code.replace('DELETE_FUNCTION', delete))
            binary = source.with_suffix('.exe' if os.name == 'nt' else '')
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Werror', str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GUEST_SLOT_REFS_OK', result.stdout)


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
