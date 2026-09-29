#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "db_cpu.h"

static unsigned int tx_count;
static unsigned int uart_writes;
static uint8_t first_frame[39];

typedef struct {
    uint32_t reads;
    uint32_t writes;
    uint32_t first_pc;
    uint8_t first_value;
    uint8_t last_value;
    uint8_t changed_bits;
    uint8_t seen;
} mmio_entry;

static mmio_entry board_trace[512];
static mmio_entry controller_trace[4096];
static uint32_t other_mmio_count;
static uint32_t first_other_mmio;

static void trace_mmio(uint32_t address, int is_write, uint8_t value,
                       void *context)
{
    mmio_entry *entry;
    (void)context;
    if (address >= 0x00800000u && address < 0x00800200u)
        entry = &board_trace[address - 0x00800000u];
    else if (address >= 0x00fff000u)
        entry = &controller_trace[address - 0x00fff000u];
    else {
        if (other_mmio_count++ == 0u)
            first_other_mmio = address;
        return;
    }
    if (!entry->seen) {
        entry->seen = 1u;
        entry->first_pc = db_cpu_pc();
        entry->first_value = value;
        entry->last_value = value;
    }
    if (is_write) {
        ++entry->writes;
        entry->changed_bits |= entry->last_value ^ value;
        entry->last_value = value;
    } else {
        ++entry->reads;
    }
}

static void print_mmio_trace(void)
{
    unsigned int index;
    for (index = 0; index < 512u; ++index) {
        mmio_entry *entry = &board_trace[index];
        if (entry->seen)
            printf("MMIO_TRACE addr=%08X read=%u write=%u changed=%02X "
                   "first=%02X last=%02X first_pc=%08X\n",
                   0x00800000u + index, entry->reads, entry->writes,
                   entry->changed_bits, entry->first_value,
                   entry->last_value, entry->first_pc);
    }
    for (index = 0; index < 4096u; ++index) {
        mmio_entry *entry = &controller_trace[index];
        if (entry->seen)
            printf("MMIO_TRACE addr=%08X read=%u write=%u changed=%02X "
                   "first=%02X last=%02X first_pc=%08X\n",
                   0x00fff000u + index, entry->reads, entry->writes,
                   entry->changed_bits, entry->first_value,
                   entry->last_value, entry->first_pc);
    }
    if (other_mmio_count != 0u)
        printf("MMIO_TRACE_OTHER first=%08X count=%u\n",
               first_other_mmio, other_mmio_count);
}

static void trace_uart_write(uint32_t address, uint8_t old_value,
                             uint8_t new_value, void *context)
{
    (void)context;
    if (address != 0x001ebbb0u && address != 0x001ebbb1u)
        return;
    if (uart_writes < 16u)
        printf("UART_WRITE pc_after_fetch=%08X addr=%08X %02X->%02X\n",
               db_cpu_pc(), address, old_value, new_value);
    ++uart_writes;
}

static void capture_tx(uint8_t byte, void *context)
{
    (void)context;
    if (db_cpu_pc() < 0x1000u)
        return; /* loader programming/idle bytes are not operating-mode COM */
    if (tx_count < sizeof(first_frame))
        first_frame[tx_count] = byte;
    if (tx_count < 64u)
        printf("%02X ", byte);
    ++tx_count;
}

