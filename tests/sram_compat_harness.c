/* Run in a disposable working directory: no guest image or QEMU needed. */
#define SRAM_BACKING_PATH L"sram-harness.bin"
#include "../src/sram_compat.c"

#define CHECK(condition, code) do { if (!(condition)) ExitProcess(code); } while (0)

static DWORD WINAPI exercise_handle(LPVOID argument) {
    HANDLE file = (HANDLE)argument;
    unsigned char request[9] = {0x80, 0, 0, 3, 0, 0, 'X', 'Y', 'Z'};
    unsigned char readback[3];
    DWORD bytes, n;
    for (n = 0; n < 200; ++n) {
        if (!hook_device_io_control(file, SRAM_IOCTL_WRITE, request, 9, NULL, 0, &bytes, NULL)) return 1;
        if (!hook_device_io_control(file, SRAM_IOCTL_READ, request, 6, readback, 3, &bytes, NULL)) return 2;
        if (readback[0] != 'X' || readback[1] != 'Y' || readback[2] != 'Z' || bytes != 3) return 3;
    }
    return 0;
}

void mainCRTStartup(void) {
    HANDLE a, b, thread_a, thread_b, ordinary;
    HDEVINFO set;
    SP_DEVICE_INTERFACE_DATA interface_data;
    unsigned char detail[64], request[9] = {0x20, 0, 0, 3, 0, 0, 'A', 'B', 'C'};
    unsigned char output[3];
    DWORD required, bytes = 99, exit_a, exit_b;
    InitializeCriticalSection(&g_sram_lock);
    g_log = CreateFileA("sram-harness.log", FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    set = hook_get_class_devs_a(&g_sram_guid, NULL, NULL, DIGCF_DEVICEINTERFACE);
    CHECK((HANDLE)set == g_fake_devinfo, 10);
    clear_bytes(&interface_data, sizeof(interface_data));
    interface_data.cbSize = sizeof(interface_data);
    CHECK(hook_enum_device_interfaces(set, NULL, &g_sram_guid, 0, &interface_data), 11);
    CHECK(hook_enum_device_interfaces(set, NULL, &g_sram_guid, 1, &interface_data), 12);
    CHECK(!hook_enum_device_interfaces(set, NULL, &g_sram_guid, 2, &interface_data) &&
          GetLastError() == ERROR_NO_MORE_ITEMS, 13);
    CHECK(!hook_get_device_interface_detail_a(set, &interface_data, NULL, 0, &required, NULL) &&
          GetLastError() == ERROR_INSUFFICIENT_BUFFER, 14);
    CHECK(required <= sizeof(detail), 15);
    CHECK(hook_get_device_interface_detail_a(set, &interface_data,
        (PSP_DEVICE_INTERFACE_DETAIL_DATA_A)detail, sizeof(detail), &required, NULL), 16);
    CHECK(lstrcmpA((char *)detail + 4, "\\\\?\\M90SRAM") == 0, 17);
    CHECK(hook_destroy_device_info_list(set), 18);
    a = hook_create_file_a((char *)detail + 4, 0, 0, NULL, OPEN_EXISTING, 0, NULL);
    b = hook_create_file_w(g_fake_device_path, 0, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(a != INVALID_HANDLE_VALUE && b != INVALID_HANDLE_VALUE && a != b, 20);
    CHECK(GetFileSize(a, NULL) == SRAM_SIZE, 21);
    CHECK(hook_device_io_control(a, SRAM_IOCTL_WRITE, request, 9, NULL, 0, &bytes, NULL) && bytes == 0, 22);
    CHECK(hook_device_io_control(b, SRAM_IOCTL_READ, request, 6, output, 3, &bytes, NULL) && bytes == 3, 23);
    CHECK(output[0] == 'A' && output[1] == 'B' && output[2] == 'C', 24);
    CHECK(!hook_device_io_control(a, SRAM_IOCTL_READ, request, 5, output, 3, &bytes, NULL), 25);
    CHECK(!hook_device_io_control(a, SRAM_IOCTL_READ, request, 6, output, 2, &bytes, NULL), 26);
    CHECK(!hook_device_io_control(a, SRAM_IOCTL_WRITE, request, 8, NULL, 0, &bytes, NULL), 27);
    request[0] = request[1] = request[2] = 0xFF;
    CHECK(!hook_device_io_control(a, SRAM_IOCTL_READ, request, 6, output, 3, &bytes, NULL), 28);
    thread_a = CreateThread(NULL, 0, exercise_handle, a, 0, NULL);
    thread_b = CreateThread(NULL, 0, exercise_handle, b, 0, NULL);
    CHECK(thread_a && thread_b, 29);
    CHECK(WaitForSingleObject(thread_a, 30000) == WAIT_OBJECT_0, 30);
    CHECK(WaitForSingleObject(thread_b, 30000) == WAIT_OBJECT_0, 31);
    CHECK(GetExitCodeThread(thread_a, &exit_a) && GetExitCodeThread(thread_b, &exit_b) && !exit_a && !exit_b, 32);
    CloseHandle(thread_a); CloseHandle(thread_b);
    CHECK(hook_close_handle(a), 33);
    CHECK(!hook_device_io_control(a, SRAM_IOCTL_READ, request, 6, output, 3, &bytes, NULL), 34);
    a = hook_create_file_a("\\\\?\\M90SRAM", 0, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(a != INVALID_HANDLE_VALUE && hook_close_handle(a) && hook_close_handle(b), 35);
    ordinary = hook_create_file_a("ordinary.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                                 FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(ordinary != INVALID_HANDLE_VALUE && hook_close_handle(ordinary), 36);
    log_text("SRAM_HARNESS_PASS ANSI+Unicode, two handles, shared reads/writes, bounds, concurrency, close\r\n");
    CloseHandle(g_log);
    ExitProcess(0);
}
