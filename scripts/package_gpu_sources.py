"""Collect the exact corresponding upstream sources for a GPU release."""
from pathlib import Path
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tarfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    index_url = "https://repo.msys2.org/mingw/sources/"
    index = urllib.request.urlopen(index_url, timeout=60).read().decode()
    links = re.findall(r'href="([^"/]+\.src\.tar\.(?:zst|gz|xz))"', index)
    print(f"Source index: {len(links)} packages", flush=True)
    dlls = sorted(args.bundle.joinpath("host").glob("*.dll"))
    env = dict(os.environ, MSYSTEM="UCRT64")
    bash = args.tools / "msys64/usr/bin/bash.exe"
    owners = subprocess.check_output([str(bash), "--login", "-c",
        "pacman -Qoq " + " ".join("/ucrt64/bin/" + p.name for p in dlls)], env=env, text=True)
    names = sorted(set(owners.splitlines()) | {"mingw-w64-ucrt-x86_64-crt", "mingw-w64-i686-crt"})
    versions = subprocess.check_output([str(bash), "--login", "-c", "pacman -Q " + " ".join(names)],
                                      env=env, text=True)
    requests = []
    for line in versions.splitlines():
        name, version = line.split()
        metadata = (args.tools / f"msys64/var/lib/pacman/local/{name}-{version}/desc").read_text()
        base = metadata.split("%BASE%\n", 1)[1].splitlines()[0]
        prefix = f"{base}-{version}.src."
        matches = [link for link in links if link.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f"Exact source package missing for {name} {version}: {matches}")
        requests.append((name, version, matches[0]))
    def fetch(item):
        name, version, filename = item
        target = args.destination / filename
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".partial")
            with urllib.request.urlopen(index_url + filename, timeout=120) as response, temporary.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            temporary.replace(target)
        with tarfile.open(target, "r:*") as tar:
            tar.getmembers()
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(f"SOURCE_READY {name} {version}", flush=True)
        return dict(package=name, version=version, file=filename, sha256=digest, url=index_url + filename)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        unique = {item[2]: item for item in requests}
        records = list(pool.map(fetch, unique.values()))
    (args.destination / "dependency-sources.json").write_text(json.dumps(records, indent=2) + "\n")
    with tarfile.open(args.destination.parent / "gpu-dependency-sources.tar", "w") as tar:
        for path in sorted(args.destination.iterdir()):
            if path.is_file() and not path.name.endswith(".partial"):
                tar.add(path, arcname=path.name, recursive=False)
    archive = args.destination.parent / "gpu-corresponding-sources.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
        for name in ("qemu-3dfx", "wined3d-windows"):
            root = args.tools / name
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if any(p in (".git", "build-host", "build", "output", "autom4te.cache", "__pycache__") for p in relative.parts):
                    continue
                if path.is_file():
                    tar.add(path, arcname=name + "/" + relative.as_posix(), recursive=False)
    print(f"SOURCE_ARCHIVE_READY {archive} {archive.stat().st_size}", flush=True)


if __name__ == "__main__":
    main()
