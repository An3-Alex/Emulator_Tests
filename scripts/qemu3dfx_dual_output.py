"""Route the pinned Windows GPU transport to two independent cabinet windows."""


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unexpected dual-output anchor: ' + old[:80])
    return source.replace(old, new, 1)


def patch_sdl(source):
    source = once(source, '} scon_cb;', '''} scon_cbs[2];
static unsigned m90_output;
#define scon_cb scon_cbs[m90_output]
unsigned mesa_current_output(void) { return m90_output; }
void mesa_select_output(unsigned output)
{
    if (output < 2 && output < sdl2_num_outputs) {
        m90_output = output;
    }
}
''')
    source = source.replace('s->scon = &sdl2_console[0];',
                            's->scon = &sdl2_console[m90_output];')
    source = once(source, '    s->opaque = 0;\n    s->cwnd_fn = (void (*)(void *, void *, void *))cwnd_fn;',
                  '    s->opaque = (void *)(uintptr_t)(m90_output + 1);\n'
                  '    s->cwnd_fn = (void (*)(void *, void *, void *))cwnd_fn;')
    source = once(source, '    if (!SDL_GetHint(SDL_HINT_RENDER_DRIVER) ||',
                  '    if (!(SDL_GetWindowFlags(s->scon->real_window) & SDL_WINDOW_OPENGL) ||\n'
                  '        !SDL_GetHint(SDL_HINT_RENDER_DRIVER) ||')
    source = once(source, '''    s->scon->winctx = SDL_GL_GetCurrentContext();
    if (!s->scon->winctx)
        s->scon->winctx = SDL_GL_CreateContext(s->scon->real_window);''',
                  '    s->scon->winctx = SDL_GL_CreateContext(s->scon->real_window);')
    source = once(source, '    if (!s->opaque)', '    if (s->cwnd_fn)')
    return source


def patch_transport(source):
    source = once(source, '#include "qemu/osdep.h"',
                  '#include "qemu/osdep.h"\n#include "ui/console.h"')
    source = once(source, '    if (addr == 0xFBC) {', '''    if (addr == 0xFB4 && val < 2) {
        /* Drain commands on the OLD drawable before changing output. */
        if (s->mglContext && s->mglCntxCurrent) {
            processFifo(s);
        }
        mesa_select_output(val);
        return;
    }
    if (addr == 0xFBC) {''')
    source = once(source, '    if (addr == 0xFBC) {', '''    if (addr == 0xFF4 && MESAGL_MAGIC - val < MAX_LVLCNTX) {
        if (s->mglContext && s->mglCntxCurrent) { processFifo(s); }
        DPRINTF("M90 release slot %u output %u", (unsigned)(MESAGL_MAGIC - val), mesa_current_output());
        MGLDeleteContext(MESAGL_MAGIC - val);
        return; /* One context release must not shut down the other monitor. */
    }
    if (addr == 0xFBC) {''')
    source = once(source, '                    uint32_t *cntxRC = (uint32_t *)(s->fifo_ptr + (MGLSHM_SIZE - PAGE_SIZE));',
                  '                    uint32_t *cntxRC = (uint32_t *)(s->fifo_ptr + (MGLSHM_SIZE - PAGE_SIZE));\n'
                  '                    cntxRC[1] = mesa_current_output() * 3;\n'
                  '                    if ((cntxRC[0] & 0xFFFFFFF0U) != (MESAGL_HPBDC & 0xFFFFFFF0U)) {\n'
                  '                        m90_client_created(s, cntxRC[1]);\n'
                  '                    }')
    return patch_transport_client(source)


HOST_CLIENT_STATE = '''/* M90: buffer bindings and client arrays are GL context state. Two cabinet
 * devices alternate contexts every frame; one tracker per device would read a
 * buffer offset of one context as guest memory of the other. */
typedef struct {
    vtxarry_t Color, EdgeFlag, Normal, Index, TexCoord[MAX_TEXUNIT], Vertex,
              Interleaved, SecondaryColor, FogCoord, Weight, GenAttrib[2];
    int texUnit, pixPackBuf, pixUnpackBuf, szPackWidth, szUnpackWidth,
        szPackHeight, szUnpackHeight, queryBuf, arrayBuf, elemArryBuf, vao;
} M90ClientState;
static M90ClientState m90_client[MAX_LVLCNTX];
static int m90_client_slot = -1;
#define M90_CLIENT_COPY(dst, src) do { \\
    (dst)->Color = (src)->Color; (dst)->EdgeFlag = (src)->EdgeFlag; \\
    (dst)->Normal = (src)->Normal; (dst)->Index = (src)->Index; \\
    memcpy((dst)->TexCoord, (src)->TexCoord, sizeof((dst)->TexCoord)); \\
    (dst)->Vertex = (src)->Vertex; (dst)->Interleaved = (src)->Interleaved; \\
    (dst)->SecondaryColor = (src)->SecondaryColor; (dst)->FogCoord = (src)->FogCoord; \\
    (dst)->Weight = (src)->Weight; \\
    memcpy((dst)->GenAttrib, (src)->GenAttrib, sizeof((dst)->GenAttrib)); \\
    (dst)->texUnit = (src)->texUnit; \\
    (dst)->pixPackBuf = (src)->pixPackBuf; (dst)->pixUnpackBuf = (src)->pixUnpackBuf; \\
    (dst)->szPackWidth = (src)->szPackWidth; (dst)->szUnpackWidth = (src)->szUnpackWidth; \\
    (dst)->szPackHeight = (src)->szPackHeight; (dst)->szUnpackHeight = (src)->szUnpackHeight; \\
    (dst)->queryBuf = (src)->queryBuf; (dst)->arrayBuf = (src)->arrayBuf; \\
    (dst)->elemArryBuf = (src)->elemArryBuf; (dst)->vao = (src)->vao; \\
} while (0)

static void m90_client_created(MesaPTState *s, uint32_t slot)
{
    if (slot >= MAX_LVLCNTX) { return; }
    /* Context creation may reset the live trackers: keep the live owner's. */
    if (m90_client_slot >= 0 && m90_client_slot != (int)slot) {
        M90_CLIENT_COPY(&m90_client[m90_client_slot], s);
    }
    memset(&m90_client[slot], 0, sizeof(m90_client[slot]));
    m90_client_slot = -1;
}

static void m90_client_switch(MesaPTState *s, uint32_t handle, int level)
{
    static int64_t last;
    static unsigned calls, switches;
    if (!handle || (handle & 0xFFFFFFF0U) != (MESAGL_MAGIC & 0xFFFFFFF0U)
            || level < 0 || level >= MAX_LVLCNTX) {
        return;
    }
    /* Low-rate evidence of how often the host has to change contexts. */
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_REALTIME);
    calls++;
    switches += level != m90_client_slot;
    if (!last) { last = now; }
    if (now - last >= 10000000000LL) {
        fprintf(stderr, "M90_CONTEXT_SWITCHES make_current=%u switches=%u seconds=%.2f\\n",
                calls, switches, (now - last) / 1e9);
        calls = switches = 0;
        last = now;
    }
    if (level == m90_client_slot) {
        return;
    }
    if (m90_client_slot >= 0) {
        M90_CLIENT_COPY(&m90_client[m90_client_slot], s);
    }
    M90_CLIENT_COPY(s, &m90_client[level]);
    m90_client_slot = level;
}

'''


