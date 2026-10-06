"""Reproducibly port the pinned GPU transport to QEMU 11.1.0 (Windows)."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
from qemu3dfx_dual_output import patch_sdl, patch_transport, patch_wgl, patch_blit

HOST_REVISION = "84f07211cc5b4fc6a371559bf8a5de4fb068e648"
GPU_REVISION = "920661f3b48bd278b93acd9cf9ff8c968afb02c9"

PASSTHROUGH_STATE = '''void graphic_hw_passthrough(QemuConsole *con, bool passthrough)
{
    con->passthrough = passthrough;
    if (!passthrough) {
        con->gpu_snapshot_requested = false;
        if (con->gpu_snapshot) {
            pixman_image_unref(con->gpu_snapshot);
            con->gpu_snapshot = NULL;
        }
    }
}

bool qemu_console_is_passthrough(const QemuConsole *con)
{
    return con && con->passthrough;
}

pixman_image_t *qemu_console_get_gpu_snapshot(QemuConsole *con)
{
    con->gpu_snapshot_requested = true;
    return con->gpu_snapshot ? pixman_image_ref(con->gpu_snapshot) : NULL;
}

bool qemu_console_gpu_snapshot_requested(QemuConsole *con)
{
    return con && con->passthrough && con->gpu_snapshot_requested;
}

void qemu_console_set_gpu_snapshot(QemuConsole *con, pixman_image_t *image)
{
    if (con->gpu_snapshot) {
        pixman_image_unref(con->gpu_snapshot);
    }
    con->gpu_snapshot = image; /* Transfer ownership; QMP refs stay immutable. */
    con->gpu_snapshot_requested = false;
}

'''

GPU_SNAPSHOT = '''/* Snapshot only on QMP demand, before the window scaler. BQL is held. */
static void m90_gpu_snapshot(void)
{
    QemuConsole *con = qemu_console_lookup_by_index(mesa_current_output());
    MESA_PFN(PFNGLBINDFRAMEBUFFERPROC, glBindFramebuffer);
    MESA_PFN(PFNGLBINDBUFFERPROC, glBindBuffer);
    /* The WGL host links no GL import library: resolve GL 1.x entry points
     * through the transport table like the rest of hw/mesa does. */
    MESA_PFN(PFNGLGETINTEGERVPROC, glGetIntegerv);
    MESA_PFN(PFNGLPIXELSTOREIPROC, glPixelStorei);
    MESA_PFN(PFNGLREADBUFFERPROC, glReadBuffer);
    MESA_PFN(PFNGLREADPIXELSPROC, glReadPixels);
    GLint framebuffer, read_buffer, default_buffer, pbo, pack[4];
    const GLenum pack_names[] = { GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH,
                                 GL_PACK_SKIP_ROWS, GL_PACK_SKIP_PIXELS };
    int v[4], width, height, stride;
    uint8_t *data, *row;
    pixman_image_t *image;

    if (!qemu_console_gpu_snapshot_requested(con) ||
        !p_glBindFramebuffer || !p_glBindBuffer || !p_glGetIntegerv ||
        !p_glPixelStorei || !p_glReadBuffer || !p_glReadPixels) {
        return;
    }
    mesa_gui_fullscreen(v);
    width = v[0];
    height = v[1] & 0x7FFFU;
    if (width <= 0 || height <= 0 || width > 4096 || height > 4096 ||
        v[2] < width || v[3] < height) {
        return;
    }
    image = pixman_image_create_bits(PIXMAN_x8r8g8b8, width, height, NULL, 0);
    if (!image) {
        return;
    }
    data = (uint8_t *)pixman_image_get_data(image);
    stride = pixman_image_get_stride(image);
    PFN_CALL(glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &framebuffer));
    PFN_CALL(glGetIntegerv(GL_READ_BUFFER, &read_buffer));
    PFN_CALL(glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pbo));
    for (int i = 0; i < 4; i++) {
        PFN_CALL(glGetIntegerv(pack_names[i], &pack[i]));
        PFN_CALL(glPixelStorei(pack_names[i], i == 0 ? 4 : 0));
    }
    PFN_CALL(glBindBuffer(GL_PIXEL_PACK_BUFFER, 0));
    PFN_CALL(glBindFramebuffer(GL_READ_FRAMEBUFFER, 0));
    PFN_CALL(glGetIntegerv(GL_READ_BUFFER, &default_buffer));
    PFN_CALL(glReadBuffer(GL_BACK));
    PFN_CALL(glReadPixels(0, 0, width, height, GL_BGRA, GL_UNSIGNED_BYTE, data));
    PFN_CALL(glReadBuffer(default_buffer));
    PFN_CALL(glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer));
    PFN_CALL(glReadBuffer(read_buffer));
    PFN_CALL(glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo));
    for (int i = 0; i < 4; i++) {
        PFN_CALL(glPixelStorei(pack_names[i], pack[i]));
    }
    /* OpenGL rows run bottom-up; QMP/Tk images run top-down. */
    row = g_malloc(stride);
    for (int y = 0; y < height / 2; y++) {
        uint8_t *top = data + y * stride;
        uint8_t *bottom = data + (height - 1 - y) * stride;
        memcpy(row, top, stride);
        memcpy(top, bottom, stride);
        memcpy(bottom, row, stride);
    }
    g_free(row);
    qemu_console_set_gpu_snapshot(con, image);
}

'''


def capture_gpu_present(source: str) -> str:
    source = replace_once(source, 'int MGLSwapBuffers(void)\n',
                          GPU_SNAPSHOT + 'int MGLSwapBuffers(void)\n')
    source = replace_once(source, '    MesaBlitScale();\n',
                        '    m90_gpu_snapshot();\n    MesaBlitScale();\n')
    statistics = (
        '    /* Low-rate performance data, not per-call tracing. */\n'
        '    {\n'
        '        static int64_t last_by_output[2];\n'
        '        static unsigned frames_by_output[2];\n'
        '        unsigned output = mesa_current_output();\n'
        '        int64_t *last = &last_by_output[output];\n'
        '        unsigned *frames = &frames_by_output[output];\n'
        '        int64_t now = qemu_clock_get_ns(QEMU_CLOCK_REALTIME);\n'
        '        if (!*last) { *last = now; }\n'
        '        (*frames)++;\n'
        '        if (now - *last >= 10000000000LL) {\n'
        '            fprintf(stderr, "M90_GPU_PRESENT output=%u fps=%.2f frames=%u seconds=%.2f\\n",\n'
        '                    output, *frames * 1e9 / (now - *last), *frames, (now - *last) / 1e9);\n'
        '            *frames = 0;\n'
        '            *last = now;\n'
        '        }\n'
        '    }\n')
    if '    return SwapBuffers(hDC);\n' in source:
        return replace_once(source, '    return SwapBuffers(hDC);\n',
                            statistics + '    return SwapBuffers(hDC);\n')
    return replace_once(source, '    SDL_GL_SwapWindow(window);\n',
                        '    SDL_GL_SwapWindow(window);\n' + statistics)


def capture_qmp_gpu(source: str) -> str:
    start = source.index('    surface = qemu_console_surface(con);',
                         source.index('qmp_screendump('))
    end = source.index('    object_unref(con);',
                       source.index('    image = pixman_image_ref(surface->image);', start))
    return source[:start] + '''    if (qemu_console_is_passthrough(con)) {
        image = qemu_console_get_gpu_snapshot(con);
        if (!image) {
            error_setg(errp, "GPU frame pending; retry screendump");
            object_unref(con);
            return;
        }
    } else {
        surface = qemu_console_surface(con);
        if (!surface) {
            error_setg(errp, "no surface");
            object_unref(con);
            return;
        }
        image = pixman_image_ref(surface->image);
    }
''' + source[end:]


def protect_sdl_2d(source: str) -> str:
    """Keep metadata current without letting QXL replace the GPU drawable."""
    source = replace_once(source, '    if (!scon->texture) {',
        '    if (qemu_console_is_passthrough(dcl->con) || !scon->texture) {')
    return replace_once(source, '    scon->surface = new_surface;\n',
        '    scon->surface = new_surface;\n\n'
        '    if (qemu_console_is_passthrough(dcl->con)) {\n'
        '        if (!surface_is_placeholder(new_surface)) {\n'
        '            SDL_SetWindowMinimumSize(scon->real_window,\n'
        '                                     surface_width(new_surface),\n'
        '                                     surface_height(new_surface));\n'
        '        }\n'
        '        return; /* GPU owns window, context and presentation. */\n'
        '    }\n')

DRAWABLE_BOUNDS = '''static int m90_drawable_fits(const int *v)
{
    int height = v[1] & 0x7FFFU;
    return v[0] > 0 && height > 0 && v[2] >= v[0] && v[3] >= height;
}

'''


def protect_blit_bounds(source: str) -> str:
    source = replace_once(source, 'void MesaBlitScale(void)\n',
                          DRAWABLE_BOUNDS + 'void MesaBlitScale(void)\n')
    source = replace_once(source, '    blit.has_swap = 1;',
        '    if (!m90_drawable_fits(v)) {\n'
        '        return; /* Resize/minimize can transiently invalidate the drawable. */\n'
        '    }\n    blit.has_swap = 1;')
    return replace_once(source, '    uint32_t *box;\n',
        '    uint32_t *box;\n\n'
        '    if (!m90_drawable_fits(v)) {\n'
        '        return;\n'
        '    }\n')


def protect_scaler_state(source: str) -> str:
    """The presentation quad must not mutate WineD3D's cached GL state."""
    source = replace_once(source,
        'vao_binding, vbo_binding, boolean_map;',
        'vao_binding, vbo_binding, boolean_map, texture0_binding;')
    source = replace_once(source,
        'static int blit_program_buffer(void *save_map, const int size, const void *data)\n{',
        'static int blit_program_buffer(void *save_map, const int size, const void *data)\n{\n'
        '    MESA_PFN(PFNGLACTIVETEXTUREPROC, glActiveTexture);')
    source = replace_once(source,
        '    struct save_states *last = (struct save_states *)save_map;\n\n'
        '    struct states_mapping mapping[] = {',
        '    struct save_states *last = (struct save_states *)save_map;\n\n'
        '    if (!p_glBindVertexArray || !p_glGenVertexArrays) {\n'
        '        return 1; /* Never modify the guest VAO as a scaler fallback. */\n'
        '    }\n\n    struct states_mapping mapping[] = {')
    source = replace_once(source,
        '    last->boolean_map &= GL_CONTEXT_CORE_PROFILE_BIT;',
        '    PFN_CALL(glActiveTexture(GL_TEXTURE0));\n'
        '    PFN_CALL(glGetIntegerv(GL_TEXTURE_BINDING_2D, &last->texture0_binding));\n'
        '    PFN_CALL(glActiveTexture(last->texture));\n'
        '    last->boolean_map &= GL_CONTEXT_CORE_PROFILE_BIT;')
    source = replace_once(source,
        '    if (last->boolean_map & GL_CONTEXT_CORE_PROFILE_BIT) {\n'
        '        if (!blit.vao)\n'
        '            PFN_CALL(glGenVertexArrays(1, &blit.vao));\n'
        '        PFN_CALL(glBindVertexArray(blit.vao));\n'
        '    }',
        '    /* Compatibility contexts also have guest attribute/pointer state. */\n'
        '    if (!blit.vao)\n'
        '        PFN_CALL(glGenVertexArrays(1, &blit.vao));\n'
        '    PFN_CALL(glBindVertexArray(blit.vao));')
    source = replace_once(source,
        'static void blit_restore_savemap(const void *save_map)\n{',
        'static void blit_restore_savemap(const void *save_map)\n{\n'
        '    MESA_PFN(PFNGLACTIVETEXTUREPROC, glActiveTexture);\n'
        '    MESA_PFN(PFNGLBINDTEXTUREPROC, glBindTexture);')
    source = replace_once(source,
        '    if (last->boolean_map & GL_CONTEXT_CORE_PROFILE_BIT)\n'
        '        PFN_CALL(glBindVertexArray(last->vao_binding));',
        '    PFN_CALL(glBindVertexArray(last->vao_binding));\n'
        '    PFN_CALL(glActiveTexture(GL_TEXTURE0));\n'
        '    PFN_CALL(glBindTexture(GL_TEXTURE_2D, last->texture0_binding));\n'
        '    PFN_CALL(glActiveTexture(last->texture));')
    return source


