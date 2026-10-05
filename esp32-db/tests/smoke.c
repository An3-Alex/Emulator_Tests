#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "db_cpu.h"
#include "db_clock.h"

static uint64_t observed_cycles;
static uint8_t observed_tx;
static uint32_t observed_mmio_address;
static unsigned int observed_mmio_writes;
static unsigned int observed_mmio_reads;

static void observe_mmio(uint32_t address, int is_write, uint8_t value,
                         void *context)
{
    (void)value;
    (void)context;
    observed_mmio_address = address;
    if (is_write)
        ++observed_mmio_writes;
    else
        ++observed_mmio_reads;
}

static void observe_tx(uint8_t byte, void *context)
{
    (void)context;
    observed_tx = byte;
}

static void observe_cycles(uint64_t total, void *context)
{
    (void)context;
    observed_cycles = total;
}

int main(void)
{
    static const uint8_t expected_rtc[7] = {0x00u, 0x14u, 0x22u,
                                            0x04u, 0x01u, 0x02u, 0x12u};
    static const uint8_t rtc_widths[7] = {8u, 8u, 8u, 4u, 8u, 8u, 8u};
    static const uint8_t written_rtc[7] = {0x04u, 0x03u, 0x08u,
                                           0x05u, 0x02u, 0x02u, 0x12u};
    uint8_t *ram = (uint8_t *)calloc(DB_RAM_SIZE, 1u);
    db_machine machine;
    unsigned int field, bit;
    assert(ram != 0);
    db_machine_init(&machine, ram);
    machine.tx = observe_tx;
    machine.mmio_trace = observe_mmio;
    db_machine_write8(&machine, 0x00fffc0fu, 0xa5u);
    assert(observed_tx == 0xa5u);
    assert(observed_mmio_address == 0x00fffc0fu);
    assert(observed_mmio_writes == 1u);
    assert(db_machine_read8(&machine, 0x00fffc0du) == 0u);
    assert(observed_mmio_reads == 1u);
    machine.mmio_trace = 0;
    machine.rx_byte = 0x3cu;
    machine.rx_pending = 1u;
    assert(db_machine_read8(&machine, 0x00fffc0du) == 0x40u);
    assert(db_machine_read8(&machine, 0x00fffc0fu) == 0x3cu);
    assert(db_machine_read8(&machine, 0x00fffc0du) == 0u);

    db_machine_write32(&machine, 0u, DB_RAM_SIZE);
    db_machine_write32(&machine, 4u, DB_BOOT_PC);
    /* MOVEQ #42,D0; BRA.S -2.  This must execute, not merely decode. */
    db_machine_write16(&machine, DB_BOOT_PC, 0x702au);
    db_machine_write16(&machine, DB_BOOT_PC + 2u, 0x60feu);
    db_cpu_boot(&machine);
    db_cpu_set_cycle_callback(observe_cycles, 0);
    assert(db_cpu_execute(100) > 0);
    assert(observed_cycles == db_cpu_total_cycles());
    assert(db_clock_target_us(0u) == 0u);
    assert(db_clock_target_us(1u) == 1u);
    assert(db_clock_target_us(16u) == 1u);
    assert(db_clock_target_us(17u) == 2u);
    assert(db_clock_target_us(DB_CPU_MAX_CYCLES_PER_SECOND) == 1000000u);
    assert(db_cpu_pc() == DB_BOOT_PC + 2u);
    assert(machine.unmodelled_accesses == 0u);

    db_machine_write8(&machine, 0x00800181u, 0x13u);
    db_machine_write8(&machine, 0x00800181u, 0x07u);
    assert(db_machine_read8(&machine, 0x00800181u) == 0x13u);
    assert(db_machine_read8(&machine, 0x00fffc0cu) == 0x01u);
    assert(machine.dirty_pages[0] != 0u);
    db_machine_write8(&machine, 0x00fff907u, DB_RTC_CE);
    for (field = 0u; field < 7u; ++field) {
        for (bit = 0u; bit < rtc_widths[field]; ++bit) {
            unsigned int output;
            db_machine_write8(&machine, 0x00fff907u,
                              DB_RTC_CE | DB_RTC_CLK);
            output = (db_machine_read8(&machine, 0x00fff907u) &
                      DB_RTC_DATA) != 0u;
            assert(output == ((expected_rtc[field] >> bit) & 1u));
            db_machine_write8(&machine, 0x00fff907u, DB_RTC_CE);
        }
    }
    assert(machine.rtc.completed_reads == 1u);
    db_machine_write8(&machine, 0x00fff907u, 0u);
    db_machine_write8(&machine, 0x00fff907u, DB_RTC_CE | DB_RTC_WR);
    for (field = 0u; field < 7u; ++field) {
        for (bit = 0u; bit < rtc_widths[field]; ++bit) {
            uint8_t data = (written_rtc[field] >> bit) & 1u ? DB_RTC_DATA : 0u;
            db_machine_write8(&machine, 0x00fff907u,
                              DB_RTC_CE | DB_RTC_WR | data);
            db_machine_write8(&machine, 0x00fff907u,
                              DB_RTC_CE | DB_RTC_WR | DB_RTC_CLK | data);
            db_machine_write8(&machine, 0x00fff907u,
                              DB_RTC_CE | DB_RTC_WR | data);
        }
    }
    db_machine_write8(&machine, 0x00fff907u, 0u);
    assert(machine.rtc.completed_writes == 1u);
    assert(machine.rtc.year == 2012u && machine.rtc.month == 2u &&
           machine.rtc.day == 2u && machine.rtc.hour == 8u &&
           machine.rtc.minute == 3u && machine.rtc.second == 4u);
    db_rtc_advance_cycles(&machine.rtc, 16000000u);
    assert(machine.rtc.second == 5u);
    puts("ESP32_DB_CPU_SMOKE_OK");
    free(ram);
    return 0;
}
