import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "inspect_vidcom_header.py"
HEADER_PATH = ROOT / "extracted" / "HeaderFilesForDB" / "commandointerpreter.h"
SPEC = importlib.util.spec_from_file_location("inspect_vidcom_header", MODULE_PATH)
assert SPEC and SPEC.loader
INSPECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSPECTOR)


class VidComHeaderTests(unittest.TestCase):
    def test_owner_image_header_contract(self):
        report = INSPECTOR.inspect_header(HEADER_PATH)
        self.assertTrue(report["recognized"])
        self.assertEqual(
            report["sha256"],
            "DBBD9FC6F2ED00C6BBBDEB50111763999A6F466339322181023D2672E0202553",
        )
        self.assertEqual(report["command_ids"]["VID_COM_COMMAND_VARIPARA"], 64)
        self.assertEqual(report["command_ids"]["VID_COM_TOUCHCLICKDOWN"], 65)
        self.assertEqual(report["command_ids"]["VID_COM_REQUEST_KEY"], 69)
        self.assertEqual(report["line_contract"], 1581)


if __name__ == "__main__":
    unittest.main()