def patch_transport_client(source):
    marker = '/* M90: buffer bindings and client arrays are GL context state.'
    if marker in source:
        return source
    source = once(source, 'static void mesapt_write(void *opaque, hwaddr addr, uint64_t val, unsigned size)\n{',
                  HOST_CLIENT_STATE +
                  'static void mesapt_write(void *opaque, hwaddr addr, uint64_t val, unsigned size)\n{')
    source = once(source, '''                        uint32_t *argsp = (uint32_t *)(func + ALIGNED(strnlen((const char *)func, 64)));
                        if (argsp[0] && (argsp[1] == 0)) {''', '''                        uint32_t *argsp = (uint32_t *)(func + ALIGNED(strnlen((const char *)func, 64)));
                        if (argsp[0]) {
                            m90_client_created(s, argsp[1]);
                        }
                        if (argsp[0] && (argsp[1] == 0)) {''')
    source = once(source, '                        s->mglCntxCurrent = MGLMakeCurrent(ptVer[0], level)? 0:1;',
                  '                        m90_client_switch(s, ptVer[0], level);\n'
                  '                        s->mglCntxCurrent = MGLMakeCurrent(ptVer[0], level)? 0:1;')
    source = once(source, '                        MGLMakeCurrent(ptVer[0], level);\n',
                  '                        m90_client_switch(s, ptVer[0], level);\n'
                  '                        MGLMakeCurrent(ptVer[0], level);\n')
    # Base contexts of both displays alternate every frame; log only others.
    return once(source, '(ptVer[0] && (lvl_prev != level) && (0 == NumPbuffer()))',
                '(ptVer[0] && (lvl_prev != level) && (0 == NumPbuffer()) && (level % 3))')


def patch_blit(source):
    # ui/console.h carries the canonical glide-patch prototype
    # int mesa_gui_fullscreen(int *); the upstream file-local const void *
    # forward declaration would conflict with it once the header is included.
    source = once(source, '#include "mesagl_impl.h"\n\nint mesa_gui_fullscreen(const void *);\n',
                  '#include "mesagl_impl.h"\n#include "ui/console.h"\n')
    return once(source, '} blit;',
                '} output_blit[2];\n#define blit output_blit[mesa_current_output()]')


