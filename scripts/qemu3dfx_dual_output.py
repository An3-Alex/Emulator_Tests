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
        MGLDeleteContext(MESAGL_MAGIC - val);
        return; /* One context release must not shut down the other monitor. */
    }
    if (addr == 0xFBC) {''')
    return once(source, '                    uint32_t *cntxRC = (uint32_t *)(s->fifo_ptr + (MGLSHM_SIZE - PAGE_SIZE));',
                '                    uint32_t *cntxRC = (uint32_t *)(s->fifo_ptr + (MGLSHM_SIZE - PAGE_SIZE));\n'
                '                    cntxRC[1] = mesa_current_output() * 3;')


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
            } else if (i < MAX_LVLCNTX) {
                unsigned share = MESAGL_MAGIC - argsp[0];
                hRC[i] = wglFuncs.CreateContextAttribsARB(hDC,
                    share < MAX_LVLCNTX ? hRC[share] : 0, (const int *)&argsp[2]);
                ret = hRC[i] ? 1 : 0;
            } else {
                ret = 0; /* Never evict the other display's live context. */
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
        return patch_guest_timing(source)
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
    return patch_guest_timing(source)


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
