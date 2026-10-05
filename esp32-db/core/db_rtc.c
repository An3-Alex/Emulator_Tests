#include "db_rtc.h"

#include <string.h>

#define DB_RTC_CYCLES_PER_SECOND 16000000u

static uint8_t bcd(uint8_t value)
{
    return (uint8_t)(((value / 10u) << 4) | (value % 10u));
}

static int unbcd(uint8_t value)
{
    if ((value & 15u) > 9u || (value >> 4) > 9u)
        return -1;
    return (int)((value >> 4) * 10u + (value & 15u));
}

static int leap_year(unsigned int year)
{
    return year % 4u == 0u && (year % 100u != 0u || year % 400u == 0u);
}

static unsigned int month_days(unsigned int year, unsigned int month)
{
    static const uint8_t days[] = {31, 28, 31, 30, 31, 30,
                                   31, 31, 30, 31, 30, 31};
    if (month < 1u || month > 12u)
        return 0u;
    return days[month - 1u] + (month == 2u && leap_year(year));
}

/* Epson convention: Sunday=1, Monday=2, ..., Saturday=7. */
static uint8_t weekday(unsigned int year, unsigned int month,
                       unsigned int day)
{
    static const uint8_t offsets[] = {0, 3, 2, 5, 0, 3,
                                      5, 1, 4, 6, 2, 4};
    unsigned int adjusted_year = year - (month < 3u);
    unsigned int sunday_zero = (adjusted_year + adjusted_year / 4u -
        adjusted_year / 100u + adjusted_year / 400u +
        offsets[month - 1u] + day) % 7u;
    return (uint8_t)(sunday_zero + 1u);
}

static void calendar_fields(const db_rtc *rtc, uint8_t fields[7])
{
    fields[0] = bcd(rtc->second);
    fields[1] = bcd(rtc->minute);
    fields[2] = bcd(rtc->hour);
    fields[3] = weekday(rtc->year, rtc->month, rtc->day);
    fields[4] = bcd(rtc->day);
    fields[5] = bcd(rtc->month);
    fields[6] = bcd((uint8_t)(rtc->year % 100u));
}

static void prepare_read(db_rtc *rtc)
{
    static const uint8_t widths[7] = {8, 8, 8, 4, 8, 8, 8};
    uint8_t fields[7];
    unsigned int cursor = 0u;
    unsigned int field;
    calendar_fields(rtc, fields);
    for (field = 0u; field < 7u; ++field) {
        unsigned int bit;
        for (bit = 0u; bit < widths[field]; ++bit)
            rtc->read_bits[cursor++] = (uint8_t)((fields[field] >> bit) & 1u);
    }
}

static void accept_write(db_rtc *rtc)
{
    static const uint8_t widths[7] = {8, 8, 8, 4, 8, 8, 8};
    uint8_t fields[7] = {0};
    unsigned int cursor = 0u;
    unsigned int field;
    int second, minute, hour, day, month, year;
    if (rtc->received_count != DB_RTC_BITS)
        return;
    for (field = 0u; field < 7u; ++field) {
        unsigned int bit;
        for (bit = 0u; bit < widths[field]; ++bit)
            fields[field] |= (uint8_t)(rtc->received_bits[cursor++] << bit);
    }
    second = unbcd(fields[0]);
    minute = unbcd(fields[1]);
    hour = unbcd(fields[2]);
    day = unbcd(fields[4]);
    month = unbcd(fields[5]);
    year = unbcd(fields[6]);
    if (year < 0 || month < 1 || month > 12 || day < 1 ||
        day > (int)month_days((unsigned int)(2000 + year), (unsigned int)month) ||
        hour < 0 || hour > 23 || minute < 0 || minute > 59 ||
        second < 0 || second > 59 ||
        fields[3] != weekday((unsigned int)(2000 + year),
                             (unsigned int)month, (unsigned int)day))
        return;
    rtc->year = (uint16_t)(2000 + year);
    rtc->month = (uint8_t)month;
    rtc->day = (uint8_t)day;
    rtc->hour = (uint8_t)hour;
    rtc->minute = (uint8_t)minute;
    rtc->second = (uint8_t)second;
    rtc->cycle_remainder = 0u;
    ++rtc->completed_writes;
}

void db_rtc_init(db_rtc *rtc)
{
    memset(rtc, 0, sizeof(*rtc));
    /* Fixed offline seed from the existing PC bridge, not a real time source. */
    rtc->year = 2012u;
    rtc->month = 2u;
    rtc->day = 1u;
    rtc->hour = 22u;
    rtc->minute = 14u;
}

uint8_t db_rtc_port_write(db_rtc *rtc, uint8_t value)
{
    uint8_t previous = rtc->previous_port;
    int enabled = (value & DB_RTC_CE) != 0u;
    int was_enabled = (previous & DB_RTC_CE) != 0u;
    if (enabled && !was_enabled) {
        rtc->read_index = 0u;
        rtc->received_count = 0u;
        prepare_read(rtc);
    }
    if (enabled && !(previous & DB_RTC_CLK) && (value & DB_RTC_CLK)) {
        if (value & DB_RTC_WR) {
            if (rtc->received_count < DB_RTC_BITS)
                rtc->received_bits[rtc->received_count++] =
                    (uint8_t)((value & DB_RTC_DATA) != 0u);
        } else if (rtc->read_index < DB_RTC_BITS) {
            value = (uint8_t)((value & ~DB_RTC_DATA) |
                (rtc->read_bits[rtc->read_index++] ? DB_RTC_DATA : 0u));
            if (rtc->read_index == DB_RTC_BITS)
                ++rtc->completed_reads;
        }
    }
    if (was_enabled && !enabled && (previous & DB_RTC_WR))
        accept_write(rtc);
    rtc->previous_port = value;
    rtc->port = value;
    return value;
}

void db_rtc_advance_cycles(db_rtc *rtc, uint32_t cycles)
{
    rtc->cycle_remainder += cycles;
    while (rtc->cycle_remainder >= DB_RTC_CYCLES_PER_SECOND) {
        rtc->cycle_remainder -= DB_RTC_CYCLES_PER_SECOND;
        if (++rtc->second < 60u)
            continue;
        rtc->second = 0u;
        if (++rtc->minute < 60u)
            continue;
        rtc->minute = 0u;
        if (++rtc->hour < 24u)
            continue;
        rtc->hour = 0u;
        if (++rtc->day <= month_days(rtc->year, rtc->month))
            continue;
        rtc->day = 1u;
        if (++rtc->month <= 12u)
            continue;
        rtc->month = 1u;
        ++rtc->year;
    }
}
