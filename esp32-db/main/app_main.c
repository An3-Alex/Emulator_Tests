#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#if CONFIG_M90_DB_UART_TEST
#include "driver/uart.h"
#endif
#if CONFIG_M90_DB_USB_BRIDGE
#include "driver/usb_serial_jtag.h"
#endif

#include "db_cpu.h"
#include "db_clock.h"
#include "esp_timer.h"

#define IMAGE_HEADER_SIZE 4096u
#define IMAGE_RECORD_SIZE (IMAGE_HEADER_SIZE + DB_RAM_SIZE)
#define IMAGE_HEADER_FIELDS 24u

static const char *TAG = "m90_db_probe";
static const uint8_t image_magic[8] = {'M','9','0','S','3','R','A','M'};
/* Keep the MMIO register mirrors off the small FreeRTOS app_main stack. */
static db_machine machine;
static uint8_t first_tx[64];
static uint32_t tx_count;
static uint8_t pending_tx[64];
static unsigned int pending_tx_count;
static uint32_t dropped_tx;
static uint32_t wire_tx_count;
static uint32_t wire_tx_errors;
static uint32_t wire_rx_count;

#if CONFIG_M90_DB_USB_BRIDGE
static int init_usb_bridge(void)
{
    usb_serial_jtag_driver_config_t config = {
        .tx_buffer_size = 8192,
        .rx_buffer_size = 8192,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "DB_USB_BRIDGE driver install failed: %s", esp_err_to_name(err));
        return 0;
    }
    ESP_LOGI(TAG, "DB_USB_BRIDGE waiting for M90GO handshake on native USB");
    {
        const char hello[] = "M90GO\n";
        size_t matched = 0;
        uint8_t byte;
        while (matched < sizeof(hello) - 1u) {
            if (usb_serial_jtag_read_bytes(&byte, 1, pdMS_TO_TICKS(100)) != 1)
                continue;
            matched = byte == (uint8_t)hello[matched] ? matched + 1u :
                      (byte == (uint8_t)hello[0] ? 1u : 0u);
        }
    }
    {
        static const char ready[] = "M90READY\n";
        if (usb_serial_jtag_write_bytes(ready, sizeof(ready) - 1u,
                                        pdMS_TO_TICKS(1000)) != sizeof(ready) - 1u) {
            ESP_LOGE(TAG, "DB_USB_BRIDGE ready reply failed");
            return 0;
        }
        usb_serial_jtag_wait_tx_done(pdMS_TO_TICKS(1000));
    }
    ESP_LOGI(TAG, "DB_USB_BRIDGE host connected; database CPU starting");
    return 1;
}

static void poll_usb_rx(void)
{
    uint8_t byte;
    if (machine.rx_pending ||
        usb_serial_jtag_read_bytes(&byte, 1, 0) != 1)
        return;
    machine.rx_byte = byte;
    machine.rx_pending = 1u;
    ++wire_rx_count;
    /* The PC relay records every byte. Per-byte UART0 logging here would
     * throttle the database CPU and disturb serial-response timing. */
}
#endif

#if CONFIG_M90_DB_UART_TEST
static int unsafe_uart_gpio(int pin)
{
    return pin == 0 || pin == 3 || pin == 19 || pin == 20 ||
           (pin >= 26 && pin <= 37) || pin == 43 || pin == 44 ||
           pin == 45 || pin == 46;
}

static int init_wire_uart(void)
{
    const int tx_pin = CONFIG_M90_DB_UART_TX_GPIO;
    const int rx_pin = CONFIG_M90_DB_UART_RX_GPIO;
    const uart_config_t config = {
        .baud_rate = 9600,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err;
    if (tx_pin == rx_pin || unsafe_uart_gpio(tx_pin) ||
        unsafe_uart_gpio(rx_pin)) {
        ESP_LOGE(TAG, "unsafe UART GPIO selection: TX=%d RX=%d", tx_pin, rx_pin);
        return 0;
    }
    err = uart_param_config(UART_NUM_1, &config);
    if (err == ESP_OK)
        err = uart_set_pin(UART_NUM_1, tx_pin, rx_pin,
                           UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err == ESP_OK)
        err = uart_driver_install(UART_NUM_1, 2048, 2048, 0, NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "UART1 setup failed: %s", esp_err_to_name(err));
        return 0;
    }
    ESP_LOGI(TAG, "DB_UART_WIRE enabled TX_GPIO=%d RX_GPIO=%d baud=9600 8N1",
             tx_pin, rx_pin);
    return 1;
}

