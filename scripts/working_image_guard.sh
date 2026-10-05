#!/usr/bin/env bash
# Explicit, portable target selection for historical image maintenance tools.
require_working_image() {
  local selected=$1 working original
  if [[ -z "${M90_WORK_IMAGE:-}" || -z "${M90_ORIGINAL_IMAGE:-}" ]]; then
    echo 'Set M90_WORK_IMAGE and M90_ORIGINAL_IMAGE to separate existing images.' >&2
    return 3
  fi
  working=$(realpath -e -- "$M90_WORK_IMAGE") || return 3
  original=$(realpath -e -- "$M90_ORIGINAL_IMAGE") || return 3
  [[ -f "$working" && -f "$original" && "$working" != "$original" &&
     ! "$working" -ef "$original" && "$selected" == "$working" ]] || {
    echo 'Refusing an original image or an unapproved working-copy path.' >&2
    return 3
  }
}
