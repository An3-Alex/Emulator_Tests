#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE OUTPUT_DIRECTORY" >&2
  exit 2
fi

image=$1
output_dir=$2
mount_dir=$(mktemp -d /tmp/m90-diag.XXXXXX)
loop_device=""

cleanup() {
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then
    losetup -d "$loop_device" 2>/dev/null || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mkdir -p "$output_dir/NVRAM" "$output_dir/LogFiles" "$output_dir/Crash"

copy_if_present() {
  local guest_path=$1
  local destination=$2
  if [[ -f "$mount_dir/$guest_path" ]]; then
    cp -- "$mount_dir/$guest_path" "$destination"
  fi
}

copy_if_present "WINDOWS/setupapi.log" "$output_dir/setupapi.log"
copy_if_present "WINDOWS/debug/NetSetup.LOG" "$output_dir/NetSetup.LOG"
copy_if_present "WINDOWS/system32/config/SYSTEM" "$output_dir/SYSTEM.hive"
copy_if_present "NVRAM/cgos_shim.log" "$output_dir/NVRAM/cgos_shim.log"
copy_if_present "NVRAM/display_config.log" "$output_dir/NVRAM/display_config.log"
copy_if_present "NVRAM/display_bootstrap.log" "$output_dir/NVRAM/display_bootstrap.log"
copy_if_present "NVRAM/sram_compat.log" "$output_dir/NVRAM/sram_compat.log"
copy_if_present "NVRAM/irrklang_proxy.log" "$output_dir/NVRAM/irrklang_proxy.log"
copy_if_present "NVRAM/d3d9_proxy.log" "$output_dir/NVRAM/d3d9_proxy.log"
copy_if_present "NVRAM/m90_sram.bin" "$output_dir/NVRAM/m90_sram.bin"

{
  for guest_path in \
    NVRAM/game.exe WorkDir/game.exe \
    NVRAM/d3d9.dll WorkDir/d3d9.dll \
    NVRAM/irrKlang.dll WorkDir/irrKlang.dll \
    NVRAM/FBWFLIB.dll WorkDir/FBWFLIB.dll \
    WINDOWS/system32/Cgos.dll WINDOWS/system32/Cgos_original.dll; do
    if [[ -f "$mount_dir/$guest_path" ]]; then
      sha256sum "$mount_dir/$guest_path" | sed "s#  $mount_dir/#  #"
    else
      printf 'MISSING  %s\n' "$guest_path"
    fi
  done
} > "$output_dir/binary-hashes.sha256"

find "$mount_dir/LogFiles" -maxdepth 1 -type f \
  -printf '%TY-%Tm-%TdT%TH:%TM:%TS\t%s\t%f\n' \
  | sort -r > "$output_dir/LogFiles/inventory.tsv"

find "$mount_dir/LogFiles" -maxdepth 1 -type f \
  \( -iname 'logDatei*.txt' -o -iname 'VidComLog*.txt' \) \
  -exec cp -- '{}' "$output_dir/LogFiles/" \;

find "$mount_dir" -type f \
  \( -iname 'drwtsn32.log' -o -iname 'user.dmp' -o -iname '*.dmp' \
     -o -iname 'faultlog.txt' -o -iname '*.wer' \) \
  -printf '%s\t%TY-%Tm-%TdT%TH:%TM:%TS\t%P\n' \
  | sort -k2,2r > "$output_dir/Crash/inventory.tsv"

find "$mount_dir" -type f -iname 'drwtsn32.log' \
  -exec cp -- '{}' "$output_dir/Crash/" \;

echo "output=$output_dir"
sha256sum "$output_dir/SYSTEM.hive"
