"""Original PDF fixtures and local import checks."""

import shutil

from fastapi.testclient import TestClient
import pytest

from chaptercast import pdf_import
from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect


def pdf_bytes(*streams: bytes) -> bytes:
    """Build an original PDF from one content stream per page."""
    page_refs = " ".join(f"{4 + i * 2} 0 R" for i in range(len(streams)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {len(streams)} /Kids [{page_refs}] >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, stream in enumerate(streams):
        objects.extend([
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
             f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + i * 2} 0 R >>").encode(),
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        ])
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    index = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{index}\n%%EOF\n".encode())
    return bytes(output)


ORIGINAL_PDF = pdf_bytes(
    b"BT /F1 16 Tf 72 720 Td (A page from the caf\\351.) Tj ET\n"
    b"BT /F1 14 Tf 72 600 Td (A/N: Original note.) Tj ET",
    b"BT /F1 16 Tf 72 720 Td (The second page begins here.) Tj ET",
)
IMAGE_PDF = pdf_bytes(b"q 60 0 0 60 70 520 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \xff\nEI Q")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def poppler():
    if not shutil.which("pdftotext"):
        pytest.skip("Poppler is required for real PDF extraction")


def test_pdf_uses_shared_import_and_narration_queue(client, tmp_path, poppler):
    response = client.post("/api/import/upload", params={"filename": "Original.PDF", "exclude_notes": "true"}, content=ORIGINAL_PDF)
    assert response.status_code == 201, response.json()
    chapter = response.json()
    assert chapter["title"] == "Original"
    assert chapter["paragraphs"] == 2
    assert "text" not in chapter
    with connect(tmp_path / "metadata.sqlite3") as db:
        stored, = db.execute("SELECT text_path FROM chapters WHERE id = ?", (chapter["id"],)).fetchone()
    assert list((tmp_path / "uploads").iterdir()) == [tmp_path / stored]
    assert (tmp_path / stored).read_text() == "A page from the café.\n\nThe second page begins here."
    assert client.post("/api/jobs", json={"chapter_id": chapter["id"]}).status_code == 202
    renamed = client.post("/api/import/upload", params={"filename": "Original.pdf", "title": "A different title"},
                          content=ORIGINAL_PDF)
    assert renamed.status_code == 201 and renamed.json()["title"] == "A different title"


def test_bad_and_image_only_pdfs_do_not_add_library_items(client, tmp_path, poppler):
    for data, code in [(b"plain text", "unreadable_pdf"), (b"%PDF-1.4\ninvalid", "unreadable_pdf"), (IMAGE_PDF, "pdf_no_text")]:
        response = client.post("/api/import/upload?filename=Scan.pdf", content=data)
        assert (response.status_code, response.json()["code"]) == (400, code)
    assert client.get("/api/library").json()["total"] == 0
    assert list((tmp_path / "uploads").iterdir()) == []


def test_pdf_limits_bound_upload_and_extractor_output(client, monkeypatch, poppler):
    response = client.post("/api/import/upload?filename=Big.pdf", content=b"x" * (pdf_import.MAX_UPLOAD_BYTES + 1))
    assert (response.status_code, response.json()["code"]) == (413, "too_large")
    monkeypatch.setattr(pdf_import, "MAX_TEXT_BYTES", 16)
    response = client.post("/api/import/upload?filename=Big.pdf", content=ORIGINAL_PDF)
    assert (response.status_code, response.json()["code"]) == (413, "too_large")
    assert client.get("/api/library").json()["total"] == 0


def test_missing_pdf_converter_leaves_paste_available(client, monkeypatch):
    monkeypatch.setattr(pdf_import.shutil, "which", lambda _name: None)
    response = client.post("/api/import/upload?filename=Original.pdf", content=ORIGINAL_PDF)
    assert (response.status_code, response.json()["code"]) == (503, "pdf_unavailable")
    assert client.post("/api/import/text", json={"text": "Original pasted prose."}).status_code == 201


def test_pdf_extractor_times_out_without_importing_partial_text(client, tmp_path, monkeypatch):
    converter = tmp_path / "slow-pdftotext"
    converter.write_text("#!/bin/sh\nprintf 'Partial text'\nexec sleep 30\n")
    converter.chmod(0o700)
    monkeypatch.setattr(pdf_import.shutil, "which", lambda _name: str(converter))
    monkeypatch.setattr(pdf_import, "_TIMEOUT", 0.1)
    response = client.post("/api/import/upload?filename=Original.pdf", content=ORIGINAL_PDF)
    assert (response.status_code, response.json()["code"]) == (400, "pdf_timeout")
    assert client.get("/api/library").json()["total"] == 0
    assert list((tmp_path / "uploads").iterdir()) == []
