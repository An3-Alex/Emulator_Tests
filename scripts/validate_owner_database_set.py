#!/usr/bin/env python3
"""Read-only validator for an owner database, loader and auxiliary modules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from inspect_owner_database import DATABASE_MODULE_FAMILY, inspect_dump


def pair_validations(database: dict, loader: dict) -> dict[str, bool]:
    try:
        module_id = int(database["header"]["module_id"], 16)
    except (KeyError, TypeError, ValueError):
        module_id = -1
    return {
        "database_role": database.get("role") == "database",
        "loader_role": loader.get("role") == "loader",
        "database_recognized": database.get("recognized") is True,
        "loader_recognized": loader.get("recognized") is True,
        "module_family_matches_loader":
            (module_id & 0xFFFFFF00) == DATABASE_MODULE_FAMILY,
        "loader_entry_resolves_inside_file":
            loader.get("header", {}).get("entry_target_file_offset", -1)
            < loader.get("size", 0),
    }


def module_validations(database: dict, module: dict) -> dict[str, bool]:
    return {
        "module_role": module.get("role") == "database_module",
        "module_recognized": module.get("recognized") is True,
        "module_id_matches_database":
            module.get("header", {}).get("module_id")
            == database.get("header", {}).get("module_id"),
        "module_family_matches_database":
            module.get("header", {}).get("module_family")
            == database.get("header", {}).get("module_family"),
    }


def inspect_set(
    database_path: Path,
    loader_path: Path,
    expected_database_sha256: str | None = None,
    expected_loader_sha256: str | None = None,
    module_paths: list[Path] | None = None,
    expected_module_sha256s: list[str] | None = None,
) -> dict:
    database = inspect_dump(database_path)
    loader = inspect_dump(loader_path)
    pins = {}
    if expected_database_sha256:
        expected = expected_database_sha256.replace(" ", "").upper()
        pins["database_sha256_matches"] = database["sha256"] == expected
    if expected_loader_sha256:
        expected = expected_loader_sha256.replace(" ", "").upper()
        pins["loader_sha256_matches"] = loader["sha256"] == expected
    validations = pair_validations(database, loader) | pins
    module_paths = module_paths or []
    expected_module_sha256s = expected_module_sha256s or []
    if len(module_paths) != len(expected_module_sha256s):
        raise ValueError("each auxiliary module requires exactly one SHA-256 pin")
    modules = []
    module_checks = []
    for path, expected_hash in zip(module_paths, expected_module_sha256s):
        module = inspect_dump(path)
        checks = module_validations(database, module)
        expected = expected_hash.replace(" ", "").upper()
        checks["module_sha256_matches"] = module["sha256"] == expected
        modules.append(module)
        module_checks.append(checks)
    modules_recognized = all(all(check.values()) for check in module_checks)
    return {
        "schema": "m90-owner-database-set-validation-v2",
        "database": database,
        "loader": loader,
        "modules": modules,
        "pair_validations": validations,
        "module_validations": module_checks,
        "recognized_pair": all(validations.values()) and modules_recognized,
        "handling": "Inputs were opened read-only; no bytes were modified or copied.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("loader", type=Path)
    parser.add_argument("--expected-database-sha256")
    parser.add_argument("--expected-loader-sha256")
    parser.add_argument("--module", action="append", type=Path, default=[])
    parser.add_argument("--expected-module-sha256", action="append", default=[])
    args = parser.parse_args()
    report = inspect_set(
        args.database,
        args.loader,
        args.expected_database_sha256,
        args.expected_loader_sha256,
        args.module,
        args.expected_module_sha256,
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["recognized_pair"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