int main(int argc, char **argv)
{
    FILE *input;
    uint8_t header[4096];
    uint8_t *ram;
    db_machine machine;
    unsigned int steps;
    unsigned int max_steps = 200000u;
    unsigned int uart_changes = 0u;
    unsigned int last_board_ticks = 0u;
    unsigned int last_uart_ticks = 0u;
    uint16_t previous_uart_state = 0u;
    if (argc != 2 && argc != 3) {
        fputs("usage: db-owner-probe <validated-esp32-seed.bin> [max_steps]\n", stderr);
        return 2;
    }
    if (argc == 3) {
        char *end;
        unsigned long requested = strtoul(argv[2], &end, 10);
        if (*end != '\0' || requested == 0ul || requested > 2000000ul) {
            fputs("max_steps must be 1..2000000\n", stderr);
            return 2;
        }
        max_steps = (unsigned int)requested;
    }
    input = fopen(argv[1], "rb");
    if (input == NULL) {
        perror(argv[1]);
        return 2;
    }
    ram = (uint8_t *)malloc(DB_RAM_SIZE);
    if (ram == NULL || fread(header, 1u, sizeof(header), input) != sizeof(header) ||
        memcmp(header, "M90S3RAM", 8u) != 0 ||
        fread(ram, 1u, DB_RAM_SIZE, input) != DB_RAM_SIZE ||
        fgetc(input) != EOF) {
        fputs("invalid or short seed image\n", stderr);
        fclose(input);
        free(ram);
        return 2;
    }
    fclose(input);
    db_machine_init(&machine, ram);
    machine.tx = capture_tx;
    machine.ram_write = trace_uart_write;
    machine.mmio_trace = trace_mmio;
    db_cpu_boot(&machine);
    for (steps = 0; steps < max_steps && machine.unmodelled_accesses == 0u &&
                    db_cpu_uart_init_result() >= 0 && db_cpu_irq_error() == 0;
         ++steps) {
        uint16_t uart_state;
        db_cpu_execute(1000);
        if (db_cpu_board_ticks() != last_board_ticks) {
            last_board_ticks = db_cpu_board_ticks();
            if (last_board_ticks <= 4u)
                printf("BOARD_TICK step=%u PC=%08X count=%u phase=%d\n",
                       steps, db_cpu_pc(), last_board_ticks, db_cpu_irq_phase());
        }
        if (db_cpu_uart_ticks() != last_uart_ticks) {
            last_uart_ticks = db_cpu_uart_ticks();
            if (last_uart_ticks <= 4u)
                printf("UART_TICK step=%u PC=%08X count=%u phase=%d\n",
                       steps, db_cpu_pc(), last_uart_ticks, db_cpu_irq_phase());
        }
        uart_state = db_machine_read16(&machine, 0x001ebbb0u);
        if (uart_state != previous_uart_state) {
            if (uart_changes < 16u)
                printf("UART_STATE step=%u PC=%08X %04X->%04X init=%d\n",
                       steps, db_cpu_pc(), previous_uart_state, uart_state,
                       db_cpu_uart_init_result());
            ++uart_changes;
            previous_uart_state = uart_state;
        }
    }
    printf("\nOWNER_PROBE steps=%u PC=%08X unmodelled=%08X count=%u "
           "mirrored_board=%u TX=%u uart_init=%d uart_state=%04X uart_changes=%u uart_writes=%u board_ticks=%u uart_ticks=%u irq_phase=%d irq_error=%d rtc=%u-%02u-%02uT%02u:%02u reads=%u writes=%u\n",
           steps, db_cpu_pc(), machine.first_unmodelled_address,
           machine.unmodelled_accesses, machine.mirrored_board_accesses,
           tx_count, db_cpu_uart_init_result(),
           db_machine_read16(&machine, 0x001ebbb0u), uart_changes,
           uart_writes, db_cpu_board_ticks(), db_cpu_uart_ticks(),
           db_cpu_irq_phase(), db_cpu_irq_error(), machine.rtc.year,
           machine.rtc.month, machine.rtc.day, machine.rtc.hour,
           machine.rtc.minute, machine.rtc.completed_reads,
           machine.rtc.completed_writes);
    print_mmio_trace();
    if (tx_count >= sizeof(first_frame)) {
        static const uint8_t prefix[4] = {0x01u, 0x02u, 0x22u, 0x00u};
        static const uint8_t date[6] = {0xdcu, 0x07u, 0x02u,
                                        0x01u, 0x16u, 0x0eu};
        int valid = memcmp(first_frame, prefix, sizeof(prefix)) == 0 &&
                    memcmp(first_frame + 24u, date, sizeof(date)) == 0;
        printf("INITVIDEO_2012_CHECK %s first_frame_bytes=%u\n",
               valid ? "PASS" : "FAIL", (unsigned int)sizeof(first_frame));
        if (!valid) {
            free(ram);
            return 1;
        }
    }
    free(ram);
    return 0;
}
