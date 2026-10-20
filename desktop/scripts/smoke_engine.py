"""Executable-level smoke: UTF-8 stdio, read-only project discovery, clean exit."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="holoqa-desktop-") as temp:
    root = Path(temp)
    project = root / "project é with spaces"
    project.mkdir()
    process = subprocess.run([sys.argv[1], "--state-dir", str(root / "state")],
        input="\n".join(json.dumps(item) for item in [
            {"id": 1, "method": "hello"},
            {"id": 2, "method": "project.open", "params": {"path": str(project)}},
        ]) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert process.returncode == 0, "Sidecar failed to exit cleanly"
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    by_id = {message["id"]: message for message in messages if "id" in message}
    assert by_id[1]["result"]["protocol"] == 1
    assert "holoqa_observe" in by_id[1]["result"]["qaTools"]
    assert "holoqa_judge" in by_id[1]["result"]["qaTools"]
    assert by_id[2]["result"]["name"] == project.name
    assert not list(project.iterdir()), "Discovery must not write to the project"
    # Exercise document extraction through the bundled engine and its worker,
    # including the PDF dependency (without a user-installed Python runtime).
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    document = root / "requirements é.txt"
    document.write_text("Checkout acceptance criteria", encoding="utf-8")
    pdf = root / "requirements.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 10 100 Td (PDF checkout criteria) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(pdf)
    for source in (document, pdf):
        direct = subprocess.run([sys.argv[1], "--extract-document", str(source)],
            capture_output=True, text=True, encoding="utf-8", timeout=45)
        assert direct.returncode == 0
        assert json.loads(direct.stdout)["result"]["characters"] > 0
    print("Packaged engine smoke passed, including frozen TXT/PDF extraction")
