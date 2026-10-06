#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include <stddef.h>
#undef NULL
#define NULL 0
typedef uintptr_t HGLRC;
typedef uintptr_t HDC;
#define MAX_LVLCNTX 6
#define MAX_PBUFFER 16
#define MESAGL_MAGIC 0x5b5eb5e5U
#define MESAGL_HPBDC 0x5b5eb5f0U
static unsigned output, sequence, deleted[128], freed[2];
static HGLRC current, hRC[MAX_LVLCNTX], hPBRC[MAX_PBUFFER];
static HDC output_dc[2] = {10, 20}, hPBDC[MAX_PBUFFER];
#define hDC output_dc[output]
static unsigned mesa_current_output(void) { return output; }
static HGLRC create(HDC dc) { assert(dc == hDC); return ++sequence; }
static bool bind(HDC dc, HGLRC rc) { if (rc) assert(dc == hDC); current = rc; return true; }
static bool destroy(HGLRC rc) { assert(rc < 128); deleted[rc]++; return true; }
static HGLRC get(void) { return current; }
static struct {
    HGLRC (*CreateContext)(HDC);
    bool (*MakeCurrent)(HDC,HGLRC);
    bool (*DeleteContext)(HGLRC);
    HGLRC (*GetCurrentContext)(void);
    bool (*SwapIntervalEXT)(int);
} wglFuncs = {create, bind, destroy, get, NULL};
static void InitMesaGLExt(void) {}
static int ContextUseSRGB(void) { return 0; }
static int ContextVsyncOff(void) { return 0; }
static void wrContextSRGB(int value) {}
static void MGLActivateHandler(int a,int b) {}
static void MesaBlitFree(void) { assert(current == hRC[output*3]); freed[output]++; }
CONTEXT_FUNCTIONS
int main(void) {
    assert(MGLCreateContext(10) == 0);
    HGLRC lower = hRC[0];
    assert(lower);
    output = 1;
    assert(MGLCreateContext(20) == 0);
    HGLRC upper = hRC[3];
    assert(upper && upper != lower && hRC[0] == lower && !deleted[lower]);
    MGLMakeCurrent(MESAGL_MAGIC-3,3);
    assert(current == upper && DrawableContext());
    output = 0;
    MGLMakeCurrent(MESAGL_MAGIC,0);
    assert(current == lower && DrawableContext());
    MGLDeleteContext(0);
    assert(!hRC[0] && hRC[3] == upper && !deleted[upper]);
    assert(deleted[lower] == 1 && freed[0] == 1 && !freed[1]);
    MGLDeleteContext(99);
    output = 1;
    MGLMakeCurrent(MESAGL_MAGIC-3,3);
    assert(current == upper);
    MGLDeleteContext(3);
    assert(!hRC[3] && deleted[upper] == 1 && freed[1] == 1);
    puts("DUAL_GPU_CONTEXTS_OK");
}
