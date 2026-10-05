#include "db_cpu.h"

#include <stdlib.h>

#include "m68k.h"

/* Musashi calls global memory callbacks; the firmware runs one database CPU. */
static db_machine *active_machine;
static int uart_init_pending;
static int uart_init_returned;
static int uart_init_result;
static uint64_t total_cycles;
static db_cycle_callback cycle_callback;
static void *cycle_context;
static uint32_t timer_cycle_accumulator;
static uint32_t board_ticks;
static uint32_t uart_ticks;
static int irq_phase;
static int irq_error;
static int board_returned;
static int uart_returned;

#define IO_INIT_RETURN_PC 0x00018482u
#define UART_INIT_ENTRY 0x000c5f82u
#define UART_INIT_RETURN_SENTINEL 0x00000300u
#define UART_STATE_ADDRESS 0x001ebbb0u
#define UART_STATE_READY 0x58a9u
#define BOARD_TIMER_VECTOR 64u
#define BOARD_TIMER_HANDLER 0x000750dcu
#define BOARD_TIMER_RTE_PC 0x0007511cu
#define UART_TIMER_VECTOR 134u
#define UART_TIMER_HANDLER 0x000c6bd0u
#define UART_TIMER_RTE_PC 0x000c6c0au
#define BOARD_TIMER_STATUS 0x0080018bu
#define BOARD_TIMER_PENDING 0x08u
#define DB_BOARD_TIMER_CYCLES 160000u /* 10 ms at nominal 16 MHz */

static void account_cycles(int executed)
{
    if (executed <= 0)
        return;
    total_cycles += (uint32_t)executed;
    db_rtc_advance_cycles(&active_machine->rtc, (uint32_t)executed);
    if (cycle_callback != 0)
        cycle_callback(total_cycles, cycle_context);
}

static void instruction_hook(unsigned int pc)
{
    if (irq_phase == 1 && pc == BOARD_TIMER_RTE_PC) {
        board_returned = 1;
        m68k_end_timeslice();
    } else if (irq_phase == 2 && pc == UART_TIMER_RTE_PC) {
        uart_returned = 1;
        m68k_end_timeslice();
    } else if (pc == UART_INIT_RETURN_SENTINEL && uart_init_pending) {
        uart_init_returned = 1;
        m68k_end_timeslice();
    } else if (pc == IO_INIT_RETURN_PC && uart_init_result == 0 &&
               !uart_init_pending) {
        uart_init_pending = 1;
        m68k_end_timeslice();
    }
}

/* Match the existing PC bridge's 68020 format-zero frame. The original
 * handler executes the RTE; no handler body or ready value is fabricated. */
static int enter_original_interrupt(uint32_t vector, uint32_t handler)
{
    uint32_t vbr = m68k_get_reg(0, M68K_REG_VBR);
    uint32_t sp = m68k_get_reg(0, M68K_REG_SP);
    uint32_t pc = m68k_get_reg(0, M68K_REG_PC);
    uint32_t sr = m68k_get_reg(0, M68K_REG_SR);
    if (db_machine_read32(active_machine, vbr + vector * 4u) != handler ||
        sp < 8u || sp >= DB_RAM_SIZE || (sr & 0x2000u) == 0u)
        return -1;
    sp -= 8u;
    db_machine_write16(active_machine, sp, (uint16_t)sr);
    db_machine_write32(active_machine, sp + 2u, pc);
    db_machine_write16(active_machine, sp + 6u, (uint16_t)(vector * 4u));
    m68k_set_reg(M68K_REG_SP, sp);
    m68k_set_reg(M68K_REG_SR, (sr | 0x2700u) & 0xffffu);
    m68k_set_reg(M68K_REG_PC, handler);
    return 0;
}

static void start_board_tick(void)
{
    uint8_t status = db_machine_read8(active_machine, BOARD_TIMER_STATUS);
    db_machine_write8(active_machine, BOARD_TIMER_STATUS,
                      (uint8_t)(status | BOARD_TIMER_PENDING));
    if (enter_original_interrupt(BOARD_TIMER_VECTOR,
                                 BOARD_TIMER_HANDLER) != 0) {
        irq_error = 1;
        return;
    }
    irq_phase = 1;
    ++board_ticks;
}

static void finish_board_tick(void)
{
    uint8_t status = db_machine_read8(active_machine, BOARD_TIMER_STATUS);
    db_machine_write8(active_machine, BOARD_TIMER_STATUS,
                      (uint8_t)(status & ~BOARD_TIMER_PENDING));
    if (enter_original_interrupt(UART_TIMER_VECTOR,
                                 UART_TIMER_HANDLER) != 0) {
        irq_error = 2;
        return;
    }
    irq_phase = 2;
    ++uart_ticks;
}

/* Run the owner's code, then restore the interrupted CPU context exactly.
 * Only the routine's SRAM/MMIO side effects are retained.  The sentinel is
 * temporarily NOP so the instruction hook can stop without taking a trap. */