static void poll_wire_rx(void)
{
    uint8_t byte;
    if (machine.rx_pending ||
        uart_read_bytes(UART_NUM_1, &byte, 1, 0) != 1)
        return;
    machine.rx_byte = byte;
    machine.rx_pending = 1u;
    ++wire_rx_count;
    ESP_LOGI(TAG, "DB_RX byte=%02X total=%lu", byte,
             (unsigned long)wire_rx_count);
}
#endif

static void capture_tx(uint8_t value, void *context)
{
    (void)context;
    if (db_cpu_pc() < 0x1000u)
        return; /* Ignore loader programming/idle bytes. */
    if (tx_count < sizeof(first_tx))
        first_tx[tx_count] = value;
    if (pending_tx_count < sizeof(pending_tx))
        pending_tx[pending_tx_count++] = value;
    else
        ++dropped_tx;
#if CONFIG_M90_DB_UART_TEST
    if (uart_write_bytes(UART_NUM_1, &value, 1) == 1)
        ++wire_tx_count;
    else
        ++wire_tx_errors;
#elif CONFIG_M90_DB_USB_BRIDGE
    if (usb_serial_jtag_write_bytes(&value, 1, pdMS_TO_TICKS(100)) == 1)
        ++wire_tx_count;
    else
        ++wire_tx_errors;
#endif
    ++tx_count;
}

static void log_pending_tx(void)
{
#if CONFIG_M90_DB_USB_BRIDGE
    /* The host event log already contains the transmitted bytes. */
    pending_tx_count = 0u;
#else
    char hex[sizeof(pending_tx) * 3u + 1u];
    size_t cursor = 0u;
    unsigned int i;
    if (pending_tx_count == 0u)
        return;
    for (i = 0u; i < pending_tx_count; ++i)
        cursor += (size_t)snprintf(hex + cursor, sizeof(hex) - cursor,
                                   "%02X%s", pending_tx[i],
                                   i + 1u == pending_tx_count ? "" : " ");
    ESP_LOGI(TAG, "DB_TX bytes=%s total=%lu", hex, (unsigned long)tx_count);
    pending_tx_count = 0u;
#endif
}

static void pace_cpu(uint64_t total_cycles, void *context)
{
    int64_t start_us = *(const int64_t *)context;
    int64_t deadline = start_us + (int64_t)db_clock_target_us(total_cycles);
    for (;;) {
        int64_t remaining = deadline - esp_timer_get_time();
        if (remaining <= 0)
            break;
        if (remaining > 2000)
            vTaskDelay(1);
        /* For the final <=2 ms, spin against the monotonic clock. It avoids
         * oversleeping an entire RTOS tick for a 1000-cycle CPU slice. */
    }
}

