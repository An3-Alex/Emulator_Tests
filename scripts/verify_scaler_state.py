"""Execute the host's actual scaler save/restore C with a deterministic GL model.

No window, QEMU process, guest, image or graphics driver is started.
"""
import argparse
from pathlib import Path
import subprocess


PREFIX = r'''
#include <stdio.h>
#include <string.h>
#define MESA_PFN(type, name)
#define PFN_CALL(call) call
#define GL_CONTEXT_CORE_PROFILE_BIT 1
enum {
    GL_VIEWPORT=1, GL_FRAMEBUFFER_BINDING, GL_READ_FRAMEBUFFER_BINDING,
    GL_ACTIVE_TEXTURE, GL_TEXTURE_BINDING_2D, GL_VERTEX_ARRAY_BINDING,
    GL_ARRAY_BUFFER_BINDING, GL_CONTEXT_PROFILE_MASK, GL_FRAMEBUFFER_SRGB,
    GL_BLEND, GL_CULL_FACE, GL_DEPTH_TEST, GL_SCISSOR_TEST, GL_STENCIL_TEST,
    GL_ARRAY_BUFFER, GL_STATIC_DRAW, GL_TEXTURE_2D, GL_TEXTURE0=100
};
static struct { unsigned vao, vbo; } blit;
static int profile, active, vao, buffer, textures[8], enabled[32];
static struct { int enabled, pointer; } attributes[256];
static void glActiveTexture(int unit) { active=unit; }
static void glBindTexture(int target, unsigned name) { textures[active-GL_TEXTURE0]=name; }
static void glBindVertexArray(unsigned name) { vao=name; }
static void glGenVertexArrays(int n, unsigned *name) { *name=77; }
static void glGenBuffers(int n, unsigned *name) { *name=88; }
static void glBindBuffer(int target, unsigned name) { buffer=name; }
static void glBufferData(int target, int size, const void *data, int usage) {}
static int glIsEnabled(int cap) { return enabled[cap]; }
static void glDisable(int cap) { enabled[cap]=0; }
static void glEnable(int cap) { enabled[cap]=1; }
static void glGetIntegerv(int cap, int *out) {
    switch(cap) {
    case GL_VIEWPORT: out[0]=0; out[1]=0; out[2]=800; out[3]=600; break;
    case GL_FRAMEBUFFER_BINDING: case GL_READ_FRAMEBUFFER_BINDING: *out=0; break;
    case GL_ACTIVE_TEXTURE: *out=active; break;
    case GL_TEXTURE_BINDING_2D: *out=textures[active-GL_TEXTURE0]; break;
    case GL_VERTEX_ARRAY_BINDING: *out=vao; break;
    case GL_ARRAY_BUFFER_BINDING: *out=buffer; break;
    case GL_CONTEXT_PROFILE_MASK: *out=profile; break;
    default: *out=0; break;
    }
}
#define p_glBindVertexArray glBindVertexArray
#define p_glGenVertexArrays glGenVertexArrays
'''

SUFFIX = r'''
int main(void) {
    for (int pass=0; pass<2; pass++) {
        profile=pass ? GL_CONTEXT_CORE_PROFILE_BIT : 2;
        for (int frame=0; frame<10; frame++) {
            struct save_states saved;
            memset(&blit,0,sizeof(blit));
            memset(attributes,0,sizeof(attributes));
            active=GL_TEXTURE0+3; textures[0]=31; textures[3]=93;
            vao=7; buffer=9; enabled[GL_BLEND]=1;
            attributes[7].enabled=1; attributes[7].pointer=1234;
            if (blit_program_buffer(&saved,0,NULL)) return 2;
            /* Actual presentation changes attribute zero on its current VAO. */
            attributes[vao].enabled=0; attributes[vao].pointer=5678;
            glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D,99);
            blit_restore_savemap(&saved);
            if (vao!=7 || buffer!=9 || attributes[7].enabled!=1 ||
                attributes[7].pointer!=1234 || textures[0]!=31 ||
                textures[3]!=93 || active!=GL_TEXTURE0+3 || !enabled[GL_BLEND]) {
                fprintf(stderr,"STATE_CORRUPTED profile=%d frame=%d vao=%d attr=%d ptr=%d tex0=%d active=%d\n",
                    profile,frame,vao,attributes[7].enabled,attributes[7].pointer,textures[0],active);
                return 1;
            }
        }
    }
    puts("SCALER_STATE_VERIFIED compatibility/core profiles, 20 frames");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_text()
    helpers = source[source.index("struct save_states {"):source.index("void MesaBlitScale(void)")]
    args.workdir.mkdir(parents=True, exist_ok=True)
    generated = args.workdir / "scaler-state.c"
    executable = args.workdir / "scaler-state.exe"
    generated.write_text(PREFIX + helpers + SUFFIX)
    subprocess.run([str(args.compiler.resolve()), "-O0", str(generated.resolve()),
                    "-o", str(executable.resolve())], check=True)
    subprocess.run([str(executable.resolve())], check=True)


if __name__ == "__main__":
    main()
