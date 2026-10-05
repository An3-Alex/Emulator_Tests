#ifndef M90_DISPLAY_POLICY_H
#define M90_DISPLAY_POLICY_H

/* Small pure policy: rendering adapter and presentation monitor are separate.
 * Logical adapter 0 is Windows primary, adapter 1 is Windows secondary.
 * Both use the same SwiftShader engine in non-exclusive windowed mode.
 */
struct M90DisplayPlacement {
    long x, y;
    unsigned int width, height;
};

static bool m90_display_placement(unsigned int adapter, long left, long top,
    long right, long bottom, unsigned int width, unsigned int height,
    M90DisplayPlacement *placement)
{
    if (!placement || adapter > 1 || right <= left || bottom <= top) return false;
    placement->x = left;
    placement->y = top;
    placement->width = width ? width : (unsigned int)(right - left);
    placement->height = height ? height : (unsigned int)(bottom - top);
    return true;
}
#endif
