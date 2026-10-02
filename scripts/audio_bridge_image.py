"""Switch only our audio-backend marker in an already prepared working copy."""
import argparse
from pathlib import Path

FLAG = 'NVRAM/m90_audio_bridge.enabled'


def configure(root: Path, enabled: bool):
    nvram = root / 'NVRAM'
    if nvram.is_symlink() or not nvram.is_dir():
        raise ValueError('Expected a real NVRAM directory')
    target = root / FLAG
    temporary = target.with_name(target.name + '.new')
    if target.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise ValueError('Unsafe or incomplete audio-backend marker')
    marker = nvram / 'm90_setup_stage.txt'
    if not marker.is_file() or marker.read_text().strip() != 'stage=ready':
        raise ValueError('Working image is not ready')
    if enabled:
        temporary.write_text('protocol=M9A1\n', encoding='ascii')
        temporary.replace(target)
    else:
        target.unlink(missing_ok=True)
    return 'PCM-Bridge aktiviert' if enabled else 'AC97-Ausgabe aktiviert'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('mode', choices=('bridge', 'ac97'))
    args = parser.parse_args()
    print(configure(args.root, args.mode == 'bridge'))


if __name__ == '__main__':
    main()
