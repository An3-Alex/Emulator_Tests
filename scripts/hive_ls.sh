#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 HIVE KEY_PATH" >&2
  exit 2
fi

printf 'load %s\ncd %s\nls\n' "$1" "$2" | hivexsh
