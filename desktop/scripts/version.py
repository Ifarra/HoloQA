"""Desktop package.json is authoritative; engine 2.x has its own release line."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def valid_version(value):
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('Use a three-part desktop version, for example 0.1.1')
    if any(int(part) > 65535 for part in value.split('.')):
        raise ValueError('Windows version components must not exceed 65535')
    return value


def versions(root=ROOT):
    read = lambda name: (root / name).read_text('utf-8')
    package = json.loads(read('desktop/package.json'))
    lock = json.loads(read('desktop/package-lock.json'))
    cargo_lock = tomllib.loads(read('desktop/src-tauri/Cargo.lock'))
    python = re.search(r'^VERSION = "([^"]+)"$', read('src/holoqa/desktop_version.py'), re.M)
    if not python:
        raise ValueError('Missing desktop Python version')
    return {
        'package.json': package['version'],
        'package-lock.json': lock['version'],
        'package-lock.json packages root': lock['packages']['']['version'],
        'tauri.conf.json': json.loads(read('desktop/src-tauri/tauri.conf.json'))['version'],
        'Cargo.toml': tomllib.loads(read('desktop/src-tauri/Cargo.toml'))['package']['version'],
        'Cargo.lock': next(p['version'] for p in cargo_lock['package'] if p['name'] == 'holoqa-desktop'),
        'desktop_version.py': python.group(1),
    }


def check(root=ROOT, tag=None):
    found = versions(root)
    version = valid_version(found['package.json'])
    mismatches = [f'{name}={value}' for name, value in found.items() if value != version]
    if mismatches:
        raise ValueError('Desktop version drift: ' + ', '.join(mismatches))
    if tag is not None and tag != f'desktop-v{version}':
        raise ValueError(f'Tag must be desktop-v{version}; received {tag}')
    return version


def set_version(value, root=ROOT):
    valid_version(value)
    updates = {}
    for name in ['desktop/package.json', 'desktop/package-lock.json', 'desktop/src-tauri/tauri.conf.json']:
        data = json.loads((root / name).read_text('utf-8'))
        data['version'] = value
        if name.endswith('package-lock.json'):
            data['packages']['']['version'] = value
        updates[name] = json.dumps(data, indent=2) + '\n'
    for name, pattern in [
        ('desktop/src-tauri/Cargo.toml', r'(^version = ")[^"]+("$)'),
        ('desktop/src-tauri/Cargo.lock', r'(\[\[package\]\]\nname = "holoqa-desktop"\nversion = ")[^"]+("\n)'),
        ('src/holoqa/desktop_version.py', r'(^VERSION = ")[^"]+("$)'),
    ]:
        content, count = re.subn(pattern, lambda m: m[1] + value + m[2], (root / name).read_text('utf-8'), count=1, flags=re.M)
        if count != 1:
            raise ValueError('Cannot locate desktop version in ' + name)
        updates[name] = content
    for name, content in updates.items():
        (root / name).write_text(content, encoding='utf-8')
    return check(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--set', metavar='VERSION')
    group.add_argument('--check', action='store_true')
    parser.add_argument('--tag', help='Require the exact desktop-vX.Y.Z release tag')
    args = parser.parse_args()
    try:
        if args.set:
            set_version(args.set)
        print('Desktop version verified:', check(tag=args.tag))
    except (ValueError, KeyError, StopIteration) as error:
        parser.exit(1, str(error) + '\n')
