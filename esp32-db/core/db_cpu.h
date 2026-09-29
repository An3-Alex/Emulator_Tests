#ifndef M90_DB_CPU_H
#define M90_DB_CPU_H

#include "db_machine.h"

typedef void (*db_cycle_callback)(uint64_t total_cycles, void *context);

void db_cpu_boot(db_machine *machine);
int db_cpu_execute(int cycles);
uint32_t db_cpu_pc(void);
uint64_t db_cpu_total_cycles(void);
void db_cpu_set_cycle_callback(db_cycle_callback callback, void *context);
/* 0 = not yet invoked, 1 = original initializer returned ready, -1 = failed. */
int db_cpu_uart_init_result(void);
/* Diagnostic ISR counters; these do not prove electrical timing. */
uint32_t db_cpu_board_ticks(void);
uint32_t db_cpu_uart_ticks(void);
int db_cpu_irq_phase(void);
int db_cpu_irq_error(void);

#endif
