#ifndef M90_DB_MACHINE_H
#define M90_DB_MACHINE_H

#include <stdint.h>
#include "db_rtc.h"

#define DB_RAM_SIZE (2u * 1024u * 1024u)
#define DB_BOOT_PC 0x0000040eu
#define DB_BOOT_SP 0x001fff80u
#define DB_BOOT_COOKIE 0x5f72d920u

typedef void (*db_tx_callback)(uint8_t byte, void *context);
typedef void (*db_ram_write_callback)(uint32_t address, uint8_t old_value,
                                      uint8_t new_value, void *context);
typedef void (*db_mmio_trace_callback)(uint32_t address, int is_write,
                                       uint8_t value, void *context);

typedef struct {
    uint8_t *ram;
    db_tx_callback tx;
    void *tx_context;
    db_ram_write_callback ram_write;
    void *ram_write_context;
    db_mmio_trace_callback mmio_trace;
    void *mmio_trace_context;
    uint8_t board_latch_previous;
    uint8_t board_latch_last;
    uint8_t board_port_input;
    uint8_t board_registers[512];
    uint8_t rx_byte;
    uint8_t rx_pending;
    uint8_t controller_registers[4096];
    db_rtc rtc;
    void (*end_timeslice)(void);
    uint32_t first_unmodelled_address;
    uint32_t unmodelled_accesses;
    uint32_t mirrored_board_accesses;
    uint8_t dirty_pages[DB_RAM_SIZE / 4096u / 8u];
} db_machine;

void db_machine_init(db_machine *machine, uint8_t *ram);
uint8_t db_machine_read8(db_machine *machine, uint32_t address);
void db_machine_write8(db_machine *machine, uint32_t address, uint8_t value);
uint16_t db_machine_read16(db_machine *machine, uint32_t address);
uint32_t db_machine_read32(db_machine *machine, uint32_t address);
void db_machine_write16(db_machine *machine, uint32_t address, uint16_t value);
void db_machine_write32(db_machine *machine, uint32_t address, uint32_t value);

#endif