static uint32_t be32(const uint8_t *data)
{
    return ((uint32_t)data[0] << 24) | ((uint32_t)data[1] << 16) |
           ((uint32_t)data[2] << 8) | data[3];
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t size)
{
    size_t i;
    for (i = 0; i < size; ++i) {
        unsigned int bit;
        crc ^= data[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}

static int valid_image(const esp_partition_t *part, uint32_t *generation,
                       uint8_t *scratch)
{
    uint8_t header[IMAGE_HEADER_FIELDS];
    uint32_t expected_crc;
    uint32_t crc = 0xffffffffu;
    size_t offset;
    if (part == NULL || part->size < IMAGE_RECORD_SIZE ||
        esp_partition_read(part, 0, header, sizeof(header)) != ESP_OK ||
        memcmp(header, image_magic, sizeof(image_magic)) != 0 ||
        be32(header + 8) != 1u || be32(header + 16) != DB_RAM_SIZE)
        return 0;
    *generation = be32(header + 12);
    expected_crc = be32(header + 20);
    for (offset = 0; offset < DB_RAM_SIZE; offset += 4096u) {
        if (esp_partition_read(part, IMAGE_HEADER_SIZE + offset,
                               scratch, 4096u) != ESP_OK)
            return 0;
        crc = crc32_update(crc, scratch, 4096u);
    }
    return (crc ^ 0xffffffffu) == expected_crc;
}

void app_main(void)
{
    const char *labels[] = {"db_seed", "db_a", "db_b"};
    const esp_partition_t *selected = NULL;
    uint32_t selected_generation = 0u;
    uint8_t *scratch = malloc(4096u);
    uint8_t *ram;
    unsigned int i;
    int uart_ready_logged = 0;
    uint16_t previous_uart_state = 0u;
    uint32_t previous_rtc_reads = 0u;
    uint32_t previous_board_ticks = 0u;
    int64_t clock_start_us;

    if (scratch == NULL) {
        ESP_LOGE(TAG, "cannot allocate CRC scratch buffer");
        return;
    }
    for (i = 0; i < sizeof(labels) / sizeof(labels[0]); ++i) {
        const esp_partition_t *part = esp_partition_find_first(
            ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, labels[i]);
        uint32_t generation = 0u;
        if (valid_image(part, &generation, scratch) &&
            (selected == NULL || generation > selected_generation)) {
            selected = part;
            selected_generation = generation;
        }
    }
    free(scratch);
    if (selected == NULL) {
        ESP_LOGE(TAG, "no valid 2 MiB RAM image; CPU will not start");
        return;
    }
    ram = heap_caps_malloc(DB_RAM_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ram == NULL || esp_partition_read(selected, IMAGE_HEADER_SIZE,
                                          ram, DB_RAM_SIZE) != ESP_OK) {
        ESP_LOGE(TAG, "PSRAM allocation or image read failed");
        free(ram);
        return;
    }
    ESP_LOGI(TAG, "loaded %s generation=%lu into PSRAM",
             selected->label, (unsigned long)selected_generation);

#if CONFIG_M90_DB_UART_TEST
    if (!init_wire_uart()) {
        free(ram);
        return;
    }
#elif CONFIG_M90_DB_USB_BRIDGE
    if (!init_usb_bridge()) {
        free(ram);
        return;
    }
#else
    ESP_LOGI(TAG, "DB_UART_WIRE disabled (USB log only)");
#endif

    db_machine_init(&machine, ram);
    machine.tx = capture_tx;
    db_cpu_boot(&machine);
    clock_start_us = esp_timer_get_time();
    db_cpu_set_cycle_callback(pace_cpu, &clock_start_us);
    ESP_LOGI(TAG, "DB_CLOCK max=16.00 emulated MHz; CPU cycle counts are approximate");
#if CONFIG_M90_DB_UART_TEST
    ESP_LOGI(TAG, "DB_RX source=UART1; physical bus/control pins are not emulated");
#elif CONFIG_M90_DB_USB_BRIDGE
    ESP_LOGI(TAG, "DB_RX source=native USB Serial/JTAG; diagnostics on UART0");
#else
    ESP_LOGI(TAG, "DB_RX source=none; PC replies and physical controller are not connected");
#endif
    /* The owner-image host probe reaches the UART boundary after about
     * 131 million emulated cycles.  Ten million cycles was too short. */
    for (i = 0; ; ++i) {
#if !CONFIG_M90_DB_USB_BRIDGE
        if (i == 400000u)
            break;
#endif
#if CONFIG_M90_DB_UART_TEST
        poll_wire_rx();
#elif CONFIG_M90_DB_USB_BRIDGE
        poll_usb_rx();
#endif
        db_cpu_execute(1000);
        log_pending_tx();
        {
            uint16_t state = db_machine_read16(&machine, 0x001ebbb0u);
            if (state != previous_uart_state) {
                ESP_LOGI(TAG, "DB_UART_STATE %04X->%04X PC=%08lx",
                         previous_uart_state, state, (unsigned long)db_cpu_pc());
                previous_uart_state = state;
            }
        }
        if (machine.rtc.completed_reads != previous_rtc_reads) {
            previous_rtc_reads = machine.rtc.completed_reads;
            ESP_LOGI(TAG, "DB_RTC_READ count=%lu date=%u-%02u-%02u %02u:%02u:%02u",
                     (unsigned long)previous_rtc_reads, machine.rtc.year,
                     machine.rtc.month, machine.rtc.day, machine.rtc.hour,
                     machine.rtc.minute, machine.rtc.second);
        }
        if (db_cpu_board_ticks() != previous_board_ticks) {
            previous_board_ticks = db_cpu_board_ticks();
            if (previous_board_ticks == 1u || previous_board_ticks % 100u == 0u)
                ESP_LOGI(TAG, "DB_BOARD_TIMER ticks=%lu uart_ticks=%lu",
                         (unsigned long)previous_board_ticks,
                         (unsigned long)db_cpu_uart_ticks());
        }
        if (db_cpu_uart_init_result() < 0) {
            ESP_LOGE(TAG, "original UART initializer did not return ready; halted");
            break;
        }
        if (db_cpu_irq_error() != 0) {
            ESP_LOGE(TAG, "timer/IRQ vector or stack invalid: %d; halted",
                     db_cpu_irq_error());
            break;
        }
        if (!uart_ready_logged && db_cpu_uart_init_result() == 1 &&
            db_machine_read16(&machine, 0x001ebbb0u) == 0x58a9u) {
            ESP_LOGI(TAG, "original UART initializer reached ready word at chunk %u", i);
            uart_ready_logged = 1;
        }
        if (machine.unmodelled_accesses != 0u) {
            ESP_LOGW(TAG, "first unmodelled MMIO=%08lx PC=%08lx; halted safely",
                     (unsigned long)machine.first_unmodelled_address,
                     (unsigned long)db_cpu_pc());
            break;
        }
        if (i != 0u && i % 50000u == 0u) {
            uint64_t elapsed_us = (uint64_t)(esp_timer_get_time() - clock_start_us);
            uint64_t mhz_x100 = elapsed_us ?
                db_cpu_total_cycles() * 100u / elapsed_us : 0u;
            ESP_LOGI(TAG, "DB_PROGRESS chunk=%u PC=%08lx TX=%lu average=%lu.%02lu MHz",
                     i, (unsigned long)db_cpu_pc(), (unsigned long)tx_count,
                     (unsigned long)(mhz_x100 / 100u),
                     (unsigned long)(mhz_x100 % 100u));
        }
        if ((i & 255u) == 255u)
            vTaskDelay(1);
    }
    ESP_LOGI(TAG, "CPU probe PC=%08lx UART=%d board_ticks=%lu uart_ticks=%lu phase=%d TX=%lu rtc_reads=%lu tx_dropped=%lu wire_tx=%lu wire_tx_errors=%lu wire_rx=%lu (not cabinet-ready)",
             (unsigned long)db_cpu_pc(), db_cpu_uart_init_result(),
             (unsigned long)db_cpu_board_ticks(),
             (unsigned long)db_cpu_uart_ticks(), db_cpu_irq_phase(),
             (unsigned long)tx_count,
             (unsigned long)machine.rtc.completed_reads,
             (unsigned long)dropped_tx,
             (unsigned long)wire_tx_count,
             (unsigned long)wire_tx_errors,
             (unsigned long)wire_rx_count);
    if (tx_count != 0u) {
        char hex[sizeof(first_tx) * 3u + 1u];
        size_t count = tx_count < sizeof(first_tx) ? tx_count : sizeof(first_tx);
        size_t cursor = 0u;
        for (i = 0; i < count; ++i)
            cursor += (size_t)snprintf(hex + cursor, sizeof(hex) - cursor,
                                       "%02X%s", first_tx[i],
                                       i + 1u == count ? "" : " ");
        ESP_LOGI(TAG, "database TX sample (%u/%lu bytes): %s",
                 (unsigned int)count, (unsigned long)tx_count, hex);
    }
    free(ram);
}
