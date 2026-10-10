"""Original boundary fixtures; no live network or TTS success is implied."""

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect
from chaptercast.imports import MAX_TEXT_BYTES, text_chapter, ImportFailure


@pytest.mark.parametrize("headers", [
    {"Host": "untrusted.example"},
    {"Origin": "https://untrusted.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:9999"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_hostile_browser_write_cannot_import_or_enqueue(tmp_path, headers):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        for route, payload in [
            ("/api/import/text", {"text": "Original rejected safety fixture."}),
            ("/api/jobs", {"chapter_id": 1}),
            ("/api/jobs/1/cancel", {}),
        ]:
            response = client.post(route, json=payload, headers=headers)
            assert response.status_code in (400, 403)
            assert "Original rejected" not in response.text
            assert response.headers["cache-control"] == "no-store"
        with connect(tmp_path / "metadata.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM chapters").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("host", ["untrusted.example", "localhost.untrusted.example", "127.0.0.1.untrusted.example"])
def test_untrusted_host_cannot_read_library(tmp_path, host):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        assert client.get("/api/library", headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("base_url", ["http://127.0.0.1", "http://localhost:8000", "http://[::1]:8000"])
def test_same_origin_browser_and_headerless_local_client_work(tmp_path, base_url):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url=base_url) as client:
        for headers in ({}, {"Origin": base_url, "Sec-Fetch-Site": "same-origin"}):
            response = client.post("/api/import/text", json={"text": "Original accepted safety fixture."}, headers=headers)
            assert response.status_code == 201
        assert client.get("/").headers["x-frame-options"] == "DENY"


def test_explicit_private_hostname_keeps_bind_loopback(tmp_path):
    settings = Settings.from_env({"CHAPTERCAST_DATA_DIR": str(tmp_path), "CHAPTERCAST_TRUSTED_HOSTS": "private.example"})
    assert settings.host == "127.0.0.1"
    with TestClient(create_app(settings), base_url="https://private.example") as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/import/text", json={"text": "Original proxy fixture."}, headers={"Origin": "https://private.example"}).status_code == 201
        assert client.get("/health", headers={"Host": "other.example"}).status_code == 400


@pytest.mark.parametrize("hosts", ["*", "*.example", "https://private.example", "private.example:8000", "private.example/path", "bad host", "-bad.example"])
def test_trusted_hosts_are_exact_names(hosts):
    with pytest.raises(ValueError, match="trusted"):
        Settings.from_env({"CHAPTERCAST_TRUSTED_HOSTS": hosts})


def test_text_limit_counts_utf8_bytes():
    # Original generated boundary fixture, never real chapter content.
    assert len(text_chapter("é" * (MAX_TEXT_BYTES // 2)).text.encode()) == MAX_TEXT_BYTES
    with pytest.raises(ImportFailure) as exc:
        text_chapter("é" * (MAX_TEXT_BYTES // 2) + "x")
    assert exc.value.code == "too_large"


def test_streamed_json_envelope_limit_leaves_no_private_artifacts(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        chunks = (b" " * 65536 for _ in range(33))
        response = client.post("/api/import/text", content=chunks)
        assert response.status_code == 413
        assert response.json()["code"] == "too_large"
        assert not list((tmp_path / "uploads").iterdir())
