#ifndef M90_DB_CLOCK_H
#define M90_DB_CLOCK_H

#include <stdint.h>

#define DB_CPU_MAX_CYCLES_PER_SECOND 16000000u

/* Earliest elapsed wall time permitted for this many emulated CPU cycles. */
uint64_t db_clock_target_us(uint64_t total_cycles);

#endif
