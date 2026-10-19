"""Release versions and checksums must describe the actual shipped desktop."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('holoqa_desktop_version_script', ROOT / 'desktop/scripts/version.py')
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


@pytest.fixture
def release_root(tmp_path):
    paths = ['desktop/package.json', 'desktop/package-lock.json', 'desktop/src-tauri/tauri.conf.json',
             'desktop/src-tauri/Cargo.toml', 'desktop/src-tauri/Cargo.lock',
             'src/holoqa/desktop_version.py', 'pyproject.toml']
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    # Test fixtures keep a fixed version when the shipped desktop advances.
    version.set_version("0.1.0", tmp_path)
    return tmp_path


def test_checked_in_desktop_versions_agree():
    assert version.check() == json.loads((ROOT / 'desktop/package.json').read_text())['version']


def test_version_bump_updates_only_desktop_not_engine_or_dependencies(release_root):
    lock_path = release_root / 'desktop/src-tauri/Cargo.lock'
    before = tomllib.loads(lock_path.read_text())['package']
    core = (release_root / 'pyproject.toml').read_bytes()
    assert version.set_version('0.2.3', release_root) == '0.2.3'
    assert version.check(release_root, tag='desktop-v0.2.3') == '0.2.3'
    assert set(version.versions(release_root).values()) == {'0.2.3'}
    after = tomllib.loads(lock_path.read_text())['package']
    assert [p for p in before if p['name'] != 'holoqa-desktop'] == [p for p in after if p['name'] != 'holoqa-desktop']
    assert (release_root / 'pyproject.toml').read_bytes() == core


def test_drift_and_wrong_tag_fail_before_release(release_root):
    with pytest.raises(ValueError, match='Tag must be'):
        version.check(release_root, tag='desktop-v99.0.0')
    path = release_root / 'desktop/src-tauri/tauri.conf.json'
    data = json.loads(path.read_text()); data['version'] = '0.0.1'; path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='drift'):
        version.check(release_root)


@pytest.mark.parametrize('invalid', ['v1.2.3', '01.2.3', '1.2', '1.2.3-beta', '65536.0.0', '1.2.3;echo bad'])
def test_invalid_or_nonportable_version_does_not_edit_files(release_root, invalid):
    before = (release_root / 'desktop/package.json').read_bytes()
    with pytest.raises(ValueError):
        version.set_version(invalid, release_root)
    assert (release_root / 'desktop/package.json').read_bytes() == before


def release_script(monkeypatch):
    monkeypatch.setitem(sys.modules, 'version', version)
    spec = importlib.util.spec_from_file_location('holoqa_prepare_release', ROOT / 'desktop/scripts/prepare_release.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_release_assets_have_stable_names_checksums_and_source_provenance(release_root, tmp_path, monkeypatch):
    script = release_script(monkeypatch)
    source = tmp_path / 'artifact'; source.mkdir()
    contents = b'MZ' + bytes(range(256))
    (source / 'HoloQA Desktop_0.1.0_x64-setup.exe').write_bytes(contents)
    destination = tmp_path / 'assets'
    manifest = script.prepare(source, destination, 'a' * 40, '123', root=release_root)
    assert manifest['installer'] == 'HoloQA-Desktop-0.1.0-windows-x64-setup.exe'
    assert manifest['sha256'] == hashlib.sha256(contents).hexdigest()
    assert manifest['sourceCommit'] == 'a' * 40 and manifest['engineVersion'] == tomllib.loads((release_root / 'pyproject.toml').read_text())['project']['version']
    assert manifest['prerelease'] and manifest['unsigned']
    for line in (destination / 'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        script.prepare(source, destination, 'a' * 40, '123', root=release_root)


def test_release_rejects_missing_ambiguous_or_invalid_installer(release_root, tmp_path, monkeypatch):
    script = release_script(monkeypatch)
    source = tmp_path / 'artifact'; source.mkdir()
    destination = tmp_path / 'assets'
    with pytest.raises(ValueError, match='exactly one'):
        script.prepare(source, destination, 'a' * 40, '123', root=release_root)
    (source / 'one.exe').write_text('not an executable')
    with pytest.raises(ValueError, match='Windows executable'):
        script.prepare(source, destination, 'a' * 40, '123', root=release_root)
    assert not destination.exists()


def test_release_manifest_uses_engine_version_from_verified_source(release_root, tmp_path, monkeypatch):
    script = release_script(monkeypatch)
    source = tmp_path / 'artifact'; source.mkdir()
    (source / 'installer.exe').write_bytes(b'MZfixture')
    source_project = tmp_path / 'source-pyproject.toml'
    source_project.write_text('[project]\nversion = "1.9.9"\n')
    result = script.prepare(source, tmp_path / 'assets', 'a' * 40, '123', root=release_root, engine_project=source_project)
    assert result['engineVersion'] == '1.9.9'


def verifier():
    spec = importlib.util.spec_from_file_location('holoqa_verify_release', ROOT / 'desktop/scripts/verify_release_run.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_only_matching_successful_repository_workflow_can_be_promoted():
    module = verifier()
    metadata = {'head_sha': 'a' * 40, 'path': '.github/workflows/desktop.yml',
                'repository': {'full_name': 'Ifarra/HoloQA'}, 'head_repository': {'full_name': 'Ifarra/HoloQA'},
                'status': 'completed', 'conclusion': 'success'}
    assert module.verify(metadata, 'a' * 40, 'Ifarra/HoloQA') == 'a' * 40
    for field, bad in [('head_sha', 'b' * 40), ('path', '.github/workflows/other.yml'),
                       ('repository', {'full_name': 'other/repo'}), ('head_repository', {'full_name': 'fork/repo'}),
                       ('status', 'in_progress'), ('conclusion', 'failure')]:
        with pytest.raises(ValueError):
            module.verify({**metadata, field: bad}, 'a' * 40, 'Ifarra/HoloQA')
