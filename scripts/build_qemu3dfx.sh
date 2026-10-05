#!/usr/bin/env bash
# Build only. Never boots a VM or installs a driver on the host.
set -euo pipefail
[[ $# == 3 ]] || { echo 'usage: build_qemu3dfx.sh host|guest QEMU3DFX_CHECKOUT QEMU_9_2_2_ARCHIVE' >&2; exit 2; }
mode=$1
project=$(realpath -e -- "$2")
archive=$(realpath -e -- "$3")
[[ $(git -C "$project" rev-parse HEAD) == 920661f3b48bd278b93acd9cf9ff8c968afb02c9 ]] || { echo 'Unexpected qemu-3dfx revision' >&2; exit 3; }
[[ $(sha256sum "$archive" | cut -d' ' -f1) == 752eaeeb772923a73d536b231e05bcc09c9b1f51690a41ad9973d900e4ec9fbf ]] || { echo 'Unexpected QEMU source archive' >&2; exit 3; }
case "$mode" in
  host)
    [[ ${MSYSTEM:-} == UCRT64 ]] || { echo 'Use the MSYS2 UCRT64 shell' >&2; exit 3; }
    src="$project/qemu-9.2.2"
    if [[ ! -f "$project/host-source-patched" ]]; then
      [[ ! -e "$src" ]] || { echo 'Source directory already exists without build marker; refusing overwrite' >&2; exit 3; }
      tar -xf "$archive" -C "$project" --exclude='qemu-9.2.2/roms'
      rsync -r "$project/qemu-0/hw/3dfx" "$project/qemu-1/hw/mesa" "$src/hw/"
      (cd "$src"; patch -p0 -i "$project/00-qemu92x-mesa-glide.patch"; bash "$project/scripts/sign_commit" -git="$project")
      touch "$project/host-source-patched"
    fi
    python3 "$(dirname -- "$0")/qemu3dfx_host_patch.py" "$project"
    # Windows file URLs need file:///C:/..., not file://C:/.... The symlink
    # install view is developer convenience only; distribution copies files.
    python3 - "$src" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for name in ('include/hw/i386/pc.h', 'hw/mesa/mglfuncs.h'):
    p = root / name
    s = p.read_text()
    for old, new in (('0xec000000', '0x9c000000'), ('0xea000000', '0x9a000000'),
                     ('0xefffe000', '0x9fffe000'), ('(0xE0U << 24)', '(0x90U << 24)')):
        s = s.replace(old, new)
    p.write_text(s)
p = root / 'python/scripts/mkvenv.py'
s = p.read_text()
old = 'f"file://{str(wheels_dir)}"'
new = 'Path(wheels_dir).absolute().as_uri()'
if old in s: p.write_text(s.replace(old, new))
elif new not in s: raise SystemExit('Unknown mkvenv revision')
p = root / 'scripts/symlink-install-tree.py'
s = p.read_text()
marker = '# M90 copied Windows runtime bundle'
if marker not in s:
    old = 'import sys\n'
    if old not in s: raise SystemExit('Unknown symlink-install-tree revision')
    s = s.replace(old, old + '\n' + marker + '\nif os.name == "nt":\n    sys.exit(0)\n', 1)
    p.write_text(s)
PY
    mkdir -p "$project/build-host"
    cd "$project/build-host"
    # GCC sees both MSYS and Windows paths. Keep __FILE__ and debug paths
    # independent of the developer's account and checkout directory.
    project_win=$(cygpath -m "$project")
    [[ "$project" != *[[:space:]]* ]] || { echo 'Use an MSYS2 build checkout without spaces' >&2; exit 3; }
    prefix_flags="-ffile-prefix-map=$project=/usr/src/qemu-3dfx -ffile-prefix-map=$project_win=/usr/src/qemu-3dfx"
    "$src/configure" --target-list=x86_64-softmmu --enable-whpx --enable-sdl \
      --enable-opengl --enable-slirp --disable-gtk --disable-werror --disable-docs \
      --extra-cflags="$prefix_flags" --extra-cxxflags="$prefix_flags"
    jobs=${M90_BUILD_JOBS:-2}
    [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid M90_BUILD_JOBS' >&2; exit 3; }
    ninja -j"$jobs" qemu-system-x86_64.exe
    ;;
  guest)
    [[ ${MSYSTEM:-} == MINGW32 ]] || { echo 'Use the MSYS2 MINGW32 shell' >&2; exit 3; }
    python_command=python3
    if ! command -v python3 >/dev/null && [[ -x /ucrt64/bin/python.exe ]]; then
      python_command=/ucrt64/bin/python.exe
    fi
    "$python_command" "$(dirname -- "$0")/qemu3dfx_guest_patch.py" "$project"
    "$python_command" - "$project" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for name in ('qemu-1/hw/mesa/mglfuncs.h', 'wrappers/mesa/src/wrapgl32.c'):
    path = root / name
    value = path.read_text()
    for old, new in (('0xec000000', '0x9c000000'), ('0xea000000', '0x9a000000'),
                     ('0xefffe000', '0x9fffe000'), ('(0xE0U << 24)', '(0x90U << 24)')):
        value = value.replace(old, new)
    path.write_text(value)
PY
    mkdir -p "$project/wrappers/mesa/build"
    cd "$project/wrappers/mesa/build"
    bash "$project/scripts/conf_wrapper"
    # The emulated game CPU promises SSE2, not SSE4.2 or POPCNT.
    flags='-march=i686 -msse2 -mfpmath=sse -mtune=generic -O3 -pipe -I../../../qemu-1/hw/mesa -I../../fxlib -Wall -Werror -fomit-frame-pointer -fuse-linker-plugin -flto=auto'
    make clean
    make CFLAGS="$flags" fxlib
    make -j2 CFLAGS="$flags" opengl32.dll exports-check
    make -B CFLAGS="$flags" wglinfo.exe
    mkdir -p "$project/wrappers/3dfx/build"
    make -C "$project/wrappers/3dfx/drv" fxptl.sys fxmemmap.vxd
    cd "$project/wrappers/3dfx/build"
    shasum fxmemmap.vxd fxptl.sys | sed 's/ \*/ /' | diff - <(tr -d '\r' < ../drv/shasum.txt)
    ;;
  *) echo 'Choose host or guest' >&2; exit 2 ;;
esac
printf 'QEMU3DFX_BUILD_COMPLETE %s (no guest started)\n' "$mode"
