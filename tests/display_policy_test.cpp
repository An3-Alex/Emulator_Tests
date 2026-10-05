#include "../src/display_policy.h"

/* Pure native policy checks; no window, Direct3D or VM is created. */
int main()
{
    M90DisplayPlacement p;
    if (!m90_display_placement(0, 0, 0, 640, 480, 0, 0, &p) ||
        p.x != 0 || p.y != 0 || p.width != 640 || p.height != 480) return 1;
    if (!m90_display_placement(1, 640, 0, 1280, 480, 640, 480, &p) ||
        p.x != 640 || p.y != 0 || p.width != 640 || p.height != 480) return 2;
    if (!m90_display_placement(1, -800, -600, 0, 0, 0, 0, &p) ||
        p.x != -800 || p.y != -600 || p.width != 800 || p.height != 600) return 3;
    if (!m90_display_placement(1, 0, 480, 640, 960, 320, 240, &p) ||
        p.x != 0 || p.y != 480 || p.width != 320 || p.height != 240) return 4;
    if (m90_display_placement(2, 0, 0, 640, 480, 0, 0, &p)) return 5;
    if (m90_display_placement(0, 0, 0, 0, 480, 0, 0, &p)) return 6;
    if (m90_display_placement(0, 0, 0, 640, 0, 0, 0, &p)) return 7;
    if (m90_display_placement(0, 0, 0, 640, 480, 0, 0, 0)) return 8;
    return 0;
}
