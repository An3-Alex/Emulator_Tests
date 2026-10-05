#include "db_machine.h"

#include <string.h>

#define MASK24 0x00ffffffu
#define MAIN_TX_STATUS 0x00fffc0cu
#define MAIN_RX_STATUS 0x00fffc0du
#define MAIN_DATA 0x00fffc0fu
#define BOARD_LATCH 0x00800181u
#define BOARD_PORT_INPUT 0x0080019bu
#define BOARD_PORT_SET 0x0080019du
#define BOARD_PORT_CLEAR 0x0080019fu
#define BOARD_REGISTER_BASE 0x00800000u
#define CONTROLLER_BASE 0x00fff000u
#define RTC_PORT 0x00fff907u

static void unmodelled(db_machine *machine, uint32_t address)
{
    if (machine->unmodelled_accesses == 0u) {
        machine->first_unmodelled_address = address;
        if (machine->end_timeslice != 0)
            machine->end_timeslice();
    }
    ++machine->unmodelled_accesses;
}

static uint8_t observed_read(db_machine *machine, uint32_t address,
                             uint8_t value)
{
    if (machine->mmio_trace != 0)
        machine->mmio_trace(address, 0, value, machine->mmio_trace_context);
    return value;
}

void db_machine_init(db_machine *machine, uint8_t *ram)
{
    memset(machine, 0, sizeof(*machine));
    machine->ram = ram;
    machine->board_port_input = 0x10u; /* closed door, absent physical panel */
    db_rtc_init(&machine->rtc);
}

uint8_t db_machine_read8(db_machine *machine, uint32_t address)
{
    address &= MASK24;
    if (address < DB_RAM_SIZE)
        return machine->ram[address];
    if (address == MAIN_TX_STATUS)
        return observed_read(machine, address, 0x01u);
    if (address == MAIN_RX_STATUS)
        return observed_read(machine, address,
                             machine->rx_pending ? 0x40u : 0u);
    if (address == MAIN_DATA) {
        uint8_t byte = machine->rx_byte;
        machine->rx_pending = 0u;
        return observed_read(machine, address, byte);
    }
    if (address == BOARD_LATCH && machine->board_latch_last == 0x07u &&
        (machine->board_latch_previous == 0x02u ||
         machine->board_latch_previous == 0x13u))
        return observed_read(machine, address, machine->board_latch_previous);
    if (address == BOARD_PORT_INPUT)
        return observed_read(machine, address, machine->board_port_input);
    if (address == RTC_PORT)
        return observed_read(machine, address, machine->rtc.port);
    if (address >= BOARD_REGISTER_BASE && address < BOARD_REGISTER_BASE + 512u) {
        ++machine->mirrored_board_accesses;
        return observed_read(machine, address,
                             machine->board_registers[address - BOARD_REGISTER_BASE]);
    }
    if (address >= CONTROLLER_BASE)
        return observed_read(machine, address,
                             machine->controller_registers[address - CONTROLLER_BASE]);
    unmodelled(machine, address);
    return observed_read(machine, address, 0u);
}

void db_machine_write8(db_machine *machine, uint32_t address, uint8_t value)
{
    address &= MASK24;
    if (address < DB_RAM_SIZE) {
        uint8_t old_value = machine->ram[address];
        machine->ram[address] = value;
        if (machine->ram_write != 0 && old_value != value)
            machine->ram_write(address, old_value, value,
                               machine->ram_write_context);
        machine->dirty_pages[address / 4096u / 8u] |=
            (uint8_t)(1u << ((address / 4096u) % 8u));
        return;
    }
    if (machine->mmio_trace != 0)
        machine->mmio_trace(address, 1, value, machine->mmio_trace_context);
    if (address == MAIN_DATA) {
        if (machine->tx != 0)
            machine->tx(value, machine->tx_context);
        return;
    }
    if (address == BOARD_LATCH) {
        machine->board_latch_previous = machine->board_latch_last;
        machine->board_latch_last = value;
        return;
    }
    if (address == BOARD_PORT_INPUT) {
        machine->board_port_input = value;
        return;
    }
    if (address == BOARD_PORT_SET) {
        machine->board_port_input |= value;
        return;
    }
    if (address == BOARD_PORT_CLEAR) {
        machine->board_port_input &= (uint8_t)~value;
        return;
    }
    if (address == RTC_PORT) {
        machine->controller_registers[address - CONTROLLER_BASE] =
            db_rtc_port_write(&machine->rtc, value);
        return;
    }
    if (address >= BOARD_REGISTER_BASE && address < BOARD_REGISTER_BASE + 512u) {
        ++machine->mirrored_board_accesses;
        machine->board_registers[address - BOARD_REGISTER_BASE] = value;
        return;
    }
    if (address >= CONTROLLER_BASE) {
        machine->controller_registers[address - CONTROLLER_BASE] = value;
        return;
    }
    unmodelled(machine, address);
}

uint16_t db_machine_read16(db_machine *machine, uint32_t address)
{
    return (uint16_t)(((uint16_t)db_machine_read8(machine, address) << 8) |
                      db_machine_read8(machine, address + 1u));
}

uint32_t db_machine_read32(db_machine *machine, uint32_t address)
{
    return ((uint32_t)db_machine_read16(machine, address) << 16) |
           db_machine_read16(machine, address + 2u);
}

void db_machine_write16(db_machine *machine, uint32_t address, uint16_t value)
{
    db_machine_write8(machine, address, (uint8_t)(value >> 8));
    db_machine_write8(machine, address + 1u, (uint8_t)value);
}

void db_machine_write32(db_machine *machine, uint32_t address, uint32_t value)
{
    db_machine_write16(machine, address, (uint16_t)(value >> 16));
    db_machine_write16(machine, address + 2u, (uint16_t)value);
}
