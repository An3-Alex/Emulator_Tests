#!/usr/bin/env bash
# Inspect diagnostics in a stopped working image. The image is never mounted RW.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

[[ $# -eq 1 && -f "$1" ]] || { echo 'usage: inspect_guest_logs_ro.sh IMAGE' >&2; exit 2; }
image=$1
mount_dir=$(mktemp -d /tmp/m90-crash-inspect.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT

loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1

printf 'Log directories:\n'
find "$mount_dir" -maxdepth 4 -type d -iname '*log*' -print | sed -n '1,60p'
printf '\nGuest crash dumps:\n'
find "$mount_dir" -type f \
  \( -iname '*.dmp' -o -iname 'drwtsn32.log' -o -iname '*.mdmp' \) \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort -r | sed -n '1,30p'

for name in WINDOWS/system32/config/AppEvent.Evt \
            WINDOWS/system32/config/SysEvent.Evt \
            WINDOWS/MEMORY.DMP \
            WorkDir/sound/A/TurbobuchenDry.ogg; do
  if [[ -f "$mount_dir/$name" ]]; then
    stat -c '%y %s %n' "$mount_dir/$name"
  fi
done

printf '\nRecent NVRAM files:\n'
find "$mount_dir/NVRAM" -maxdepth 3 -type f \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort -r | sed -n '1,60p'
if [[ -d "$mount_dir/LogFiles" ]]; then
  printf '\nRecent guest log files:\n'
  find "$mount_dir/LogFiles" -maxdepth 2 -type f \
    -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort -r | sed -n '1,60p'
fi

if [[ -f "$mount_dir/LogFiles/logDatei.txt" ]]; then
  printf '\nLogFiles/logDatei.txt (first 100 lines, newest first):\n'
  sed -n '1,100p' "$mount_dir/LogFiles/logDatei.txt"
fi
if [[ -f "$mount_dir/LogFiles/logDatei.txt13.txt" ]]; then
  printf '\nLogFiles/logDatei.txt13.txt (first 60 lines, newest first):\n'
  sed -n '1,60p' "$mount_dir/LogFiles/logDatei.txt13.txt"
fi
if [[ -d "$mount_dir/LogFiles" ]]; then
  printf '\nSound-buffer errors in guest logs:\n'
  grep -H -F 'SoundBuffer konnte nicht erstellt werden' \
    "$mount_dir"/LogFiles/logDatei.txt* 2>/dev/null | sed -n '1,30p' || true
fi
for break_log in "$mount_dir"/LogFiles/logComAtBreak20120201_*.bin; do
  if [[ -f "$break_log" ]]; then
    printf '\n%s (first 96 bytes):\n' "${break_log#"$mount_dir"/}"
    od -An -tx1 -N96 "$break_log"
  fi
done

for name in NVRAM/qxl_install.log NVRAM/display_verify.log \
            NVRAM/display_bootstrap.log NVRAM/d3d9_proxy.log \
            NVRAM/irrklang_proxy.log NVRAM/cgos_shim.log \
            NVRAM/sram_compat.log NVRAM/CheckTemp.txt; do
  if [[ -f "$mount_dir/$name" ]]; then
    printf '\n%s (last 60 lines):\n' "$name"
    tail -n 60 "$mount_dir/$name"
  fi
done

system_hive="$mount_dir/WINDOWS/system32/config/SYSTEM"
if [[ -f "$system_hive" ]]; then
  printf '\nSYSTEM Select:\n'
  python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" Select --depth 1
  for control_set in ControlSet001 ControlSet002 ControlSet003; do
    printf '\n%s QXL service:\n' "$control_set"
    python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" \
      "$control_set/Services/qxl" --depth 1 || true
  done
  printf '\nDisplay class configuration:\n'
  python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" \
    'ControlSet001/Control/Class/{4d36e968-e325-11ce-bfc1-08002be10318}' \
    --depth 2 || true
  printf '\nQXL PCI enumeration keys:\n'
  python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" \
    ControlSet001/Enum/PCI --depth 4 | grep -ai -A 28 'VEN_1B36' || true
  printf '\nQXL Control/Video settings:\n'
  while IFS= read -r video_id; do
    python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" \
      "ControlSet001/Control/Video/$video_id" --depth 3 || true
  done < <(python3 "$(dirname -- "$0")/hive_query.py" "$system_hive" \
    ControlSet001/Enum/PCI --depth 4 | sed -n 's/^value VideoID type=1 value=//p')
fi
if [[ -f "$mount_dir/WINDOWS/setupapi.log" ]]; then
  printf '\nRecent QXL SetupAPI entries:\n'
  grep -ai -B 2 -A 8 -E 'qxl|ven_1b36|Red Hat' \
    "$mount_dir/WINDOWS/setupapi.log" | tail -n 160 || true
fi
printf '\nWindows driver-start diagnostics:\n'
for diagnostic in "$mount_dir/WINDOWS/ntbtlog.txt" \
                  "$mount_dir/WINDOWS/system32/config/SysEvent.Evt"; do
  if [[ -f "$diagnostic" ]]; then
    echo "${diagnostic#"$mount_dir"/}"
    strings -el "$diagnostic" | grep -Ei -A 3 -B 2 'qxl|qxldd|video|display|failed|Fehler' | tail -n 90 || true
  fi
done