def patch_wgl(source):
    source = once(source, 'static HWND hwnd;\nstatic HDC hDC, hPBDC[MAX_PBUFFER];', '''static HWND output_hwnd[2];
static HDC output_dc[2], hPBDC[MAX_PBUFFER];
#define hwnd output_hwnd[mesa_current_output()]
#define hDC output_dc[mesa_current_output()]''')
    source = once(source, 'static int wnd_ready, GLon12;',
                  'static int output_ready[2], GLon12;\n#define wnd_ready output_ready[mesa_current_output()]')
    source = once(source, '''    ReleaseDC(hwnd, hDC);
    hwnd = (HWND)nwnd;
    hDC = GetDC(hwnd);''', '''    unsigned output = (uintptr_t)opaque ? (uintptr_t)opaque - 1 : 0;
    if (output >= 2) { return; }
    if (output_hwnd[output] && output_dc[output]) {
        ReleaseDC(output_hwnd[output], output_dc[output]);
    }
    output_hwnd[output] = (HWND)nwnd;
    output_dc[output] = GetDC(output_hwnd[output]);''')
    source = once(source, '    qatomic_set(&wnd_ready, 1);',
                  '    qatomic_set(&output_ready[output], 1);')
    old = '''        wglFuncs.MakeCurrent(NULL, NULL);
        for (i = MAX_LVLCNTX; i > 1;) {
            if (hRC[--i]) {
                wglFuncs.DeleteContext(hRC[i]);
                hRC[i] = 0;
            }
        }
        if (!hRC[0])
            hRC[0] = wglFuncs.CreateContext(hDC);
        ret = (hRC[0])? 0:1;'''
    source = once(source, old, '''        unsigned base = mesa_current_output() * 8;
        wglFuncs.MakeCurrent(NULL, NULL);
        if (!hRC[base]) {
            hRC[base] = wglFuncs.CreateContext(hDC);
        }
        ret = hRC[base] ? 0 : 1;''')
    source = once(source, '        if (!n)\n            MGLActivateHandler(1, 0);',
                  '        if (n == mesa_current_output() * 8)\n            MGLActivateHandler(1, 0);')
    source = once(source, '    return (hRC[0] == wglFuncs.GetCurrentContext());',
                  '    return (hRC[mesa_current_output() * 8] == wglFuncs.GetCurrentContext());')
    source = once(source, '    ImplMesaGLReset(); \\\n',
                  '    if (!hRC[0] && !hRC[8]) { ImplMesaGLReset(); } \\\n')
    start = source.index('void MGLDeleteContext(int level)')
    end = source.index('void MGLWndRelease(void)', start)
    source = source[:start] + '''void MGLDeleteContext(int level)
{
    if (level < 0 || level >= MAX_LVLCNTX || !hRC[level]) { return; }
    if (level == (int)mesa_current_output() * 8) {
        wglFuncs.MakeCurrent(hDC, hRC[level]);
        MesaBlitFree();
    }
    wglFuncs.MakeCurrent(NULL, NULL);
    wglFuncs.DeleteContext(hRC[level]);
    hRC[level] = 0;
}

''' + source[end:]
    source = once(source, '''        if (hRC[0]) {
            wglFuncs.MakeCurrent(NULL, NULL);
            wglFuncs.DeleteContext(hRC[0]);
            hRC[0] = 0;
        }''', '''        MGLDeleteContext(mesa_current_output() * 8);''')
    start = source.index('            if (argsp[1] == 0) {', source.index('FUNCP_HANDLER("wglCreateContextAttribsARB")'))
    end = source.index('            argsp[0] = ret;', start)
    source = source[:start] + '''            if (argsp[1] == 0) {
                unsigned base = mesa_current_output() * 8;
                wglFuncs.MakeCurrent(NULL, NULL);
                MGLDeleteContext(base);
                hRC[base] = wglFuncs.CreateContextAttribsARB(hDC, 0, (const int *)&argsp[2]);
                argsp[1] = base;
                ret = hRC[base] ? 1 : 0;
                DPRINTF("M90 base slot %u [%p] output %u dc %p err 0x%08lx", base, (void *)hRC[base],
                        mesa_current_output(), (void *)hDC, ret ? 0UL : GetLastError());
            } else if (i < MAX_LVLCNTX) {
                unsigned share = MESAGL_MAGIC - argsp[0];
                hRC[i] = wglFuncs.CreateContextAttribsARB(hDC,
                    share < MAX_LVLCNTX ? hRC[share] : 0, (const int *)&argsp[2]);
                ret = hRC[i] ? 1 : 0;
                DPRINTF("M90 shared slot %u share %u [%p] output %u dc %p err 0x%08lx", i, share,
                        share < MAX_LVLCNTX ? (void *)hRC[share] : 0, mesa_current_output(),
                        (void *)hDC, ret ? 0UL : GetLastError());
            } else {
                ret = 0; /* Never evict the other display's live context. */
                DPRINTF("M90 shared slots exhausted output %u", mesa_current_output());
            }
''' + source[end:]
    source = once(source, 'void MGLWndRelease(void)\n', 'static void MGLWndReleaseOutput(void)\n')
    source = once(source, 'static void MGLWndReleaseOutput(void)\n{',
                  'static void MGLWndReleaseOutput(void)\n{\n    deactivateCancel();')
    source = once(source, '            for (i = 0; ((i < MAX_LVLCNTX) && hRC[i]); i++);',
                  '            for (i = 1; i < MAX_LVLCNTX && (i == 3 || hRC[i]); i++);')
    start = source.index('int MGLCreateContext(uint32_t gDC)')
    source = source[:start] + '''void MGLWndRelease(void)
{
    unsigned previous = mesa_current_output();
    for (unsigned output = 0; output < 2; output++) {
        mesa_select_output(output);
        MGLWndReleaseOutput();
    }
    for (int i = 0; i < MAX_LVLCNTX; i++) {
        if (hRC[i]) { wglFuncs.DeleteContext(hRC[i]); hRC[i] = 0; }
    }
    mesa_select_output(previous);
}

''' + source[start:]
    # A temporary probe must never delete another output's real base context.
    source = once(source, '''    if ((n == 1) && hRC[--n]) {''',
                  '''    if ((n == 1) && !output_hwnd[0] && !output_hwnd[1] && hRC[--n]) {''')
    source = once(source, '    static int last;\n\n    if (i != last) {\n        last = i;',
                  '    static int last_by_output[2];\n    int *last = &last_by_output[mesa_current_output()];\n\n'
                  '    if (i != *last) {\n        *last = i;')
    source = once(source, 'static QEMUTimer *ts;',
                  'static QEMUTimer *output_timers[2];\n#define ts output_timers[mesa_current_output()]')
    source = once(source, '''static void deactivateOneshot(void *opaque)
{
    deactivateCancel();
    deactivateOnce();
}''', '''static void deactivateOneshot(void *opaque)
{
    unsigned previous = mesa_current_output();
    if (opaque) { mesa_select_output((uintptr_t)opaque - 1); }
    deactivateCancel();
    deactivateOnce();
    mesa_select_output(previous);
}''')
    source = once(source, '''static void deactivateGuiRefOneshot(void *opaque)
{
    deactivateCancel();
    graphic_hw_passthrough(qemu_console_lookup_by_index(0), 1);
}''', '''static void deactivateGuiRefOneshot(void *opaque)
{
    unsigned previous = mesa_current_output();
    if (opaque) { mesa_select_output((uintptr_t)opaque - 1); }
    deactivateCancel();
    graphic_hw_passthrough(qemu_console_lookup_by_index(mesa_current_output()), 1);
    mesa_select_output(previous);
}''')
    source = once(source, 'timer_new_ms(QEMU_CLOCK_VIRTUAL, deactivateOneshot, 0)',
                  'timer_new_ms(QEMU_CLOCK_VIRTUAL, deactivateOneshot, (void *)(uintptr_t)(mesa_current_output() + 1))')
    source = once(source, 'timer_new_ms(QEMU_CLOCK_VIRTUAL, deactivateGuiRefOneshot, 0)',
                  'timer_new_ms(QEMU_CLOCK_VIRTUAL, deactivateGuiRefOneshot, (void *)(uintptr_t)(mesa_current_output() + 1))')
    # Both displays swap in the vCPU thread, one after the other. Waiting for
    # vblank on both halves the frame rate; only the lower display paces.
    source = once(source, '''            if (wglFuncs.SwapIntervalEXT)
                wglFuncs.SwapIntervalEXT(val);
        }
''', '''            if (wglFuncs.SwapIntervalEXT)
                wglFuncs.SwapIntervalEXT(val);
        }
        if (mesa_current_output() && wglFuncs.SwapIntervalEXT && wglFuncs.GetSwapIntervalEXT
                && wglFuncs.GetSwapIntervalEXT()) {
            wglFuncs.SwapIntervalEXT(0);
        }
''')
    source = once(source, '''            int curr = wglFuncs.GetSwapIntervalEXT();
            if (curr != argsp[0]) {''', '''            if (mesa_current_output()) { argsp[0] = 0; } /* M90: upper display never waits. */
            int curr = wglFuncs.GetSwapIntervalEXT();
            if (curr != argsp[0]) {''')
    # The pinned protocol has six slots (MAGIC low nibble is 5).
    return source.replace('mesa_current_output() * 8', 'mesa_current_output() * 3').replace('!hRC[8]', '!hRC[3]')


