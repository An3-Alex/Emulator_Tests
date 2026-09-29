import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_owner_database_set.py"
SPEC = importlib.util.spec_from_file_location("validate_owner_database_set", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class OwnerDatabaseSetTests(unittest.TestCase):
    def reports(self):
        database = {
            "role": "database",
            "recognized": True,
            "header": {"module_id": "61640403", "module_family": "61640400"},
            "size": 0x1C3138,
        }
        loader = {
            "role": "loader",
            "recognized": True,
            "header": {"entry_target_file_offset": 0x8C8},
            "size": 0xBC0,
        }
        return database, loader

    def test_accepts_matching_database_and_loader(self):
        database, loader = self.reports()
        validations = VALIDATOR.pair_validations(database, loader)
        self.assertTrue(all(validations.values()))

    def test_rejects_different_module_family(self):
        database, loader = self.reports()
        database["header"]["module_id"] = "12345678"
        validations = VALIDATOR.pair_validations(database, loader)
        self.assertFalse(validations["module_family_matches_loader"])

    def test_rejects_entry_outside_loader_file(self):
        database, loader = self.reports()
        loader["header"]["entry_target_file_offset"] = loader["size"]
        validations = VALIDATOR.pair_validations(database, loader)
        self.assertFalse(validations["loader_entry_resolves_inside_file"])

    def test_accepts_matching_auxiliary_module(self):
        database, _ = self.reports()
        module = {
            "role": "database_module",
            "recognized": True,
            "header": {"module_id": "61640403", "module_family": "61640400"},
        }
        self.assertTrue(all(VALIDATOR.module_validations(database, module).values()))

    def test_rejects_auxiliary_module_from_other_family(self):
        database, _ = self.reports()
        module = {
            "role": "database_module",
            "recognized": True,
            "header": {"module_id": "12345678", "module_family": "12345600"},
        }
        self.assertFalse(
            VALIDATOR.module_validations(database, module)["module_family_matches_database"]
        )


if __name__ == "__main__":
    unittest.main()
