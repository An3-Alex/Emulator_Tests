#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef int GLint;
typedef int GLenum;
typedef struct { int width, height; uint32_t *data; } pixman_image_t;
typedef struct { bool requested; pixman_image_t *image; } QemuConsole;
enum { GL_READ_FRAMEBUFFER_BINDING, GL_READ_BUFFER, GL_PIXEL_PACK_BUFFER_BINDING,
       GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_ROWS, GL_PACK_SKIP_PIXELS,
       GL_PIXEL_PACK_BUFFER, GL_READ_FRAMEBUFFER, GL_BACK, GL_BGRA, GL_UNSIGNED_BYTE,
       PIXMAN_x8r8g8b8 };
static int framebuffer = 9, pbo = 17, buffers[10] = {55,0,0,0,0,0,0,0,0,77};
static int pack[4] = {8,13,3,5}, reads, valid_size = 1;
static QemuConsole console;
static unsigned mesa_current_output(void) { return 0; }
#define MESA_PFN(type, name) void *p_##name = (void *)1
#define PFN_CALL(call) call
#define g_malloc malloc
#define g_free free
static QemuConsole *qemu_console_lookup_by_index(int index) { assert(index == 0); return &console; }
static bool qemu_console_gpu_snapshot_requested(QemuConsole *con) { return con->requested; }
static void qemu_console_set_gpu_snapshot(QemuConsole *con, pixman_image_t *image) {
    if (con->image) { free(con->image->data); free(con->image); }
    con->image = image; con->requested = false;
}
static int mesa_gui_fullscreen(int *v) { v[0]=2; v[1]=2; v[2]=valid_size ? 2 : 1; v[3]=2; return 0; }
static pixman_image_t *pixman_image_create_bits(int fmt,int w,int h,void *p,int stride) {
    pixman_image_t *image = calloc(1, sizeof(*image));
    image->width=w; image->height=h; image->data=calloc(w*h,4); return image;
}
static uint32_t *pixman_image_get_data(pixman_image_t *image) { return image->data; }
static int pixman_image_get_stride(pixman_image_t *image) { return image->width*4; }
static void glGetIntegerv(int key,int *value) {
    switch (key) {
    case GL_READ_FRAMEBUFFER_BINDING: *value=framebuffer; break;
    case GL_READ_BUFFER: *value=buffers[framebuffer]; break;
    case GL_PIXEL_PACK_BUFFER_BINDING: *value=pbo; break;
    default: assert(key>=GL_PACK_ALIGNMENT && key<=GL_PACK_SKIP_PIXELS); *value=pack[key-GL_PACK_ALIGNMENT];
    }
}
static void glPixelStorei(int key,int value) { pack[key-GL_PACK_ALIGNMENT]=value; }
static void glBindBuffer(int target,int value) { assert(target==GL_PIXEL_PACK_BUFFER); pbo=value; }
static void glBindFramebuffer(int target,int value) { assert(target==GL_READ_FRAMEBUFFER); framebuffer=value; }
static void glReadBuffer(int value) { buffers[framebuffer]=value; }
static void glReadPixels(int x,int y,int w,int h,int fmt,int type,void *data) {
    assert(framebuffer==0 && pbo==0 && buffers[0]==GL_BACK);
    assert(pack[0]==4 && !pack[1] && !pack[2] && !pack[3]);
    assert(x==0 && y==0 && w==2 && h==2 && fmt==GL_BGRA && type==GL_UNSIGNED_BYTE);
    uint32_t *pixels=data; pixels[0]=0x00112233; pixels[1]=0x00445566;
    pixels[2]=0x00778899; pixels[3]=0x00AABBCC; reads++;
}
/* Generated readback function is inserted here by the Python harness. */
SNAPSHOT_FUNCTION
int main(void) {
    m90_gpu_snapshot(); assert(reads==0);
    console.requested=true; valid_size=0;
    m90_gpu_snapshot(); assert(reads==0 && console.requested);
    valid_size=1; m90_gpu_snapshot(); assert(reads==1 && !console.requested);
    assert(framebuffer==9 && pbo==17 && buffers[0]==55 && buffers[9]==77);
    assert(pack[0]==8 && pack[1]==13 && pack[2]==3 && pack[3]==5);
    assert(console.image->data[0]==0x00778899 && console.image->data[1]==0x00AABBCC);
    assert(console.image->data[2]==0x00112233 && console.image->data[3]==0x00445566);
    m90_gpu_snapshot(); assert(reads==1);
    console.requested=true; m90_gpu_snapshot(); assert(reads==2);
    assert(framebuffer==9 && pbo==17 && buffers[0]==55 && buffers[9]==77);
    free(console.image->data); free(console.image);
    puts("GPU_SNAPSHOT_STATE_AND_PIXELS_OK");
    return 0;
}