static int run_original_uart_initializer(void)
{
    void *saved_context;
    uint32_t stack;
    uint32_t saved_return;
    uint16_t saved_sentinel;
    unsigned int i;
    int result = -1;

    saved_context = malloc(m68k_context_size());
    if (saved_context == 0)
        return -1;
    m68k_get_context(saved_context);
    stack = m68k_get_reg(0, M68K_REG_SP) - 4u;
    saved_return = db_machine_read32(active_machine, stack);
    saved_sentinel = db_machine_read16(active_machine,
                                        UART_INIT_RETURN_SENTINEL);
    db_machine_write16(active_machine, UART_INIT_RETURN_SENTINEL, 0x4e71u);
    db_machine_write32(active_machine, stack, UART_INIT_RETURN_SENTINEL);
    m68k_set_reg(M68K_REG_SP, stack);
    m68k_set_reg(M68K_REG_PC, UART_INIT_ENTRY);
    uart_init_returned = 0;
    for (i = 0; i < 100000u && !uart_init_returned &&
                active_machine->unmodelled_accesses == 0u; ++i)
        account_cycles(m68k_execute(1000));
    if (uart_init_returned &&
        db_machine_read16(active_machine, UART_STATE_ADDRESS) ==
            UART_STATE_READY)
        result = 1;
    db_machine_write32(active_machine, stack, saved_return);
    db_machine_write16(active_machine, UART_INIT_RETURN_SENTINEL,
                       saved_sentinel);
    m68k_set_context(saved_context);
    free(saved_context);
    return result;
}

unsigned int m68k_read_memory_8(unsigned int address)
{
    return db_machine_read8(active_machine, address);
}

unsigned int m68k_read_memory_16(unsigned int address)
{
    return db_machine_read16(active_machine, address);
}

unsigned int m68k_read_memory_32(unsigned int address)
{
    return db_machine_read32(active_machine, address);
}

void m68k_write_memory_8(unsigned int address, unsigned int value)
{
    db_machine_write8(active_machine, address, (uint8_t)value);
}

void m68k_write_memory_16(unsigned int address, unsigned int value)
{
    db_machine_write16(active_machine, address, (uint16_t)value);
}

void m68k_write_memory_32(unsigned int address, unsigned int value)
{
    db_machine_write32(active_machine, address, value);
}

void db_cpu_boot(db_machine *machine)
{
    active_machine = machine;
    uart_init_pending = 0;
    uart_init_returned = 0;
    uart_init_result = 0;
    total_cycles = 0u;
    cycle_callback = 0;
    cycle_context = 0;
    timer_cycle_accumulator = 0;
    board_ticks = 0;
    uart_ticks = 0;
    irq_phase = 0;
    irq_error = 0;
    board_returned = 0;
    uart_returned = 0;
    machine->end_timeslice = m68k_end_timeslice;
    m68k_init();
    m68k_set_instr_hook_callback(instruction_hook);
    m68k_set_cpu_type(M68K_CPU_TYPE_68020);
    m68k_pulse_reset();
    m68k_set_reg(M68K_REG_D2, DB_BOOT_COOKIE);
    m68k_set_reg(M68K_REG_D3, 0u);
    m68k_set_reg(M68K_REG_SP, DB_BOOT_SP);
    m68k_set_reg(M68K_REG_PC, DB_BOOT_PC);
}

int db_cpu_execute(int cycles)
{
    int executed = m68k_execute(cycles);
    account_cycles(executed);
    if (uart_init_pending && uart_init_result == 0) {
        uart_init_result = run_original_uart_initializer();
        uart_init_pending = 0;
        timer_cycle_accumulator = 0;
    }
    if (board_returned) {
        board_returned = 0;
        finish_board_tick();
    } else if (uart_returned) {
        uart_returned = 0;
        irq_phase = 0;
    }
    if (uart_init_result == 1 && irq_error == 0) {
        timer_cycle_accumulator += (uint32_t)executed;
        if (irq_phase == 0 && timer_cycle_accumulator >=
                              DB_BOARD_TIMER_CYCLES) {
            timer_cycle_accumulator -= DB_BOARD_TIMER_CYCLES;
            start_board_tick();
        }
    }
    return executed;
}

uint64_t db_cpu_total_cycles(void)
{
    return total_cycles;
}

void db_cpu_set_cycle_callback(db_cycle_callback callback, void *context)
{
    cycle_callback = callback;
    cycle_context = context;
}

uint32_t db_cpu_pc(void)
{
    return m68k_get_reg(0, M68K_REG_PC);
}

int db_cpu_uart_init_result(void)
{
    return uart_init_result;
}

uint32_t db_cpu_board_ticks(void)
{
    return board_ticks;
}

uint32_t db_cpu_uart_ticks(void)
{
    return uart_ticks;
}

int db_cpu_irq_phase(void)
{
    return irq_phase;
}

int db_cpu_irq_error(void)
{
    return irq_error;
}