GUEST_SELECT = '''/* M90: output identity comes from the guest window's actual monitor. */
static void m90_select_output(HDC dc)
{
    HWND window = WindowFromDC(dc);
    MONITORINFO info;
    if (!window) { return; } /* Includes pbuffer DCs and context release. */
    info.cbSize = sizeof(info);
    if (GetMonitorInfoA(MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST), &info)) {
        ptm[0xFB4 >> 2] = (info.dwFlags & MONITORINFOF_PRIMARY) ? 0 : 1;
    }
}

'''


def patch_guest(source):
    marker = '/* M90: output identity comes from'
    if marker in source:
        return patch_guest_threads(patch_guest_client(patch_guest_window(patch_guest_refs(patch_guest_timing(source)))))
    source = once(source, '  WGL_FUNCP("wglCreateContextAttribsARB");',
                  '  m90_select_output(hDC);\n  WGL_FUNCP("wglCreateContextAttribsARB");')
    source = once(source, 'static int level;',
                  'static void m90_select_output(HDC dc);\nstatic int level;')
    start = source.index('      if (currGLRC && hShareContext)', source.index('wglCreateContextAttribsARB(HDC hDC,'))
    end = source.index('  return (ret)? (HGLRC)(MESAGL_MAGIC - level):0;', start)
    source = source[:start] + '''      level = argsp[1]; /* Host slot, not a process-global increment. */
      if (!currDC) { InitClientStates(); }
      currDC = (uint32_t)hDC;
      GLwnd = WindowFromDC(hDC);
  }
''' + source[end:]
    source = once(source, 'uint32_t PT_CALL COMPACT\nmglCreateContext (uint32_t arg0)',
                  GUEST_SELECT + 'uint32_t PT_CALL COMPACT\nmglCreateContext (uint32_t arg0)')
    source = once(source, '    cntxDC[0] = arg0;',
                  '    m90_select_output((HDC)arg0);\n    cntxDC[0] = arg0;')
    old = '''    if (currGLRC == 0) {
        DPRINTF("CreateContext %x", arg0);
        currDC = arg0;
        currRC = MESAGL_MAGIC;
        InitClientStates();
        GLwnd = WindowFromDC((HDC)arg0);
    }'''
    source = once(source, old, '''    if (arg0 != ((MESAGL_HPBDC & 0xFFFFFFF0U) | i)) {
        currRC = MESAGL_MAGIC - cntxDC[1];
        if (!currDC) { InitClientStates(); }
        currDC = arg0;
        GLwnd = WindowFromDC((HDC)arg0);
    }''')
    source = once(source, '    //DPRINTF("MakeCurrent %x %x", arg0, arg1);',
                  '    m90_select_output((HDC)arg0);\n'
                  '    if (arg0) { currDC = arg0; GLwnd = WindowFromDC((HDC)arg0); }\n'
                  '    //DPRINTF("MakeCurrent %x %x", arg0, arg1);')
    for signature in (
        'int WINAPI wglSwapBuffers (HDC hdc)\n{',
        'int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd)\n{',
        'int WINAPI wglDescribePixelFormat(HDC hdc, int iPixelFormat, UINT nBytes, LPPIXELFORMATDESCRIPTOR ppfd)\n{',
        'wglSetPixelFormat(HDC hdc, int format, const PIXELFORMATDESCRIPTOR *ppfd)\n{',
    ):
        source = once(source, signature, signature + '\n    m90_select_output(hdc);')
    source = once(source, '''    currGLRC = (level && ((arg1 + level) == MESAGL_MAGIC))?
        (arg1 + level):((level)? MESAGL_MAGIC:arg1);''', '    currGLRC = arg1;')
    source = once(source, '    return ((currGLRC + level) == MESAGL_MAGIC)? (currGLRC - level):currGLRC;',
                  '    return currGLRC;')
    start = source.index('    if (level && ((arg0 + level) == MESAGL_MAGIC)) { }', source.index('mglDeleteContext (uint32_t arg0)'))
    end = source.index('    ptm[0xFF4 >> 2] = arg0;', start)
    source = source[:start] + '''    if (MESAGL_MAGIC - arg0 >= MAX_LVLCNTX) { return FALSE; }
    if (arg0 == currGLRC) { mglMakeCurrent(0, 0); }
''' + source[end:]
    source = once(source, '''    if (currGLRC) {
        mglMakeCurrent(0, 0);
        mglDeleteContext(MESAGL_MAGIC);
    }''', '''    /* Setting a pixel format on the other monitor must not delete live contexts. */''')
    return patch_guest_threads(patch_guest_client(patch_guest_window(patch_guest_refs(patch_guest_timing(source)))))


