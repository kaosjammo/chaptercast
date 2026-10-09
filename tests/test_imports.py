"""Original synthetic fixtures only; these tests do not establish live FFN access."""

from io import BytesIO
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect
from chaptercast.imports import ImportFailure, MAX_TEXT_BYTES, MAX_UPLOAD_BYTES, read_epub, text_chapter
from chaptercast import ffn


ORIGINAL_TEXT = "The clockwork kite rose above the orchard.\r\n\r\nA brass bird followed it home."
ORIGINAL_HTML = '<html><head><title>Original fixture</title></head><body><nav>Menu</nav><div id="storytext"><p>The clockwork <em>kite</em> rose.</p><p>A brass bird followed.<br>It sang.</p><p>A/N: Original test note.</p></div><footer>Footer</footer></body></html>'


def epub_bytes():
    """Generate an original two-chapter EPUB, never a downloaded book."""
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="BOOK/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        book.writestr("BOOK/package.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="one"/><itemref idref="two"/><itemref idref="nav" linear="no"/></spine></package>')
        for name, title in [("one", "The Kite"), ("two", "The Bird")]:
            book.writestr(f"BOOK/{name}.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{title}</title></head><body><h1>{title}</h1><p>Original fixture: a {name} adventure.</p><aside epub:type="footnote">A fixture footnote.</aside><p>A/N: An original author note.</p></body></html>')
        book.writestr("BOOK/nav.xhtml", "<html><body>Navigation</body></html>")
    return stream.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        yield client


def test_text_preserves_paragraphs_and_optional_notes():
    assert text_chapter(ORIGINAL_TEXT).text == ORIGINAL_TEXT.replace("\r\n", "\n")
    text = "A/N: Original note.\n\n" + ORIGINAL_TEXT
    assert text_chapter(text).text.startswith("A/N:")
    assert text_chapter(text, exclude_notes=True).text == ORIGINAL_TEXT.replace("\r\n", "\n")
    assert text_chapter("One line\nwrapped line\n\nNext paragraph").text == "One line\nwrapped line\n\nNext paragraph"


@pytest.mark.parametrize("text", ["", " \n\n ", "bad\x00text", "x" * (MAX_TEXT_BYTES + 1)])
def test_bad_text_rejected(text):
    with pytest.raises(ImportFailure):
        text_chapter(text)


def test_paste_stored_privately_without_queue_or_raw_text_response(client, tmp_path):
    response = client.post("/api/import/text", json={"text": ORIGINAL_TEXT, "title": "Original fixture"})
    assert response.status_code == 201
    data = response.json()
    assert data["paragraphs"] == 2
    assert "text" not in data
    with connect(tmp_path / "metadata.sqlite3") as db:
        title, path, url = db.execute("SELECT title, text_path, source_url FROM chapters").fetchone()
        assert title == "Original fixture" and url is None
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert (tmp_path / path).read_text() == ORIGINAL_TEXT.replace("\r\n", "\n")
    assert client.get("/" + path).status_code == 404


def test_txt_and_rejections(client):
    assert client.post("/api/import/upload?filename=original.txt", content=ORIGINAL_TEXT.encode()).status_code == 201
    for filename, data, code in [("bad.txt", b"\xff", "invalid_utf8"), ("bad.html", b"<p>text</p>", "unsupported_file"), ("big.txt", b"x" * (MAX_TEXT_BYTES + 1), "too_large"), ("bad.epub", b"not a zip", "malformed_epub")]:
        response = client.post("/api/import/upload", params={"filename": filename}, content=data)
        assert response.status_code in (400, 413)
        assert response.json()["code"] == code


def test_epub_selection_uses_spine_and_only_selected_chapter(client, tmp_path):
    response = client.post("/api/import/upload?filename=original.epub", content=epub_bytes())
    assert response.status_code == 200
    data = response.json()
    assert [c["title"] for c in data["chapters"]] == ["The Kite", "The Bird"]
    response = client.post("/api/import/epub", json={"upload_id": data["upload_id"], "chapter_id": "two", "exclude_notes": True})
    assert response.status_code == 201
    with connect(tmp_path / "metadata.sqlite3") as db:
        path = db.execute("SELECT text_path FROM chapters").fetchone()[0]
    text = (tmp_path / path).read_text()
    assert "two adventure" in text and "one adventure" not in text
    assert "note" not in text and "Navigation" not in text
    assert client.post("/api/import/epub", json={"upload_id": "../../metadata", "chapter_id": "two"}).status_code == 400
    assert client.post("/api/import/epub", json={"upload_id": data["upload_id"], "chapter_id": "absent"}).status_code == 400


@pytest.mark.parametrize("name,payload", [("../escape", "oops"), ("/absolute", "oops"), ("META-INF/container.xml", '<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><container>&y;</container>'), ("huge", "x" * (MAX_UPLOAD_BYTES * 4))])
def test_hostile_archives_rejected(name, payload):
    data = BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(name, payload)
    with pytest.raises(ImportFailure):
        read_epub(data.getvalue())


@pytest.mark.parametrize("url", ["http://www.fanfiction.net/s/1/1/Test", "https://example.com/s/1/1/Test", "https://127.0.0.1/s/1/1/Test", "https://www.fanfiction.net@localhost/s/1/1/Test", "https://www.fanfiction.net:8443/s/1/1/Test", "https://www.fanfiction.net/", "https://www.fanfiction.net/s/1/0/Test", "https://www.fanfiction.net/s/1/1/Test?secret=x", "https://www.fanfiction.net/s/1/1/../other", "https://www.fanfiction.net/s/1/1/Test\n"])
def test_unsupported_urls_rejected_before_dns(monkeypatch, url):
    monkeypatch.setattr(ffn, "resolve_public", lambda *args: pytest.fail("must reject before DNS"))
    with pytest.raises(ImportFailure):
        ffn.fetch_chapter(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "::ffff:127.0.0.1", "2001:db8::1"])
