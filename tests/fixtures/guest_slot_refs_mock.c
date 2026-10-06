#include <stdint.h>
#include <stdio.h>

#define TRUE 1
#define FALSE 0
#define PT_CALL
#define COMPACT
#define MESAGL_MAGIC 0x5b5eb5e5U
#define MAX_LVLCNTX 6

static uint32_t ptm[0x1000 >> 2];
static uint32_t currGLRC, m90_configured, unbinds;
static uint8_t m90_slot_refs[MAX_LVLCNTX];
static uint8_t m90_slot_debt[MAX_LVLCNTX];
static uint32_t m90_host_rc;

static uint32_t mglMakeCurrent(uint32_t dc, uint32_t rc)
{
    (void)dc;
    if (!rc) { unbinds++; }
    currGLRC = rc;
    return TRUE;
}

DELETE_FUNCTION

static int expect(int ok, const char *what)
{
    if (!ok) { printf("FAIL %s\n", what); }
    return ok;
}

int main(void)
{
    int ok = 1;
    /* WineD3D adapter probe: legacy context on slot 0, then its
     * wglCreateContextAttribsARB replacement on the same slot (same handle). */
    m90_slot_refs[0] = 2;
    currGLRC = MESAGL_MAGIC;
    ptm[0xFF4 >> 2] = 0;
    mglDeleteContext(MESAGL_MAGIC); /* the legacy context */
    ok &= expect(ptm[0xFF4 >> 2] == 0, "stale alias must not reach the host");
    ok &= expect(currGLRC == MESAGL_MAGIC && !unbinds, "live replacement stays current");
    mglDeleteContext(MESAGL_MAGIC); /* the replacement itself */
    ok &= expect(ptm[0xFF4 >> 2] == MESAGL_MAGIC, "last handle releases the host slot");
    ok &= expect(currGLRC == 0 && unbinds == 1, "last handle unbinds");
    /* The other monitor's base slot is untouched by slot-0 releases. */
    m90_slot_refs[3] = 1;
    ptm[0xFF4 >> 2] = 0;
    mglDeleteContext(MESAGL_MAGIC - 3);
    ok &= expect(ptm[0xFF4 >> 2] == MESAGL_MAGIC - 3 && m90_slot_refs[0] == 0, "slot 3 release");
    ok &= expect(mglDeleteContext(0x12345678U) == TRUE, "foreign handle is a no-op");
    /* A reclaimed slot ignores its stale handle once, then serves the new owner. */
    m90_slot_refs[4] = 1; m90_slot_debt[4] = 1; m90_host_rc = MESAGL_MAGIC - 4;
    ptm[0xFF4 >> 2] = 0;
    mglDeleteContext(MESAGL_MAGIC - 4);
    ok &= expect(ptm[0xFF4 >> 2] == 0 && m90_slot_refs[4] == 1 && !m90_slot_debt[4], "stale handle ignored");
    mglDeleteContext(MESAGL_MAGIC - 4);
    ok &= expect(ptm[0xFF4 >> 2] == MESAGL_MAGIC - 4 && m90_host_rc == 0, "owner release unbinds the host");
    if (ok) { puts("GUEST_SLOT_REFS_OK"); }
    return ok ? 0 : 1;
}