def patch_guest_timing(source):
    marker = '/* M90: configure each live context once, not on every rebind. */'
    if marker in source:
        return source
    source = once(source, 'static int level;', 'static int level;\n' + marker + '\nstatic uint32_t m90_configured;')
    source = once(source, '''        ptm[0xFB4 >> 2] = (info.dwFlags & MONITORINFOF_PRIMARY) ? 0 : 1;''', '''        static unsigned previous = ~0U;
        unsigned output = (info.dwFlags & MONITORINFOF_PRIMARY) ? 0 : 1;
        if (output != previous) { ptm[0xFB4 >> 2] = output; previous = output; }''')
    source = once(source, '      level = argsp[1]; /* Host slot, not a process-global increment. */',
                  '      level = argsp[1]; /* Host slot, not a process-global increment. */\n'
                  '      if (level < MAX_LVLCNTX) { m90_configured &= ~(1U << level); }')
    source = once(source, '    if (!currGLRC) {', '''    uint32_t slot = MESAGL_MAGIC - arg1;
    if (slot < MAX_LVLCNTX && !(m90_configured & (1U << slot))) {
        m90_configured |= 1U << slot;''')
    source = once(source, '    ptm[0xFF4 >> 2] = arg0;',
                  '    m90_configured &= ~(1U << (MESAGL_MAGIC - arg0));\n    ptm[0xFF4 >> 2] = arg0;')
    return source


def patch_guest_refs(source):
    # A host slot outlives the guest handle that created it: WineD3D creates a
    # legacy context, replaces it on the same slot via wglCreateContextAttribsARB
    # (identical handle) and then deletes the legacy one. Upstream ignores that
    # delete; deleting the slot would destroy the live replacement.
    marker = '/* M90: a host slot is released by its last guest handle. */'
    if marker in source:
        return source
    source = once(source, 'static uint32_t m90_configured;',
                  'static uint32_t m90_configured;\n' + marker +
                  '\nstatic uint8_t m90_slot_refs[MAX_LVLCNTX];')
    source = once(source, '      if (level < MAX_LVLCNTX) { m90_configured &= ~(1U << level); }',
                  '      if (level < MAX_LVLCNTX) { m90_configured &= ~(1U << level); m90_slot_refs[level]++; }')
    source = once(source, '        currRC = MESAGL_MAGIC - cntxDC[1];',
                  '        currRC = MESAGL_MAGIC - cntxDC[1];\n'
                  '        if (cntxDC[1] < MAX_LVLCNTX) { m90_slot_refs[cntxDC[1]]++; }')
    return once(source, '''    if (MESAGL_MAGIC - arg0 >= MAX_LVLCNTX) { return FALSE; }
    if (arg0 == currGLRC) { mglMakeCurrent(0, 0); }
    m90_configured &= ~(1U << (MESAGL_MAGIC - arg0));''', '''    uint32_t slot = MESAGL_MAGIC - arg0;
    if (slot >= MAX_LVLCNTX) { return TRUE; }
    if (m90_slot_refs[slot] > 1) { m90_slot_refs[slot]--; return TRUE; }
    m90_slot_refs[slot] = 0;
    if (arg0 == currGLRC) { mglMakeCurrent(0, 0); }
    m90_configured &= ~(1U << slot);''')


