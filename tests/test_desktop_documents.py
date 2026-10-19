"""Untrusted attachment parsing stays bounded and outside the engine process."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import pytest

from holoqa import desktop_documents as documents
from holoqa.desktop_documents import DocumentCancelled, DocumentError, read_document


def document(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize('extension', ['txt', 'md', 'markdown'])
def test_utf8_document_uses_owned_extractor_and_preserves_snapshot(tmp_path, extension):
    raw = '\ufeffRequirements é\nMust show 日本語 ✓'.encode('utf-8')
    path = document(tmp_path, f'requirements.{extension}', raw)
    result = read_document(path)
    assert result['text'] == raw.decode('utf-8-sig')
    assert result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['size'] == len(raw)
    assert result['name'] == Path(path).name
    assert result['characters'] == len(result['text'])


@pytest.mark.parametrize('data,code', [(b'\xff\xfe', 'encoding'), (b'', 'no_text'),
                                       (b'plain\x00binary', 'binary_content'),
                                       (b'a' * (documents.MAX_TEXT + 1), 'text_limit')])
def test_invalid_text_is_actionable_without_source_contents(tmp_path, data, code):
    path = document(tmp_path, 'sensitive-customer-name.txt', data)
    with pytest.raises(DocumentError) as error:
        read_document(path)
    assert error.value.code == code
    assert 'sensitive-customer-name' not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_source_byte_limit_and_unsupported_extension(tmp_path):
    path = document(tmp_path, 'oversized.txt', b'x' * (documents.MAX_BYTES + 1))
    with pytest.raises(DocumentError) as error:
        read_document(path)
    assert error.value.code == 'source_limit'
    path = document(tmp_path, 'program.exe', b'not text')
    with pytest.raises(DocumentError) as error:
        read_document(path)
    assert error.value.code == 'unsupported_type'


def docx(tmp_path, entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return document(tmp_path, 'requirements.docx', stream.getvalue())


def test_docx_parses_text_without_unpacking_files(tmp_path):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Ready é</w:t></w:r></w:p></w:body></w:document>'
    path = docx(tmp_path, [('word/document.xml', xml), ('../must-not-unpack.txt', 'embedded')])
    before = set(tmp_path.iterdir())
    assert read_document(path)['text'] == 'Ready é'
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('entries', [
    [(f'member-{i}', '') for i in range(2001)],
    [('big-member', b'a' * (16 * 1024 * 1024 + 1))],
    [('word/document.xml', b'a' * (2 * 1024 * 1024 + 1))],
])
def test_compressed_docx_expansion_is_rejected(tmp_path, entries):
    path = docx(tmp_path, entries)
    assert Path(path).stat().st_size < documents.MAX_BYTES
    with pytest.raises(DocumentError) as error:
        read_document(path)
    assert error.value.code == 'archive_limit'


@pytest.mark.parametrize('extension,data', [('docx', b'bad zip private body'),
                                           ('pdf', b'%PDF-broken private body')])
def test_malformed_documents_do_not_leak_parser_diagnostics(tmp_path, extension, data):
    path = document(tmp_path, f'private-filename.{extension}', data)
    with pytest.raises(DocumentError) as error:
        read_document(path)
    assert error.value.code == 'malformed'
    assert not any(value in str(error.value) for value in ['private body', 'private-filename', str(tmp_path)])


def pdf(tmp_path, *, text=True, encrypted=False, pages=1):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            content = DecodedStreamObject()
            content.set_data(b'BT /F1 12 Tf 10 10 Td (The page must be Ready) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(content)
    if encrypted:
        writer.encrypt('test-only-password')
    output = io.BytesIO(); writer.write(output)
    return document(tmp_path, 'requirements.pdf', output.getvalue())


def test_pdf_extracts_actual_text(tmp_path):
    assert 'The page must be Ready' in read_document(pdf(tmp_path))['text']


@pytest.mark.parametrize('options,code', [({'text': False}, 'no_text'),
                                         ({'encrypted': True}, 'encrypted'),
                                         ({'pages': 101}, 'page_limit')])
def test_textless_encrypted_and_large_page_count_pdfs_are_explicit(tmp_path, options, code):
    with pytest.raises(DocumentError) as error:
        read_document(pdf(tmp_path, **options))
    assert error.value.code == code


def fake_worker(monkeypatch, script):
    processes = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(documents, '_worker_command', lambda _: [sys.executable, '-u', '-c', script])
    monkeypatch.setattr(documents.subprocess, 'Popen', launch)
    return processes


def test_timeout_kills_and_reaps_worker(tmp_path, monkeypatch):
    processes = fake_worker(monkeypatch, 'import time; time.sleep(60)')
    with pytest.raises(DocumentError) as error:
        read_document(str(tmp_path / 'unused.txt'), timeout=.15)
    assert error.value.code == 'timeout'
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_cancellation_kills_and_reaps_worker(tmp_path, monkeypatch):
    processes = fake_worker(monkeypatch, 'import time; time.sleep(60)')
    cancelled = threading.Event()
    timer = threading.Timer(.15, cancelled.set); timer.start()
    try:
        with pytest.raises(DocumentCancelled):
            read_document(str(tmp_path / 'unused.txt'), cancel=cancelled)
    finally:
        timer.cancel(); timer.join()
    assert len(processes) == 1 and processes[0].poll() is not None


def test_pre_cancelled_extraction_never_starts_worker(tmp_path, monkeypatch):
    processes = fake_worker(monkeypatch, 'raise RuntimeError("must not start")')
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(DocumentCancelled):
        read_document(str(tmp_path / 'unused.txt'), cancel=cancelled)
    assert not processes


def test_noisy_worker_output_is_bounded_and_worker_is_reaped(tmp_path, monkeypatch):
    processes = fake_worker(monkeypatch,
        f'import sys,time; sys.stdout.write("x" * {documents.MAX_OUTPUT * 10}); sys.stdout.flush(); time.sleep(60)')
    with pytest.raises(DocumentError) as error:
        read_document(str(tmp_path / 'unused.txt'))
    assert error.value.code == 'output_limit'
    assert processes[0].poll() is not None


@pytest.mark.parametrize('script,code', [('print("not json")', 'worker_protocol'),
                                        ('raise SystemExit(2)', 'worker_failed')])
def test_worker_failure_is_actionable(tmp_path, monkeypatch, script, code):
    processes = fake_worker(monkeypatch, script)
    with pytest.raises(DocumentError) as error:
        read_document(str(tmp_path / 'unused.txt'))
    assert error.value.code == code
    assert processes[0].poll() is not None


def test_worker_start_failure_does_not_expose_command_or_paths(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('secret-path-and-command')
    monkeypatch.setattr(documents.subprocess, 'Popen', fail)
    with pytest.raises(DocumentError) as error:
        read_document(str(tmp_path / 'unused.txt'))
    assert error.value.code == 'worker_start'
    assert 'secret-path' not in str(error.value)


def test_frozen_worker_uses_bundled_engine_dispatch(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert documents._worker_command('requirements é.pdf') == [sys.executable, '--extract-document', 'requirements é.pdf']


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux resource limits')
def test_worker_resource_limits_are_applied_without_changing_test_process(monkeypatch):
    import resource
    limits = {}
    monkeypatch.setattr(resource, 'setrlimit', lambda kind, value: limits.__setitem__(kind, value))
    documents._worker_limits()
    assert limits[resource.RLIMIT_AS][0] == 512 * 1024 * 1024
    assert limits[resource.RLIMIT_CPU] == (8, 9)
    assert limits[resource.RLIMIT_FSIZE][0] == documents.MAX_OUTPUT


def test_compressed_pdf_stream_expansion_is_bounded(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=200,height=200)
    stream=DecodedStreamObject()
    stream.set_data(b' ' * (9 * 1024 * 1024))
    page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
    source=tmp_path/'compressed.pdf'
    writer.write(source)
    assert source.stat().st_size < 5 * 1024 * 1024
    with pytest.raises(DocumentError):
        read_document(str(source))
