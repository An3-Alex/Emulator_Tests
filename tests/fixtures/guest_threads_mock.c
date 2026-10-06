#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MESAGL_MAGIC 0x5b5eb5efU
#define MAX_LVLCNTX 16
#define TRUE 1
#define FALSE 0
static volatile uint32_t ptm[0x1000 >> 2];
static uint32_t backing[64];

/*M90_THREAD_STATE*/

static uint8_t m90_slot_refs[MAX_LVLCNTX];
static uint32_t m90_configured;
/*M90_THREAD_SLOTS*/

static int switches;
static uint32_t switched_to[64];
static void m90_host_switch(uint32_t dc, uint32_t rc)
{
    (void)mfifo[0]; /* nested shared-memory access during a switch must not recurse */
    switched_to[switches++ % 64] = rc;
    m90_host_dc = dc;
    m90_host_rc = rc;
}

static void bind_ctx(uint32_t dc, uint32_t rc) { currDC = dc; currGLRC = rc; }
static void gl_call(uint32_t arg) { pt[1] = arg; ptm[0] = pt[0] + arg; }

static volatile LONG phase;
static DWORD WINAPI second(LPVOID unused)
{
    (void)unused;
    bind_ctx(20, MESAGL_MAGIC - 3);
    gl_call(2);
    InterlockedExchange(&phase, 1);
    return 0;
}

static int expect(int ok, const char *what)
{
    if (!ok) { printf("FAIL %s\n", what); }
    return ok;
}

int main(void)
{
    int ok = 1;
    HANDLE thread;
    (void)m90_any_dc; (void)m90_client_init; (void)pt0;
    m90_tls = TlsAlloc();
    m90_raw_mfifo = backing; m90_raw_pt = &backing[1]; m90_raw_mdata = &backing[32];
    backing[1] = (uint32_t)(uintptr_t)&backing[60];
    gl_call(0);
    ok &= expect(switches == 0, "no context, no switch");
    bind_ctx(10, MESAGL_MAGIC);
    gl_call(1); gl_call(1); gl_call(1);
    ok &= expect(switches == 1 && switched_to[0] == MESAGL_MAGIC, "first call binds once");
    thread = CreateThread(NULL, 0, second, NULL, 0, NULL);
    WaitForSingleObject(thread, INFINITE);
    ok &= expect(phase == 1 && switches == 2 && switched_to[1] == MESAGL_MAGIC - 3, "other thread switches to its own context");
    ok &= expect(currGLRC == MESAGL_MAGIC, "current context stays per thread");
    gl_call(1);
    ok &= expect(switches == 3 && switched_to[2] == MESAGL_MAGIC, "back on this thread's context");
    gl_call(1);
    ok &= expect(switches == 3, "repeated calls do not switch");

    /* Slot reclaim: every shared slot taken, slot 5's thread has exited. */
    for (uint32_t slot = 1; slot < MAX_LVLCNTX; slot++) {
        if (slot != 3) { m90_slot_refs[slot] = 1; m90_slot_tid[slot] = GetCurrentThreadId(); }
    }
    m90_slot_tid[5] = GetThreadId(thread);
    CloseHandle(thread);
    m90_reclaim_slot();
    ok &= expect(!m90_slot_refs[5] && m90_slot_debt[5] == 1 && ptm[0xFF4 >> 2] == MESAGL_MAGIC - 5,
                 "dead thread's slot reclaimed");
    ok &= expect(m90_host_rc == 0, "reclaim unbinds the host");
    ptm[0xFF4 >> 2] = 0;
    m90_reclaim_slot();
    ok &= expect(ptm[0xFF4 >> 2] == 0, "a free slot is not reclaimed again");
    if (ok) { puts("GUEST_THREADS_OK"); }
    return ok ? 0 : 1;
}
