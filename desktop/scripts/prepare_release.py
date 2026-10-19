"""Turn one verified Windows installer into stable, checksummed release assets."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib
from version import ROOT, check


def prepare(source, destination, commit, run, *, root=ROOT, engine_project=None):
    version = check(root)
    if not re.fullmatch('[0-9a-f]{40}', commit) or not str(run).isdigit():
        raise ValueError('A full source commit and numeric successful build run are required')
    source, destination = Path(source), Path(destination)
    installers = list(source.glob('*.exe'))
    if len(installers) != 1 or not installers[0].is_file() or installers[0].is_symlink():
        raise ValueError('Expected exactly one regular Windows installer')
    with installers[0].open('rb') as handle:
        if handle.read(2) != b'MZ':
            raise ValueError('Installer is not a Windows executable')
    destination.mkdir(parents=True, exist_ok=False)
    name = f'HoloQA-Desktop-{version}-windows-x64-setup.exe'
    installer = destination / name
    shutil.copyfile(installers[0], installer)
    with installer.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    manifest = {
        'product': 'HoloQA Desktop', 'version': version,
        'engineVersion': tomllib.loads(Path(engine_project or root / 'pyproject.toml').read_text('utf-8'))['project']['version'],
        'tag': f'desktop-v{version}', 'sourceCommit': commit,
        'buildRun': f'https://github.com/Ifarra/HoloQA/actions/runs/{run}',
        'platform': 'windows-x64', 'unsigned': True, 'prerelease': True,
        'installer': name, 'sha256': digest, 'size': installer.stat().st_size,
    }
    (destination / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    manifest_hash = hashlib.sha256((destination / 'release-manifest.json').read_bytes()).hexdigest()
    (destination / 'SHA256SUMS.txt').write_text(f'{digest}  {name}\n{manifest_hash}  release-manifest.json\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--engine-project', type=Path, help='pyproject.toml read from the verified source commit')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.input, args.output, args.commit, args.run, engine_project=args.engine_project), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
