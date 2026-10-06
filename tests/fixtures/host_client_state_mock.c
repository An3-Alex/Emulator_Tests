#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MESAGL_MAGIC 0x5b5eb5e5U
#define MAX_LVLCNTX 6
#define MAX_TEXUNIT 8
#define QEMU_CLOCK_REALTIME 0
static int64_t qemu_clock_get_ns(int clock) { (void)clock; return 0; }

typedef struct { int size, type, stride; void *ptr; } vtxarry_t;
typedef struct {
    vtxarry_t Color, EdgeFlag, Normal, Index, TexCoord[MAX_TEXUNIT], Vertex,
              Interleaved, SecondaryColor, FogCoord, Weight, GenAttrib[2];
    int texUnit, pixPackBuf, pixUnpackBuf, szPackWidth, szUnpackWidth,
        szPackHeight, szUnpackHeight, queryBuf, arrayBuf, elemArryBuf, vao;
} MesaPTState;

/*M90_HOST_CODE*/

static int expect(int ok, const char *what)
{
    if (!ok) { printf("FAIL %s\n", what); }
    return ok;
}

int main(void)
{
    MesaPTState s;
    int ok = 1;
    memset(&s, 0, sizeof(s));
    m90_client_created(&s, 0);
    m90_client_created(&s, 3);
    m90_client_switch(&s, MESAGL_MAGIC, 0);              /* lower display */
    s.arrayBuf = 5; s.vao = 9; s.TexCoord[2].ptr = &s; s.pixUnpackBuf = 4;
    m90_client_switch(&s, MESAGL_MAGIC - 3, 3);          /* upper display */
    ok &= expect(s.arrayBuf == 0 && s.vao == 0 && !s.TexCoord[2].ptr && !s.pixUnpackBuf,
                 "upper context starts with default bindings");
    s.arrayBuf = 7;
    m90_client_switch(&s, MESAGL_MAGIC, 0);
    ok &= expect(s.arrayBuf == 5 && s.vao == 9 && s.TexCoord[2].ptr == &s && s.pixUnpackBuf == 4,
                 "lower bindings survive the upper frame");
    m90_client_switch(&s, MESAGL_MAGIC - 3, 3);
    ok &= expect(s.arrayBuf == 7, "upper bindings survive the lower frame");
    m90_client_switch(&s, 0, 0);                          /* unbind keeps trackers */
    m90_client_switch(&s, 0x5b5eb5e5U << 4, 0);           /* pbuffer handle is not a slot */
    ok &= expect(s.arrayBuf == 7, "unbind and pbuffer handles do not switch");
    /* Creating the lower base context resets the live trackers afterwards
     * (ContextCreateCommon); the live upper state must be kept first. */
    m90_client_created(&s, 0);
    memset(&s, 0, sizeof(s));
    m90_client_switch(&s, MESAGL_MAGIC - 3, 3);
    ok &= expect(s.arrayBuf == 7, "upper state kept across lower re-creation");
    m90_client_switch(&s, MESAGL_MAGIC, 0);
    ok &= expect(s.arrayBuf == 0 && s.vao == 0, "re-created lower context has default state");
    if (ok) { puts("HOST_CLIENT_STATE_OK"); }
    return ok ? 0 : 1;
}