def freeze_gpu_guest_resolution(source: str) -> str:
    return replace_once(source, '    case SDL_WINDOWEVENT_RESIZED:\n',
        '    case SDL_WINDOWEVENT_RESIZED:\n'
        '        if (qemu_console_is_passthrough(scon->dcl.con)) {\n'
        '            break; /* Host sizing must not reset the fixed XP game mode. */\n'
        '        }\n')


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"Unexpected source anchor: {old[:80]}")
    return source.replace(old, new, 1)


def revision(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def pinned_file(root: Path, commit: str, name: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), "show", f"{commit}:{name}"]).decode()


def additions(patch: str, name: str) -> str:
    section = next(s for s in patch.split("diff -Nru ")[1:]
                   if s.splitlines()[0].endswith(" ./" + name))
    return "\n".join(line[1:] for line in section.splitlines()
                     if line.startswith("+") and not line.startswith("+++")) + "\n"


def apply(host: Path, gpu: Path) -> None:
    if revision(host) != HOST_REVISION or revision(gpu) != GPU_REVISION:
        raise ValueError("Unexpected host/GPU source revision")
    marker = host / "m90-gpu-port.json"
    recipe = hashlib.sha256(Path(__file__).read_bytes() +
                            Path(__file__).with_name('qemu3dfx_dual_output.py').read_bytes()).hexdigest()
    previous_port = marker.exists()
    owned_files = set()
    if marker.exists():
        record = json.loads(marker.read_text())
        owned_files = set(record["files"])
        if record.get("host_revision") != HOST_REVISION or record.get("gpu_revision") != GPU_REVISION:
            raise ValueError("Unexpected GPU port marker")
        for name, digest in record["files"].items():
            if hashlib.sha256((host / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Ported source changed: {name}")
        if record.get("recipe_sha256") == recipe:
            return
    if not previous_port and subprocess.check_output(["git", "-c", "core.filemode=false", "-c", "core.autocrlf=true", "-C", str(host), "diff", "--name-only"], text=True).strip():
        raise ValueError("Refusing to port over existing host changes")
    patch = pinned_file(gpu, GPU_REVISION, "00-qemu92x-mesa-glide.patch")
    updates: dict[str, str] = {}

    def edit(name, transform):
        updates[name] = transform(pinned_file(host, HOST_REVISION, name))

    # Modern WHPX already traps all three CPU-brand leaves; retain that path.
    cpuid = pinned_file(host, HOST_REVISION, "target/i386/whpx/whpx-all.c")
    exit_list = re.search(r"UINT32 cpuidExitList\[\] = \{(.*?)\};", cpuid, re.S)
    if exit_list is None or not all(leaf in exit_list[1] for leaf in
                                   ("0x80000002", "0x80000003", "0x80000004")):
        raise ValueError("Configured guest CPU brand is not intercepted")

    pc_add = additions(patch, "hw/i386/pc.c")
    calls, functions = pc_add.split("void glidept_mm_init(void)", 1)
    edit("hw/i386/pc.c", lambda s: replace_once(replace_once(s,
         "    /* Super I/O */", calls + "    /* Super I/O */"),
         "void pc_i8259_create(", "void glidept_mm_init(void)" + functions + "\nvoid pc_i8259_create("))
    pc_defs = additions(patch, "include/hw/i386/pc.h")
    pc_defs = pc_defs[pc_defs.index("/* GLIDE pass-through */"):].replace("0xefffe000", "0x9fffe000")
    edit("include/hw/i386/pc.h", lambda s: replace_once(s, '#define TYPE_PORT92 "port92"',
         pc_defs + '\n#define TYPE_PORT92 "port92"'))
    hook = additions(patch, "include/sysemu/whpx.h")
    edit("include/system/whpx.h", lambda s: replace_once(s, "#ifdef CONFIG_WHPX_IS_POSSIBLE\n", "#ifdef CONFIG_WHPX_IS_POSSIBLE\n" + hook))
    # Use the modern common memory mapper. The transport's backing pages remain
    # owned by the GPU context, as in the original fork; no synthetic RAMBlock.
    mapper = '''
void whpx_update_guest_pa_range(uint64_t start_pa, uint64_t size,
                               void *host_va, int readonly, int add)
{
    struct whpx_state *whpx = &whpx_global;
    uint64_t page_size = qemu_real_host_page_size();
    HRESULT hr;
    size = QEMU_ALIGN_UP(size, page_size);
    if (!QEMU_IS_ALIGNED(start_pa, page_size) ||
        (add && !QEMU_IS_ALIGNED((uintptr_t)host_va, page_size))) {
        error_report("WHPX: unaligned GPU mapping");
        abort();
    }
    if (add) {
        WHV_MAP_GPA_RANGE_FLAGS flags = WHvMapGpaRangeFlagRead |
            WHvMapGpaRangeFlagExecute | (readonly ? 0 : WHvMapGpaRangeFlagWrite);
        hr = whp_dispatch.WHvMapGpaRange(whpx->partition, host_va,
                                       start_pa, size, flags);
    } else {
        hr = whp_dispatch.WHvUnmapGpaRange(whpx->partition, start_pa, size);
    }
    if (FAILED(hr)) {
        error_report("WHPX: GPU mapping failed, hr=%08lx", hr);
        abort();
    }
}

'''
    edit("accel/whpx/whpx-common.c", lambda s: replace_once(s, "static void whpx_region_add(", mapper + "static void whpx_region_add("))
    prototypes = additions(patch, "include/ui/console.h")
    prototypes = prototypes[prototypes.index("/* glidewnd.c */"):]
    edit("include/ui/console.h", lambda s: replace_once(s,
         "void qemu_graphic_console_close(QemuConsole *con);",
         "void qemu_graphic_console_close(QemuConsole *con);\n"
         "void graphic_hw_passthrough(QemuConsole *con, bool passthrough);\n"
         "bool qemu_console_is_passthrough(const QemuConsole *con);\n"
         "pixman_image_t *qemu_console_get_gpu_snapshot(QemuConsole *con);\n"
         "bool qemu_console_gpu_snapshot_requested(QemuConsole *con);\n"
         "void qemu_console_set_gpu_snapshot(QemuConsole *con, pixman_image_t *image);\n"
         "unsigned mesa_current_output(void);\nvoid mesa_select_output(unsigned output);\n" + prototypes))
    # Rendering ownership is internal state, not a replaceable UI-info field.
    # RESIZED supplies only width/height and must never re-enable QXL painting.
    edit("ui/console-priv.h", lambda s: replace_once(s, "    QemuUIInfo ui_info;",
         "    QemuUIInfo ui_info;\n    bool passthrough;\n"
         "    bool gpu_snapshot_requested;\n    pixman_image_t *gpu_snapshot;"))
    edit("ui/console.c", lambda s: replace_once(replace_once(s,
         "void qemu_console_hw_update_done(", PASSTHROUGH_STATE + "void qemu_console_hw_update_done("),
         "    if (!con->hw_ops->gfx_update || con->hw_ops->gfx_update(con->hw)) {",
         "    if (con->passthrough || !con->hw_ops->gfx_update ||\n"
         "        con->hw_ops->gfx_update(con->hw)) {"))
    edit("ui/ui-qmp-cmds.c", capture_qmp_gpu)
    edit("ui/sdl2-2d.c", protect_sdl_2d)
    sdl_add = additions(patch, "ui/sdl2.c")
    focus = sdl_add[sdl_add.index("static int fxui_grab_val"):sdl_add.index("        fxui_focus_gained(scon);")]
    windows = sdl_add[sdl_add.index("static void sdl_display_valid"):sdl_add.index("#ifdef __linux__")]
    windows = windows.replace("dpy_cursor_define(", "qemu_console_set_cursor(")
    windows = windows.replace("dpy_mouse_set(", "qemu_console_set_mouse(")
    # Switch back to the cached QXL surface only after GPU ownership ends.
    windows = replace_once(windows,
        '    else {\n        if (!s->scon->real_renderer)',
        '    else {\n        graphic_hw_passthrough(s->scon->dcl.con, false);\n'
        '        SDL_SetWindowMinimumSize(s->scon->real_window, 0, 0);\n'
        '        if (!s->scon->real_renderer)')
    windows = replace_once(windows,
        'static void wndproc_fxui_release(struct sdl_console_cb *s)\n{',
        'static void wndproc_fxui_release(struct sdl_console_cb *s)\n{\n'
        '    graphic_hw_passthrough(s->scon->dcl.con, false);\n'
        '    SDL_SetWindowMinimumSize(s->scon->real_window, 0, 0);')
    windows = replace_once(windows, '    s->render_pause = 1;\n',
        '    SDL_SetWindowMinimumSize(s->scon->real_window,\n'
        '                             surface_width(s->scon->surface),\n'
        '                             surface_height(s->scon->surface));\n'
        '    s->render_pause = 1;\n')
    def sdl(s):
        s = freeze_gpu_guest_resolution(s)
        s = replace_once(s, '#include "qemu/module.h"', '#include "qemu/module.h"\n#include "qemu/error-report.h"')
        s = replace_once(s, "static void handle_windowevent(", focus + "static void handle_windowevent(")
        s = replace_once(s, "    case SDL_WINDOWEVENT_FOCUS_GAINED:", "    case SDL_WINDOWEVENT_FOCUS_GAINED:\n        fxui_focus_gained(scon);")
        s = replace_once(s, "        if (gui_grab && !gui_fullscreen) {", "        if (!fxui_focus_lost() && gui_grab && !gui_fullscreen) {\n            fxui_grab_val(0x80 | gui_grab);")
        s = replace_once(s, "static const DisplayChangeListenerOps dcl_2d_ops", windows + "static const DisplayChangeListenerOps dcl_2d_ops")
        s = replace_once(s, "        SDL_SetWindowIcon(sdl2_console[0].real_window, icon);", "        SDL_SetWindowIcon(sdl2_console[0].real_window, icon);\n        scon_cbs[0].icon = scon_cbs[1].icon = icon;")
        return patch_sdl(s)
    edit("ui/sdl2.c", sdl)
    edit("meson.build", lambda s: replace_once(s, "  subdir('target')", "  subdir('target')\n  subdir('hw/3dfx')\n  subdir('hw/mesa')"))
    feature = additions(patch, "system/vl.c").replace("                feature();\n", "")
    feature = feature.replace("rev_[ALIGNED(1)]", 'rev_[] = "920661f-"')
    edit("system/vl.c", lambda s: replace_once(replace_once(s, "static void version(void)", feature + "static void version(void)"),
         "                version();", "                version();\n                feature();"))
    for prefix in ("qemu-0/hw/3dfx", "qemu-1/hw/mesa"):
        names = subprocess.check_output(["git", "-C", str(gpu), "ls-tree", "-r", "--name-only", GPU_REVISION, prefix], text=True).splitlines()
        for name in names:
            target = name.split("/", 1)[1]
            s = pinned_file(gpu, GPU_REVISION, name)
            s = re.sub(r"\bHASH_ALGO", "QCRYPTO_HASH_ALGO", s)
            s = s.replace("rev_[ALIGNED(1)]", 'rev_[ALIGNED(1)] = "920661f-"')
            for old, new in (("0xec000000", "0x9c000000"), ("0xea000000", "0x9a000000"),
                             ("0xefffe000", "0x9fffe000"), ("(0xE0U << 24)", "(0x90U << 24)")):
                s = s.replace(old, new)
            if target == 'hw/mesa/mesagl_blit.c':
                s = patch_blit(protect_scaler_state(protect_blit_bounds(s)))
            if target == 'hw/mesa/mesapt_mm.c':
                s = patch_transport(s)
            if target == 'hw/mesa/mglcntx_mingw.c':
                s = capture_gpu_present(patch_wgl(s))
            if target == 'hw/mesa/mglcntx_sdlgl.c':
                s = capture_gpu_present(s)
            updates[target] = s
    # MSYS2 native Python needs a canonical Windows file URI.
    edit("python/scripts/mkvenv.py", lambda s: s.replace('f"file://{str(wheels_dir)}"', 'Path(wheels_dir).absolute().as_uri()'))
    edit("scripts/symlink-install-tree.py", lambda s: replace_once(s, "import sys\n", "import sys\n\nif os.name == 'nt':\n    sys.exit(0)\n"))
    # Relocate package resources beside the host executable. Meson's native
    # Python environment must never become an embedded installation prefix.
    portable = "/* Windows standalone runtime resource layout. */\n"
    for key, value in {
        "CONFIG_PREFIX": "C:/qemu", "CONFIG_BINDIR": "C:/qemu",
        "CONFIG_QEMU_DATADIR": "C:/qemu/pc-bios", "CONFIG_QEMU_MODDIR": "C:/qemu/modules",
        "CONFIG_QEMU_CONFDIR": "C:/qemu/etc", "CONFIG_SYSCONFDIR": "C:/qemu/etc",
        "CONFIG_QEMU_ICONDIR": "C:/qemu/share/icons", "CONFIG_QEMU_HELPERDIR": "C:/qemu/libexec",
    }.items():
        portable += f'#undef {key}\n#define {key} "{value}"\n'
    portable += '#undef CONFIG_QEMU_FIRMWAREPATH\n#define CONFIG_QEMU_FIRMWAREPATH "C:/qemu/pc-bios",\n'
    updates["include/qemu/m90-portable-prefix.h"] = portable
    for name in ("system/vl.c", "ui/sdl2.c", "util/cutils.c", "util/datadir.c", "util/module.c"):
        value = updates.get(name) or pinned_file(host, HOST_REVISION, name)
        updates[name] = replace_once(value, '#include "qemu/osdep.h"',
            '#include "qemu/osdep.h"\n#include "qemu/m90-portable-prefix.h"')
    # A Windows Git checkout can have CRLF even when the MSYS2 build scripts
    # require LF. This is a mechanical normalization of pristine tracked text.
    tracked = subprocess.check_output(["git", "-C", str(host), "ls-files", "-z"]).decode().split("\0")
    for name in updates:
        path = host / name
        if path.exists() and name not in owned_files:
            if name not in tracked or path.is_symlink() or path.read_bytes().replace(b"\r\n", b"\n") != pinned_file(host, HOST_REVISION, name).encode():
                raise ValueError(f"Refusing to overwrite an unowned source change: {name}")
    # A verified previous port already normalized the pristine checkout.
    # Re-reading every upstream file for a small patch is unnecessary.
    for name in (() if previous_port else filter(None, tracked)):
        path = host / name
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            if b"\0" not in raw and b"\r\n" in raw:
                path.write_bytes(raw.replace(b"\r\n", b"\n"))
    for name, value in updates.items():
        path = host / name
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = value.encode()
        if not path.is_file() or path.read_bytes() != encoded:
            path.write_bytes(encoded)
    marker.write_text(json.dumps(dict(host_revision=HOST_REVISION, gpu_revision=GPU_REVISION, recipe_sha256=recipe,
         files={name: hashlib.sha256((host / name).read_bytes()).hexdigest() for name in updates}), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=Path)
    parser.add_argument("gpu", type=Path)
    args = parser.parse_args()
    apply(args.host, args.gpu)