GUEST_CLIENT_STATE = '''
/* M90: client arrays and buffer bindings are tracked per context slot. */
static struct {
    __typeof__(vtxArry) arry;
    vtxarry_t interleaved;
    int pixPackBuf, pixUnpackBuf, szPackWidth, szUnpackWidth, szPackHeight, szUnpackHeight, queryBuf;
} m90_client[MAX_LVLCNTX];
static int m90_client_slot = -1;
static void m90_client_save(void)
{
    if (m90_client_slot < 0) { return; }
    m90_client[m90_client_slot].arry = vtxArry;
    m90_client[m90_client_slot].interleaved = Interleaved;
    m90_client[m90_client_slot].pixPackBuf = pixPackBuf;
    m90_client[m90_client_slot].pixUnpackBuf = pixUnpackBuf;
    m90_client[m90_client_slot].szPackWidth = szPackWidth;
    m90_client[m90_client_slot].szUnpackWidth = szUnpackWidth;
    m90_client[m90_client_slot].szPackHeight = szPackHeight;
    m90_client[m90_client_slot].szUnpackHeight = szUnpackHeight;
    m90_client[m90_client_slot].queryBuf = queryBuf;
}
static void m90_client_switch(uint32_t slot)
{
    if (slot >= MAX_LVLCNTX || (int)slot == m90_client_slot) { return; }
    m90_client_save();
    vtxArry = m90_client[slot].arry;
    Interleaved = m90_client[slot].interleaved;
    pixPackBuf = m90_client[slot].pixPackBuf;
    pixUnpackBuf = m90_client[slot].pixUnpackBuf;
    szPackWidth = m90_client[slot].szPackWidth;
    szUnpackWidth = m90_client[slot].szUnpackWidth;
    szPackHeight = m90_client[slot].szPackHeight;
    szUnpackHeight = m90_client[slot].szUnpackHeight;
    queryBuf = m90_client[slot].queryBuf;
    m90_client_slot = slot;
}
static void m90_client_created(uint32_t slot)
{
    if (slot >= MAX_LVLCNTX) { return; }
    memset(&m90_client[slot], 0, sizeof(m90_client[slot]));
    /* A replaced live context loses its state; the next bind loads defaults. */
    if ((int)slot == m90_client_slot) { m90_client_slot = -1; }
}'''


def patch_guest_client(source):
    marker = '/* M90: client arrays and buffer bindings are tracked per context slot. */'
    if marker in source:
        return source
    source = once(source, 'static uint8_t m90_slot_refs[MAX_LVLCNTX];',
                  'static uint8_t m90_slot_refs[MAX_LVLCNTX];' + GUEST_CLIENT_STATE)
    source = once(source, '      if (level < MAX_LVLCNTX) { m90_configured &= ~(1U << level); m90_slot_refs[level]++; }',
                  '      if (level < MAX_LVLCNTX) { m90_configured &= ~(1U << level); m90_slot_refs[level]++; }\n'
                  '      m90_client_created(level);')
    source = once(source, '        if (cntxDC[1] < MAX_LVLCNTX) { m90_slot_refs[cntxDC[1]]++; }',
                  '        if (cntxDC[1] < MAX_LVLCNTX) { m90_slot_refs[cntxDC[1]]++; }\n'
                  '        m90_client_created(cntxDC[1]);')
    return once(source, '    uint32_t slot = MESAGL_MAGIC - arg1;\n',
                '    uint32_t slot = MESAGL_MAGIC - arg1;\n'
                '    if (arg1) { m90_client_switch(slot); }\n')


GUEST_THREAD_STATE = '''static volatile uint32_t *pt0;
/* M90: WGL's current context belongs to a thread. WineD3D keeps one context
 * per swapchain and thread and compares wglGetCurrentContext() with it; a
 * process-global value made every call from another thread save and restore
 * contexts. The host has one current context, so the first shared-memory
 * access of a call brings it to the calling thread's context. */
typedef struct { uint32_t dc, rc; } M90Thread;
static DWORD m90_tls = TLS_OUT_OF_INDEXES;
static M90Thread m90_thread_fallback;
static uint32_t m90_host_dc, m90_host_rc, m90_any_dc;
static int m90_switching, m90_client_init;
static uint32_t *m90_raw_pt, *m90_raw_mfifo, *m90_raw_mdata;
static M90Thread *m90_thread(void)
{
    M90Thread *t;
    if (m90_tls == TLS_OUT_OF_INDEXES) { return &m90_thread_fallback; }
    t = (M90Thread *)TlsGetValue(m90_tls);
    if (!t) {
        t = (M90Thread *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*t));
        if (!t || !TlsSetValue(m90_tls, t)) { return &m90_thread_fallback; }
    }
    return t;
}
static void m90_host_switch(uint32_t dc, uint32_t rc);
static void m90_dispatch(void)
{
    M90Thread *t = m90_thread();
    if (t->rc && !m90_switching && (t->rc != m90_host_rc || t->dc != m90_host_dc)) {
        m90_switching = 1;
        m90_host_switch(t->dc, t->rc);
        m90_switching = 0;
    }
}
static uint32_t **m90_shm(uint32_t **area)
{
    m90_dispatch();
    return area;
}
#define pt (*m90_shm(&m90_raw_pt))
#define mfifo (*m90_shm(&m90_raw_mfifo))
#define mdata (*m90_shm(&m90_raw_mdata))
#define currDC (m90_thread()->dc)
#define currGLRC (m90_thread()->rc)
'''

