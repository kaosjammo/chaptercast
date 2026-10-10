from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings


def test_health_and_private_runtime_initialisation(tmp_path):
    root = tmp_path / "runtime"
    app = create_app(Settings(data_dir=root))
    assert not root.exists()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert (root / "metadata.sqlite3").is_file()
        for name in ("uploads", "models", "audio"):
            assert (root / name).is_dir()


def test_static_shell_and_assets(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "ChapterCast" in response.text
        assert 'name="viewport"' in response.text
        assert "British voice preview" in response.text
        assert client.get("/static/style.css").headers["content-type"].startswith("text/css")
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/../metadata.sqlite3").status_code == 404
        assert client.get("/metadata.sqlite3").status_code == 404