def test_nonpublic_dns_rejected(monkeypatch, address):
    monkeypatch.setattr(ffn.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(ImportFailure, match="public"):
        ffn.resolve_public("www.fanfiction.net", 3)


def test_ffn_fixture_extraction_and_bot_rejection():
    chapter = ffn.parse_chapter(ORIGINAL_HTML, exclude_notes=True)
    assert chapter.text == "The clockwork kite rose.\n\nA brass bird followed.\nIt sang."
    for html in ['<html><title>Just a moment...</title><body>Verify you are human</body></html>', '<html><div id="storytext"><p>Checking your browser</p></div><script src="/cdn-cgi/challenge-platform/x"></script></html>', '<html><body>Menu only</body></html>']:
        with pytest.raises(ImportFailure) as exc:
            ffn.parse_chapter(html)
        assert exc.value.code == "ffn_blocked"


def test_ffn_api_403_explicit_fallback_no_saved_chapter(client, monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        raise ImportFailure("ffn_blocked", "FanFiction.net blocked this request. Paste text or upload TXT/EPUB.", 502)
    monkeypatch.setattr(ffn, "fetch_chapter", blocked)
    response = client.post("/api/import/url", json={"url": "https://www.fanfiction.net/s/1/1/Original"})
    assert response.status_code == 502
    assert response.json()["code"] == "ffn_blocked"
    assert "Paste" in response.json()["message"]
    with connect(tmp_path / "metadata.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM chapters").fetchone()[0] == 0


def test_invalid_json_errors_do_not_echo_private_content(client):
    response = client.post("/api/import/text", content='{"text":"PRIVATE ORIGINAL TEST SENTINEL"')
    assert response.status_code == 400
    assert "SENTINEL" not in response.text
    assert client.post("/api/import/text", json={"text": ["bad"]}).status_code == 400
    assert client.post("/api/import/text", content=b"x" * (MAX_TEXT_BYTES * 3)).status_code == 413


def test_no_failed_import_leaves_text_file(client, tmp_path):
    response = client.post("/api/import/text", json={"text": "A/N: Only an original note.", "exclude_notes": True})
    assert response.status_code == 400
    assert list((tmp_path / "uploads").iterdir()) == []


def test_invalid_unicode_and_deep_json_are_clean_errors(client):
    assert client.post("/api/import/text", content=json.dumps({"text": "\ud800"})).status_code == 400
    assert client.post("/api/import/text", content='[' * 2000 + ']' * 2000).status_code == 400


def test_epub_crc_corruption_rejected():
    raw = bytearray(epub_bytes())
    with zipfile.ZipFile(BytesIO(raw)) as book:
        member = book.getinfo("BOOK/one.xhtml")
        offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
    raw[offset + member.compress_size // 2] ^= 0x80
    with pytest.raises(ImportFailure) as exc:
        read_epub(bytes(raw))
    assert exc.value.code == "malformed_epub"


def test_upload_stream_limit_with_no_declared_length(client):
    def chunks():
        yield b"x" * (MAX_UPLOAD_BYTES // 2)
        yield b"x" * (MAX_UPLOAD_BYTES // 2 + 1)
    response = client.post("/api/import/upload?filename=original.epub", content=chunks())
    assert response.status_code == 413


def test_notes_are_optional_and_comments_inactive_html_are_excluded():
    html = ORIGINAL_HTML.replace('<p>The clockwork', '<!-- Original private comment --><script>Original inactive script</script><p>The clockwork')
    text = ffn.parse_chapter(html).text
    assert "A/N:" in text and "comment" not in text and "script" not in text
    chapters = read_epub(epub_bytes())
    from chaptercast.imports import epub_chapter
    assert "footnote" in epub_chapter(chapters[0]).text
    assert "footnote" not in epub_chapter(chapters[0], True).text


def test_html_disguised_as_txt_is_rejected(client):
    bot = b'<html><title>Just a moment...</title><body>Verify you are human</body></html>'
    response = client.post("/api/import/upload?filename=original.txt", content=bot)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_text"


def test_deeply_nested_html_does_not_crash_parser():
    html = '<html><div id="storytext">' + '<div>' * 2000 + '<p>Original deep nesting fixture.</p>' + '</div>' * 2000 + '</div></html>'
    assert ffn.parse_chapter(html).text == "Original deep nesting fixture."


def repack_epub(overrides):
    original = zipfile.ZipFile(BytesIO(epub_bytes()))
    result = BytesIO()
    with original, zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as book:
        for name in original.namelist():
            book.writestr(name, overrides.get(name, original.read(name)))
    return result.getvalue()


def test_standard_xhtml_doctype_supported_without_fetching_dtd():
    chapter = b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Original DTD fixture</title></head><body><p>Original standard XHTML fixture.</p></body></html>'
    assert read_epub(repack_epub({"BOOK/one.xhtml": chapter}))[0].title == "Original DTD fixture"


def test_xhtml_external_entities_rejected():
    chapter = b'<!DOCTYPE html [<!ENTITY forbidden SYSTEM "file:///etc/passwd">]><html><body><p>&forbidden;</p></body></html>'
    with pytest.raises(ImportFailure) as exc:
        read_epub(repack_epub({"BOOK/one.xhtml": chapter}))
    assert exc.value.code == "malformed_epub"


@pytest.mark.parametrize("variant", ["duplicates", "symlink", "member_count", "ratio"])
def test_archive_resource_and_member_checks(variant):
    result = BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as book:
        if variant == "duplicates":
            book.writestr("original", "one")
            with pytest.warns(UserWarning, match="Duplicate"):
                book.writestr("original", "two")
        elif variant == "symlink":
            info = zipfile.ZipInfo("original-link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            book.writestr(info, "original-target")
        elif variant == "member_count":
            for n in range(513):
                book.writestr(f"original-{n}", "x")
        else:
            book.writestr("original-ratio", "x" * 100000)
    with pytest.raises(ImportFailure) as exc:
        read_epub(result.getvalue())
    assert exc.value.code == "malformed_epub"
