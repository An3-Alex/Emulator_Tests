idf_component_register(
    SRCS
        "app_main.c"
        "../core/db_machine.c"
        "../core/db_rtc.c"
        "../core/db_cpu.c"
        "../core/db_clock.c"
        "../generated/m68kops.c"
        "../../third_party/Musashi/m68kcpu.c"
        "../../third_party/Musashi/softfloat/softfloat.c"
    INCLUDE_DIRS
        "."
        "../core"
        "../generated"
        "../../third_party/Musashi"
        "../../third_party/Musashi/softfloat"
    REQUIRES esp_partition esp_timer esp_driver_uart esp_driver_usb_serial_jtag esp_psram
)
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    M68K_EMULATE_030=0 M68K_EMULATE_040=0 M68K_EMULATE_PMMU=0
    M68K_INSTRUCTION_HOOK=1)
