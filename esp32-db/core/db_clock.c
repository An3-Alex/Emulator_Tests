#include "db_clock.h"

uint64_t db_clock_target_us(uint64_t total_cycles)
{
    /* 16 emulated cycles per microsecond; round up so we never run ahead. */
    return total_cycles / 16u + (total_cycles % 16u != 0u);
}
