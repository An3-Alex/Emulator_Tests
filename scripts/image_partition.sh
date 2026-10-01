#!/usr/bin/env bash
# Derive the volume boundaries from the image, not from one particular CF card.
image_loop_device() {
  local image=$1 mode=$2 bounds offset length
  local -a options=()
  [[ -f "$image" ]] || { echo 'CF image missing' >&2; return 3; }
  case "$mode" in ro) options=(--read-only);; rw) ;; *) return 2;; esac
  bounds=$(python3 "$(dirname -- "${BASH_SOURCE[0]}")/image_partition.py" "$image") || return 3
  read -r offset length <<< "$bounds"
  [[ "$offset" =~ ^[0-9]+$ && "$length" =~ ^[0-9]+$ ]] || return 3
  losetup --find --show "${options[@]}" --offset "$offset" --sizelimit "$length" "$image"
}
