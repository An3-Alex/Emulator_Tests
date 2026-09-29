#ifndef M90_DB_RTC_H
#define M90_DB_RTC_H

#include <stdint.h>

#define DB_RTC_DATA 0x80u
#define DB_RTC_CE 0x40u
#define DB_RTC_WR 0x20u
#define DB_RTC_CLK 0x10u
#define DB_RTC_BITS 52u

typedef struct {
    uint16_t year;
    uint8_t month, day, hour, minute, second;
    uint8_t previous_port, port;
    uint8_t read_bits[DB_RTC_BITS];
    uint8_t received_bits[DB_RTC_BITS];
    uint8_t read_index, received_count;
    uint32_t cycle_remainder;
    uint32_t completed_reads, completed_writes;
} db_rtc;

void db_rtc_init(db_rtc *rtc);
uint8_t db_rtc_port_write(db_rtc *rtc, uint8_t value);
void db_rtc_advance_cycles(db_rtc *rtc, uint32_t cycles);

#endif
