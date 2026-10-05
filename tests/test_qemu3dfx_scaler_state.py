from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qemu3dfx_modern_port import protect_scaler_state, GPU_REVISION


class ScalerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = Path(__file__).resolve().parents[2] / ".tools/qemu-3dfx"
        if not cls.checkout.is_dir():
            raise unittest.SkipTest("Pinned upstream checkout is not present")
        cls.source = subprocess.check_output([
            "git", "-C", str(cls.checkout), "show",
            GPU_REVISION + ":qemu-1/hw/mesa/mesagl_blit.c",
        ]).decode()

    def test_private_vao_is_used_and_restored_in_both_gl_profiles(self):
        fixed = protect_scaler_state(self.source)
        prepare = fixed.split("static int blit_program_buffer", 1)[1].split(
            "static void blit_restore_savemap", 1)[0]
        restore = fixed.split("static void blit_restore_savemap", 1)[1].split(
            "void MesaBlitScale", 1)[0]
        self.assertIn("glBindVertexArray(blit.vao)", prepare)
        self.assertNotIn("if (last->boolean_map & GL_CONTEXT_CORE_PROFILE_BIT)", prepare)
        self.assertNotIn("if (last->boolean_map & GL_CONTEXT_CORE_PROFILE_BIT)", restore)
        self.assertIn("glBindVertexArray(last->vao_binding)", restore)
        self.assertLess(prepare.index("if (!p_glBindVertexArray"),
                        prepare.index("glDisable(boolean_states[i])"))

    def test_unit_zero_binding_is_saved_separately_from_guest_active_unit(self):
        fixed = protect_scaler_state(self.source)
        self.assertIn("glGetIntegerv(GL_TEXTURE_BINDING_2D, &last->texture0_binding)", fixed)
        restore = fixed.split("static void blit_restore_savemap", 1)[1]
        self.assertLess(restore.index("glActiveTexture(GL_TEXTURE0)"),
                        restore.index("glBindTexture(GL_TEXTURE_2D, last->texture0_binding)"))
        self.assertLess(restore.index("glBindTexture(GL_TEXTURE_2D, last->texture0_binding)"),
                        restore.index("glActiveTexture(last->texture)"))

    def test_partial_or_repeated_patch_is_refused(self):
        with self.assertRaises(ValueError):
            protect_scaler_state("unexpected revision")
        with self.assertRaises(ValueError):
            protect_scaler_state(protect_scaler_state(self.source))


if __name__ == "__main__":
    unittest.main()
