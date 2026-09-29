#define INSTALLER_TITLE "QEMU HDA XP one-shot installer v1 (SetupAPI only)\r\n"
#define INSTALLER_INF_PATH "C:\\NVRAM\\hda-driver\\hdaudio-qemu.inf"
#define INSTALLER_HARDWARE_ID \
    "HDAUDIO\\FUNC_01&VEN_1AF4&DEV_0022&SUBSYS_1AF40022&REV_1001"
#define INSTALLER_ENUMERATOR "HDAUDIO"
#define INSTALLER_LOG_PATH "C:\\NVRAM\\hda_install.log"
#include "qxl_installer.c"