GUEST_THREAD_SLOTS = '''
/* M90: WineD3D destroys the contexts of exited threads only with the device.
 * Give such a shared slot back when a new context finds none free. A later
 * release of the stale handle is then ignored once per reclaimed reference. */
WINBASEAPI HANDLE WINAPI OpenThread(DWORD, BOOL, DWORD);
static DWORD m90_slot_tid[MAX_LVLCNTX];
static uint8_t m90_slot_debt[MAX_LVLCNTX];
static int m90_thread_alive(DWORD tid)
{
    DWORD code = STILL_ACTIVE;
    HANDLE thread;
    if (!tid) { return 1; }
    thread = OpenThread(THREAD_QUERY_INFORMATION, FALSE, tid);
    if (!thread) { return 0; }
    if (!GetExitCodeThread(thread, &code)) { code = STILL_ACTIVE; }
    CloseHandle(thread);
    return code == STILL_ACTIVE;
}
static void m90_reclaim_slot(void)
{
    uint32_t slot;
    for (slot = 1; slot < MAX_LVLCNTX; slot++) {
        if (slot != 3 && !m90_slot_refs[slot]) { return; }
    }
    for (slot = 1; slot < MAX_LVLCNTX; slot++) {
        if (slot == 3 || MESAGL_MAGIC - slot == m90_host_rc || m90_thread_alive(m90_slot_tid[slot])) { continue; }
        m90_slot_debt[slot] += m90_slot_refs[slot];
        m90_slot_refs[slot] = 0;
        m90_configured &= ~(1U << slot);
        ptm[0xFF4 >> 2] = MESAGL_MAGIC - slot;
        m90_host_rc = 0; /* every host release unbinds */
        return;
    }
}'''

GUEST_THREAD_MAKE_CURRENT = '''static void m90_host_switch(uint32_t dc, uint32_t rc)
{
    static const char icdBuild[] __attribute__((aligned(16),used)) =
        __TIME__" "__DATE__" build ";
    uint32_t *ptVer = &mfifo[(MGLSHM_SIZE - PAGE_SIZE) >> 2];
    m90_select_output((HDC)dc);
    ptVer[0] = rc;
    memcpy((char *)&ptVer[1], rev_, 8);
    memcpy(((char *)&ptVer[1] + 8), icdBuild, sizeof(icdBuild));
    ptm[0xFF8 >> 2] = MESAGL_MAGIC;
    if (rc) { m90_client_switch(MESAGL_MAGIC - rc); }
    m90_host_dc = dc;
    m90_host_rc = rc;
}

static void m90_configure(uint32_t rc)
{
    uint32_t slot = MESAGL_MAGIC - rc;
    if (rc && slot < MAX_LVLCNTX && !(m90_configured & (1U << slot))) {
        m90_configured |= 1U << slot;
        struct mglOptions cfg;
        parse_options(&cfg);
        if (cfg.useSRGB && !glIsEnabled(GL_FRAMEBUFFER_SRGB))
            glEnable(GL_FRAMEBUFFER_SRGB);
        if (cfg.vsyncOff) {
            if (wglGetSwapIntervalEXT())
                wglSwapIntervalEXT(0);
        }
        else if (cfg.swapInt && (cfg.swapInt != wglGetSwapIntervalEXT()))
            wglSwapIntervalEXT(cfg.swapInt);
        if (logpname)
            HeapFree(GetProcessHeap(), 0, logpname);
        logpname = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 0x2000);
    }
}

uint32_t PT_CALL COMPACT
mglMakeCurrent (uint32_t arg0, uint32_t arg1)
{
    if (!m90_any_dc && !mglCreateContext(arg0))
        return 0;
    if (arg0) { currDC = arg0; GLwnd = WindowFromDC((HDC)arg0); m90_any_dc = 1; }
    currGLRC = arg1;
    m90_switching = 1;
    m90_host_switch(arg0, arg1);
    m90_switching = 0;
    m90_configure(arg1);
    return TRUE;
}'''


def patch_guest_fifo_args(source):
    # pt is a macro after patch_guest_threads; this helper's parameter must
    # not share its name. Separate step: it also repairs an earlier port.
    # The export table is checked (2977 entries). With the extra dispatch code
    # GCC clones this debug export with constant arguments; the clone would be
    # exported too.
    definition = 'void PT_CALL glDebugMessageInsertARB(uint32_t arg0, uint32_t arg1, uint32_t arg2, uint32_t arg3, uint32_t arg4, uint32_t arg5) {'
    if '__attribute__((noipa)) ' + definition not in source:
        source = once(source, '\n' + definition, '\n__attribute__((noipa)) ' + definition)
    old = 'static INLINE void fifoAddEntry(uint32_t *pt, int FEnum, int numArgs)'
    if old not in source:
        return source
    source = once(source, old, 'static INLINE void fifoAddEntry(uint32_t *args, int FEnum, int numArgs)')
    return once(source, '    for (i = 0; i < numArgs; i++)\n        mfifo[j++] = pt[i];',
                '    for (i = 0; i < numArgs; i++)\n        mfifo[j++] = args[i];')


