"""Original library fixtures. Transport bytes do not establish playable audio."""

import json

from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect, initialise
from chaptercast.jobs import GenerationSettings, digest


def test_library_persists_imports_jobs_and_cross_import_cache_links(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get('/api/library').json()['chapters'] == []
        chapters = [client.post('/api/import/text', json={
            'title': title, 'text': 'Original library fixture. A little bell rings.'
        }).json() for title in ['<img src=x onerror=alert(1)>', 'Second original import']]
        job = client.post('/api/jobs', json={'chapter_id': chapters[0]['id']}).json()
        reused = client.post('/api/jobs', json={'chapter_id': chapters[1]['id']}).json()
        assert reused['id'] == job['id']
        library = client.get('/api/library').json()
        assert library['total'] == 2
        assert [c['id'] for c in library['chapters']] == [2, 1]
        assert all(c['jobs'][0]['id'] == job['id'] for c in library['chapters'])
        assert library['chapters'][1]['title'] == chapters[0]['title']
        assert library['chapters'][0]['jobs'][0]['voice'] == 'bf_emma'
        assert library['chapters'][0]['jobs'][0]['speed'] == 1
        assert 'text_path' not in json.dumps(library)
        assert 'Original library fixture.' not in json.dumps(library)
        assert client.get('/api/library?offset=1&limit=1').json()['chapters'][0]['id'] == 1
        assert client.get('/api/library?offset=-1').status_code == 422
        assert client.get('/api/library?limit=101').status_code == 422
        client.post(f"/api/jobs/{job['id']}/cancel")
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        library = client.get('/api/library').json()
        assert library['chapters'][0]['jobs'][0]['status'] == 'cancelled'
        assert client.get('/api/library').headers['cache-control'] == 'no-store'


def test_schema_two_migration_backfills_library_job_links(tmp_path):
    path = tmp_path / 'metadata.sqlite3'
    initialise(path)
    with connect(path) as db:
        db.execute('DROP TABLE chapter_jobs')
        db.execute('PRAGMA user_version = 2')
        db.execute("INSERT INTO chapters (title, text_path) VALUES ('Original migration fixture', 'uploads/test.txt')")
        db.execute("INSERT INTO jobs (chapter_id, status) VALUES (1, 'cancelled')")
    initialise(path)
    with connect(path) as db:
        assert db.execute('SELECT chapter_id, job_id FROM chapter_jobs').fetchall() == [(1, 1)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_preview_is_in_queue_history_but_not_imported_library(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        preview = app.state.jobs.preview(GenerationSettings())
        library = client.get('/api/library').json()
        assert library['chapters'] == [] and library['total'] == 0
        assert library['jobs'][0]['id'] == preview['id']
        assert library['jobs'][0]['preview'] is True
        assert library['worker']['backend_ready'] is False


def test_completed_output_range_and_safe_download_name(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        chapter = client.post('/api/import/text', json={
            'title': '../"<Original> / café', 'text': 'Original transport fixture.'
        }).json()
        job = client.post('/api/jobs', json={'chapter_id': chapter['id']}).json()
        path = tmp_path / 'audio/ready/fixture.opus'
        path.parent.mkdir(parents=True)
        raw = b'Original transport fixture bytes. ' * 8
        path.write_bytes(raw)
        with connect(tmp_path / 'metadata.sqlite3') as db:
            db.execute("UPDATE jobs SET status='completed', completed_chunks=total_chunks WHERE id=?", (job['id'],))
            db.execute('INSERT INTO audio_outputs (job_id, format, path, byte_size, duration_seconds, sha256) VALUES (?, ?, ?, ?, ?, ?)',
                       (job['id'], 'opus', 'audio/ready/fixture.opus', len(raw), 1, digest(raw)))
        url = f"/api/jobs/{job['id']}/outputs/opus"
        response = client.get(url, headers={'Range': 'bytes=10-19'})
        assert response.status_code == 206
        assert response.content == raw[10:20]
        assert response.headers['content-range'] == f'bytes 10-19/{len(raw)}'
        assert response.headers['accept-ranges'] == 'bytes'
        assert client.get(url, headers={'Range': f'bytes={len(raw)}-'}).status_code == 416
        disposition = client.get(url).headers['content-disposition']
        assert '\r' not in disposition and '\n' not in disposition
        assert '%2F' not in disposition and '%3C' not in disposition
        assert 'chapter-1-' in disposition
        path.write_bytes(b'damaged')
        assert client.get(url).status_code == 404
