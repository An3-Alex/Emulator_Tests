from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qemu3dfx_modern_port import (additions, replace_once, protect_sdl_2d,
                                protect_blit_bounds, freeze_gpu_guest_resolution,
                                firmware_dma_capable,
                                DRAWABLE_BOUNDS, PASSTHROUGH_STATE,
                                HOST_REVISION, GPU_REVISION)


class PortTests(unittest.TestCase):
    def test_host_resize_cannot_change_fixed_guest_mode_during_gpu_output(self):
        fixture = '''    case SDL_WINDOWEVENT_RESIZED:
        qemu_console_set_ui_info(scon->dcl.con, &info, true);
        sdl2_redraw(scon);
        break;
'''
        fixed = freeze_gpu_guest_resolution(fixture)
        self.assertLess(fixed.index('if (qemu_console_is_passthrough'),
                        fixed.index('qemu_console_set_ui_info'))
        self.assertIn('break; /* Host sizing', fixed)
        self.assertEqual(fixed.count('qemu_console_set_ui_info'), 1)

    def test_small_or_minimized_drawable_is_guarded_before_copy_and_scale(self):
        fixture = '''void MesaBlitScale(void)
{
    blit.has_swap = 1;
    glCopyTexImage2D();
}
void MesaRenderScaler(void)
{
    uint32_t *box;
    glGetIntegerv();
}
'''
        fixed = protect_blit_bounds(fixture)
        self.assertEqual(fixed.count(DRAWABLE_BOUNDS), 1)
        self.assertEqual(fixed.count('if (!m90_drawable_fits(v))'), 2)
        self.assertLess(fixed.index('if (!m90_drawable_fits(v))'), fixed.index('glCopyTexImage2D'))
        self.assertIn('v[2] >= v[0] && v[3] >= height', DRAWABLE_BOUNDS)
        self.assertIn('v[1] & 0x7FFFU', DRAWABLE_BOUNDS)
        with self.assertRaises(ValueError):
            protect_blit_bounds('unexpected')

    def test_gpu_ownership_is_not_part_of_replaceable_resize_metadata(self):
        self.assertIn('con->passthrough = passthrough;', PASSTHROUGH_STATE)
        self.assertNotIn('ui_info', PASSTHROUGH_STATE)
        self.assertIn('return con && con->passthrough;', PASSTHROUGH_STATE)

    def test_qxl_updates_do_not_replace_gpu_context_but_cache_latest_surface(self):
        fixture = '''    if (!scon->texture) { return; }
    SDL_RenderClear(scon->real_renderer);
    scon->surface = new_surface;
    SDL_DestroyTexture(scon->texture);
    sdl2_window_destroy(scon);
'''
        fixed = protect_sdl_2d(fixture)
        self.assertIn('SDL_SetWindowMinimumSize', fixed)
        self.assertIn('qemu_console_is_passthrough(dcl->con) || !scon->texture', fixed)
        self.assertLess(fixed.index('scon->surface = new_surface'), fixed.index('return; /* GPU'))
        self.assertLess(fixed.index('return; /* GPU'), fixed.index('SDL_DestroyTexture'))
        self.assertLess(fixed.index('return; /* GPU'), fixed.index('sdl2_window_destroy'))
        with self.assertRaises(ValueError):
            protect_sdl_2d(fixed)

    def test_attached_ide_drives_are_dma_capable_after_reset(self):
        fixture = '''static void bmdma_reset(const IDEDMA *dma)
{
    bmdma_cancel(bm);
    bm->cmd = 0;
    bm->status = 0;
    bm->addr = 0;
}
'''
        fixed = firmware_dma_capable(fixture)
        self.assertNotIn('bm->status = 0;', fixed)
        self.assertIn('bm->bus->ifs[0].blk ? 0x20 : 0', fixed)
        self.assertIn('bm->bus->ifs[1].blk ? 0x40 : 0', fixed)
        self.assertLess(fixed.index('bmdma_cancel(bm);'), fixed.index('bm->status ='))
        with self.assertRaises(ValueError):
            firmware_dma_capable(fixed)

    def test_replacement_requires_an_unambiguous_anchor(self):
        self.assertEqual(replace_once("one two", "two", "three"), "one three")
        for source in ("missing", "two two"):
            with self.assertRaises(ValueError):
                replace_once(source, "two", "three")

    def test_pinned_patch_additions_exclude_headers_context_and_removals(self):
        patch = "diff -Nru old/a ./a\n--- old/a\n+++ ./a\n@@ -1 +1 @@\n context\n-removed\n+added\n+\n"
        self.assertEqual(additions(patch, "a"), "added\n\n")

    def test_build_revisions_are_fixed_commits_not_floating_refs(self):
        for value in (HOST_REVISION, GPU_REVISION):
            self.assertRegex(value, r"^[a-f0-9]{40}$")


if __name__ == "__main__":
    unittest.main()