def patch_guest_threads(source):
    marker = "/* M90: WGL's current context belongs to a thread."
    if marker in source:
        return patch_guest_fifo_args(source)
    source = once(source, 'static volatile uint32_t *pt0;\nstatic uint32_t *pt;\nstatic uint32_t *mfifo;\nstatic uint32_t *mdata;\n',
                  GUEST_THREAD_STATE)
    source = once(source, 'static uint32_t currDC, currGLRC;\n', '')
    source = once(source, '            currGLRC = 0;\n            currPixFmt = 0;',
                  '            if (m90_tls == TLS_OUT_OF_INDEXES) { m90_tls = TlsAlloc(); }\n'
                  '            currGLRC = 0;\n            currPixFmt = 0;')
    # Client trackers are per slot now; only the process' first context resets them.
    source = once(source, '      if (!currDC) { InitClientStates(); }\n      currDC = (uint32_t)hDC;',
                  '      if (!m90_client_init) { InitClientStates(); m90_client_init = 1; }\n'
                  '      currDC = (uint32_t)hDC; m90_any_dc = 1;')
    source = once(source, '        if (!currDC) { InitClientStates(); }\n        currDC = arg0;',
                  '        if (!m90_client_init) { InitClientStates(); m90_client_init = 1; }\n'
                  '        currDC = arg0; m90_any_dc = 1;')
    source = once(source, '    if ((int)slot == m90_client_slot) { m90_client_slot = -1; }\n}',
                  '    if ((int)slot == m90_client_slot) { m90_client_slot = -1; }\n}' + GUEST_THREAD_SLOTS)
    # Bring the host to this thread's context first, then pick the new
    # context's display; a WGL argument block must not be overwritten later.
    source = once(source, '  m90_select_output(hDC);\n  WGL_FUNCP("wglCreateContextAttribsARB");',
                  '  m90_dispatch();\n  if (hShareContext) { m90_reclaim_slot(); }\n'
                  '  m90_select_output(hDC);\n  WGL_FUNCP("wglCreateContextAttribsARB");')
    source = once(source, '  WGL_FUNCP_RET(ret);\n  if (ret) {\n      level = argsp[1];',
                  '  WGL_FUNCP_RET(ret);\n  m90_host_rc = 0; /* creation unbinds on the host */\n'
                  '  if (ret) {\n      level = argsp[1];')
    source = once(source, '      m90_client_created(level);',
                  '      m90_client_created(level);\n'
                  '      if (level < MAX_LVLCNTX) { m90_slot_tid[level] = GetCurrentThreadId(); }')
    source = once(source, '    cntxDC[0] = arg0;\n    ptm[0xFFC >> 2] = MESAGL_MAGIC;',
                  '    cntxDC[0] = arg0;\n    ptm[0xFFC >> 2] = MESAGL_MAGIC;\n    m90_host_rc = 0;')
    source = once(source, '        m90_client_created(cntxDC[1]);',
                  '        m90_client_created(cntxDC[1]);\n'
                  '        if (cntxDC[1] < MAX_LVLCNTX) { m90_slot_tid[cntxDC[1]] = GetCurrentThreadId(); }')
    source = once(source, '#define WGL_FUNCP(a) \\\n    uint32_t *funcp',
                  '#define WGL_FUNCP(a) \\\n    m90_dispatch(); \\\n    uint32_t *funcp')
    for signature in (
        'int WINAPI wglSwapBuffers (HDC hdc)\n{',
        'int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd)\n{',
        'int WINAPI wglDescribePixelFormat(HDC hdc, int iPixelFormat, UINT nBytes, LPPIXELFORMATDESCRIPTOR ppfd)\n{',
        'wglSetPixelFormat(HDC hdc, int format, const PIXELFORMATDESCRIPTOR *ppfd)\n{',
    ):
        source = once(source, signature + '\n    m90_select_output(hdc);',
                      signature + '\n    m90_dispatch();\n    m90_select_output(hdc);')
    start = source.index('uint32_t PT_CALL COMPACT\nmglMakeCurrent (uint32_t arg0, uint32_t arg1)\n{')
    end = source.index('    currGLRC = arg1;\n    return TRUE;\n}', start) + len('    currGLRC = arg1;\n    return TRUE;\n}')
    source = source[:start] + GUEST_THREAD_MAKE_CURRENT + source[end:]
    source = once(source, '    if (slot >= MAX_LVLCNTX) { return TRUE; }\n    if (m90_slot_refs[slot] > 1)',
                  '    if (slot >= MAX_LVLCNTX) { return TRUE; }\n'
                  '    if (m90_slot_debt[slot]) { m90_slot_debt[slot]--; return TRUE; }\n'
                  '    if (m90_slot_refs[slot] > 1)')
    source = once(source, '    m90_configured &= ~(1U << slot);\n    ptm[0xFF4 >> 2] = arg0;\n    return TRUE;',
                  '    m90_configured &= ~(1U << slot);\n    ptm[0xFF4 >> 2] = arg0;\n'
                  '    m90_host_rc = 0; /* every host release unbinds */\n    return TRUE;')
    return patch_guest_fifo_args(source)


def patch_slots(source):
    # 16 instead of 6 context slots (MAX_LVLCNTX is the magic's low nibble + 1):
    # WineD3D needs one context per swapchain and rendering thread.
    if '#define MESAGL_MAGIC    0x5b5eb5ef' in source:
        return source
    return once(source, '#define MESAGL_MAGIC    0x5b5eb5e5', '#define MESAGL_MAGIC    0x5b5eb5ef')


def patch_guest_window(source):
    # The host creates an output's window only from ChoosePixelFormat, which
    # also waits for it to be ready. WineD3D reuses its pixel format on the
    # second monitor's window and never asks there, leaving that output
    # without a host DC; run the handshake the first time an output is used.
    marker = '/* M90: create each output\'s host window before first use. */'
    if marker in source:
        return source
    source = once(source, "/* M90: output identity comes from the guest window's actual monitor. */\n",
                  "/* M90: output identity comes from the guest window's actual monitor. */\n" + marker +
                  '\nstatic int m90_output_window[2], m90_output_init;\n'
                  'int WINAPI wglChoosePixelFormat(HDC hdc, const PIXELFORMATDESCRIPTOR *ppfd);\n')
    return once(source, '''        if (output != previous) { ptm[0xFB4 >> 2] = output; previous = output; }
''', '''        if (output != previous) { ptm[0xFB4 >> 2] = output; previous = output; }
        if (!m90_output_window[output] && !m90_output_init) {
            PIXELFORMATDESCRIPTOR pfd = { sizeof(pfd), 1,
                PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER, PFD_TYPE_RGBA, 32 };
            m90_output_window[output] = 1;
            m90_output_init = 1;
            wglChoosePixelFormat(dc, &pfd);
            m90_output_init = 0;
        }
''')
