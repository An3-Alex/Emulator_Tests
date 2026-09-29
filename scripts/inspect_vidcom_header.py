#!/usr/bin/env python3
"""Read-only extractor for the COMMANDID contract in commandointerpreter.h."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MAX_HEADER_SIZE = 2 * 1024 * 1024
EXPECTED_COMMANDS = {
    "VID_COM_INITVIDEO": 34,
    "VID_COM_COMMAND_VARIPARA": 64,
    "VID_COM_TOUCHCLICKDOWN": 65,
    "VID_COM_REQUEST_KEY": 69,
    "VID_COM_STARTUPTXT": 70,
    "VID_COM_GRAPHICCHECKSUM": 73,
    "VID_COM_SYSTEMINFO": 79,
}


def parse_command_ids(text: str) -> dict[str, int]:
    match = re.search(r"enum\s+COMMANDID\s*\{(.*?)\};", text, re.DOTALL)
    if not match:
        raise ValueError("enum COMMANDID not found")
    body = re.sub(r"//[^\r\n]*", "", match.group(1))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    result: dict[str, int] = {}
    value = -1
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        assignment = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:=\s*(\d+))?", item)
        if not assignment:
            raise ValueError(f"unsupported COMMANDID item: {item!r}")
        name, explicit = assignment.groups()
        value = int(explicit) if explicit is not None else value + 1
        result[name] = value
    return result


def inspect_header(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_HEADER_SIZE:
        raise ValueError("header exceeds bounded input size")
    data = path.read_bytes()
    text = data.decode("cp1252")
    commands = parse_command_ids(text)
    marker_line = next(
        (index for index, line in enumerate(text.splitlines(), 1)
         if "LINESCOMMANDOINTERPRETER" in line),
        None,
    )
    expected_results = {
        name: commands.get(name) == value for name, value in EXPECTED_COMMANDS.items()
    }
    validations = {
        "expected_command_ids": all(expected_results.values()),
        "max_command_size_1100": bool(
            re.search(r"#define\s+MAX_BYTESIZE_COMMAND\s+1100\b", text)
        ),
        "packed_protocol_structures": "#pragma pack(1)" in text,
        "varipara_layout": bool(
            re.search(
                r"WORD\s+numWerte\s*;.*?WORD\s+objId\s*;.*?"
                r"BYTE\s+secComId\s*;.*?BYTE\s+werte\s*;.*?"
                r"PARA_COMMAND_VARIPARA",
                text,
                re.DOTALL,
            )
        ),
        "line_contract_1581": marker_line == 1581,
    }
    return {
        "schema": "m90-vidcom-header-inspection-v1",
        "source": str(path.resolve()),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest().upper(),
        "encoding": "Windows-1252",
        "command_ids": commands,
        "expected_command_results": expected_results,
        "line_contract": marker_line,
        "validations": validations,
        "recognized": all(validations.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("header", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    report = inspect_header(args.header)
    if args.expected_sha256:
        expected = args.expected_sha256.replace(" ", "").upper()
        report["expected_sha256"] = expected
        report["hash_matches"] = report["sha256"] == expected
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if args.expected_sha256 and not report["hash_matches"]:
        return 2
    return 0 if report["recognized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

