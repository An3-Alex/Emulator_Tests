#!/usr/bin/env bash
# Read only; reports an image's preparation marker without changing its contents.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"
[[ $# -eq 1 ]] || { echo 'usage: check_image_stage.sh IMAGE' >&2; exit 2; }
image=$1
[[ -f "$image" ]] || exit 3
mount_dir=$(mktemp -d /tmp/m90-stage-check.XXXXXX)
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
marker="$mount_dir/NVRAM/m90_setup_stage.txt"
if [[ -f "$marker" ]]; then
  value=$(cat "$marker")
  case "$value" in
    stage=qxl-pnp) echo qxl-pnp ;;
    stage=qxl-verify) echo qxl-verify ;;
    stage=ready)
      verify_log="$mount_dir/NVRAM/display_verify.log"
      if [[ -f "$verify_log" ]] &&
         grep -Fq 'Recognized QXL displays: 0x00000002' "$verify_log" &&
         grep -Fq 'Active QXL primary: 0x00000001' "$verify_log" &&
         grep -Fq 'Attached secondary displays: 0x00000001' "$verify_log"; then
        echo ready
      else
        echo ready-unverified
      fi ;;
    *) echo unknown ;;
  esac
else
  cgos="$mount_dir/WINDOWS/system32/Cgos.dll"
  shell="$mount_dir/WINDOWS/explorer.exe"
  if [[ -f "$mount_dir/NVRAM/m90_audio_stage.json" ]]; then
    # Legacy working copies have no display preparation marker. During the
    # reversible audio installation their shell is temporarily our helper.
    audio_stage=$(python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from audio_image_stage import status; print(status(Path(sys.argv[2])))' "$(dirname "$0")" "$mount_dir")
    case "$audio_stage" in
      staging|software|install|verify|ready|legacy-install|legacy-verify|legacy-ready)
        echo legacy-ready; exit 0;;
      *) exit 3;;
    esac
  fi
  if [[ -f "$cgos" && -f "$shell" ]]; then
    cgos_hash=$(sha256sum "$cgos" | cut -d' ' -f1)
    shell_hash=$(sha256sum "$shell" | cut -d' ' -f1)
    if [[ "$cgos_hash" == 16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51 && \
          ( "$shell_hash" == ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6 ||
            "$shell_hash" == fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea ) ]]; then
      echo legacy-ready
    else
      echo unprepared
    fi
  else
    echo unknown
  fi
fi
