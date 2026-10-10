"""Disposable original fixtures for generation cleanup and worker exclusion."""
from concurrent.futures import ThreadPoolExecutor
import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect, initialise
from chaptercast.jobs import GenerationSettings, JobFailure, JobStore
from chaptercast.worker import Worker
from test_jobs import chapter, store
from test_worker import SyntheticBackend, ffmpeg


def artifact_dirs(store, identifier):
    paths = [store.root / f'audio/{area}/{identifier}' for area in ('.work', 'ready')]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        (path / 'original-fixture').write_bytes(b'Original deletion plumbing fixture.')
    return paths


@pytest.mark.parametrize('status', ['completed', 'cancelled', 'failed'])
def test_terminal_delete_preserves_text_other_generations_and_unique_identity(store, status):
    identifier = chapter(store)
    job = store.enqueue(identifier)
    other = store.enqueue(identifier, GenerationSettings(speed=1.1))
    paths = artifact_dirs(store, job['id'])
    retained = artifact_dirs(store, other['id'])
    models = store.root / 'models'
    models.mkdir()
    (models / 'original-model-marker').write_bytes(b'Original model marker')
    with connect(store.database) as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job['id']))
    assert store.get(job['id'])['deletable'] is True
    store.delete(job['id'])
    assert all(not path.exists() for path in paths)
    assert all((path / 'original-fixture').read_bytes() for path in retained)
    assert (models / 'original-model-marker').read_bytes() == b'Original model marker'
    assert store.library(0, 20)['chapters'][0]['id'] == identifier
    assert [j['id'] for j in store.library(0, 20)['chapters'][0]['jobs']] == [other['id']]
    assert (store.root / 'uploads/fixture.txt').read_text().startswith('Original')
    with connect(store.database) as db:
        assert db.execute('SELECT count(*) FROM job_chunks WHERE job_id=?', (job['id'],)).fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    with pytest.raises(JobFailure) as missing:
        store.delete(job['id'])
    assert missing.value.status == 404
    replacement = store.enqueue(identifier)
    assert replacement['id'] > other['id']
    store.cancel(replacement['id'])
    store.delete(replacement['id'])
    # Removing the highest ID must never let a stale player/delete URL hit a new job.
    assert store.enqueue(identifier)['id'] > replacement['id']


@pytest.mark.parametrize('status,owner', [('queued', None), ('running', 'worker'), ('cancelled', 'worker'), ('completed', 'worker'), ('failed', 'worker')])
def test_active_or_still_owned_jobs_cannot_be_deleted(store, status, owner):
    job = store.enqueue(chapter(store))
    paths = artifact_dirs(store, job['id'])
    with connect(store.database) as db:
        db.execute('UPDATE jobs SET status=?, owner=? WHERE id=?', (status, owner, job['id']))
    assert store.get(job['id'])['deletable'] is False
    with pytest.raises(JobFailure) as conflict:
        store.delete(job['id'])
    assert conflict.value.status == 409
    assert all((path / 'original-fixture').exists() for path in paths)
    assert store.get(job['id'])['status'] == status


@pytest.mark.parametrize('area', ['audio', 'audio/ready', 'audio/.work', 'audio/ready/1', 'audio/.work/1'])
def test_delete_rejects_symlinked_artifact_roots_without_following(store, tmp_path, area):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    victim = tmp_path / 'untouched'
    victim.mkdir()
    (victim / 'marker').write_bytes(b'Original retained data')
    path = store.root / area
    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(exist_ok=True, parents=True)
    path.symlink_to(victim, target_is_directory=True)
    with pytest.raises(JobFailure) as invalid:
        store.delete(job['id'])
    assert invalid.value.code == 'invalid_path'
    assert (victim / 'marker').read_bytes() == b'Original retained data'
    assert store.get(job['id'])['status'] == 'cancelled'


@pytest.mark.parametrize('reference', ['../uploads/fixture.txt', 'audio/ready/2/chapter.opus', 'audio/ready/1/../../../uploads/fixture.txt'])
def test_delete_preflights_all_database_paths_before_removing_anything(store, reference):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    paths = artifact_dirs(store, job['id'])
    with connect(store.database) as db:
        db.execute('INSERT INTO audio_outputs (job_id, format, path) VALUES (?, ?, ?)', (job['id'], 'opus', reference))
    with pytest.raises(JobFailure) as invalid:
        store.delete(job['id'])
    assert invalid.value.code == 'invalid_path'
    assert all((path / 'original-fixture').exists() for path in paths)


def test_cleanup_failure_is_explicit_and_retryable(store, monkeypatch):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    paths = artifact_dirs(store, job['id'])
    original = shutil.rmtree
    def denied(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError('Private path must never appear in API')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(shutil, 'rmtree', denied)
    with pytest.raises(JobFailure) as failure:
        store.delete(job['id'])
    assert failure.value.code == 'cleanup_failed'
    assert 'Private path' not in failure.value.message
    assert store.get(job['id'])['status'] == 'cancelled'
    monkeypatch.setattr(shutil, 'rmtree', original)
    store.delete(job['id'])
    assert all(not path.exists() for path in paths)


def test_delete_cross_import_cached_generation_preserves_all_chapters(store):
    first = chapter(store)
    second = chapter(store)
    job = store.enqueue(first)
    assert store.enqueue(second)['id'] == job['id']
    store.cancel(job['id'])
    assert store.get(job['id'])['linked_chapters'] == 2
    store.delete(job['id'])
    assert all(c['jobs'] == [] for c in store.library(0, 20)['chapters'])
    assert store.enqueue(second)['id'] > job['id']


def test_cancel_during_finalising_blocks_delete_until_worker_finishes(store, ffmpeg):
    entered, release = threading.Event(), threading.Event()
    class PausingBackend(SyntheticBackend):
        def encode(self, *args, **kwargs):
            super().encode(*args, **kwargs)
            entered.set()
            assert release.wait(5)
    job = store.enqueue(chapter(store))
    worker = Worker(store, PausingBackend())
    worker.start()
    try:
        assert entered.wait(5)
        finalising = store.get(job['id'])
        assert finalising['progress'] == 1 and finalising['outputs'] == []
        with pytest.raises(JobFailure) as active:
            store.delete(job['id'])
        assert active.value.status == 409
        store.cancel(job['id'])
        with pytest.raises(JobFailure) as stopping:
            store.delete(job['id'])
        assert stopping.value.status == 409
    finally:
        release.set()
        worker.stop()
    store.delete(job['id'])
    replacement = store.enqueue(job['chapter_id'])
    assert Worker(store, SyntheticBackend()).run_once()
    assert store.get(replacement['id'])['status'] == 'completed'
    assert store.output(replacement['id'], 'm4a').exists()


def test_concurrent_deletes_and_worker_recovery_are_serialised(store):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    artifact_dirs(store, job['id'])
    def cleanup(_):
        try:
            store.delete(job['id'])
            return 'deleted'
        except JobFailure as error:
            return error.code
    with ThreadPoolExecutor(max_workers=3) as pool:
        recovery = pool.submit(Worker(store, None).run_once)
        results = list(pool.map(cleanup, range(2)))
        assert recovery.result() is False
    assert sorted(results) == ['deleted', 'missing_job']


def test_delete_api_host_origin_missing_and_regeneration(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url='http://127.0.0.1') as client:
        chapter = client.post('/api/import/text', json={'text': 'Original deletion API fixture.'}).json()
        job = client.post('/api/jobs', json={'chapter_id': chapter['id']}).json()
        url = f"/api/jobs/{job['id']}"
        assert client.delete(url).status_code == 409
        client.post(url + '/cancel')
        assert client.delete(url, headers={'Origin': 'https://foreign.test'}).status_code == 403
        assert client.delete(url, headers={'Host': 'foreign.test'}).status_code == 400
        assert client.get(url).status_code == 200
        assert client.delete(url, headers={'Origin': 'http://127.0.0.1'}).status_code == 204
        assert client.delete(url).status_code == 404
        assert client.get(url + '/outputs/opus').status_code == 404
        replacement = client.post('/api/jobs', json={'chapter_id': chapter['id']}).json()
        assert replacement['id'] > job['id']
        assert client.get('/api/library').json()['chapters'][0]['id'] == chapter['id']


def test_nested_symlink_is_unlinked_without_deleting_target(store, tmp_path):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    paths = artifact_dirs(store, job['id'])
    victim = tmp_path / 'original-unrelated-directory'
    victim.mkdir()
    (victim / 'marker').write_text('Original retained fixture')
    (paths[0] / 'link').symlink_to(victim, target_is_directory=True)
    store.delete(job['id'])
    assert (victim / 'marker').read_text() == 'Original retained fixture'


def test_other_job_reference_into_bundle_prevents_cleanup(store):
    identifier = chapter(store)
    job = store.enqueue(identifier)
    other = store.enqueue(identifier, GenerationSettings(speed=1.1))
    store.cancel(job['id'])
    paths = artifact_dirs(store, job['id'])
    with connect(store.database) as db:
        db.execute('INSERT INTO audio_outputs (job_id, format, path) VALUES (?, ?, ?)',
                   (other['id'], 'opus', f"audio/ready/{job['id']}/original-fixture"))
    with pytest.raises(JobFailure) as invalid:
        store.delete(job['id'])
    assert invalid.value.code == 'invalid_path'
    assert all((p / 'original-fixture').exists() for p in paths)


def test_real_encoded_multiple_generations_remain_independent_after_delete(store, ffmpeg):
    identifier = chapter(store)
    first = store.enqueue(identifier)
    assert Worker(store, SyntheticBackend()).run_once()
    second = store.enqueue(identifier, GenerationSettings(voice='bm_george'))
    assert Worker(store, SyntheticBackend()).run_once()
    retained = {f: store.output(second['id'], f).read_bytes() for f in ('opus', 'm4a')}
    store.delete(first['id'])
    assert all(store.output(second['id'], f).read_bytes() == data for f, data in retained.items())
    assert store.get(second['id'])['status'] == 'completed'
    replacement = store.enqueue(identifier)
    assert replacement['id'] > second['id']
    assert Worker(store, SyntheticBackend()).run_once()
    assert store.get(replacement['id'])['status'] == 'completed'


def test_publication_retains_owner_until_post_commit_cleanup(store, ffmpeg, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = shutil.rmtree
    job = store.enqueue(chapter(store))
    def paused_cleanup(path, *args, **kwargs):
        if path == store.root / f"audio/.work/{job['id']}" and store.get(job['id'])['status'] == 'completed':
            entered.set()
            assert release.wait(5)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(shutil, 'rmtree', paused_cleanup)
    worker = Worker(store, SyntheticBackend())
    worker.start()
    try:
        assert entered.wait(5)
        assert store.get(job['id'])['deletable'] is False
        # Cleanup owns the SQLite write lock. A pending delete can only resume
        # after the last worker write, then it removes the complete generation.
        with ThreadPoolExecutor(max_workers=1) as pool:
            deletion = pool.submit(store.delete, job['id'])
            release.set()
            deletion.result(timeout=5)
    finally:
        release.set()
        worker.stop()
    assert worker.last_error is None
    with pytest.raises(JobFailure):
        store.get(job['id'])


def test_schema_three_existing_ids_seed_durable_sequence(store):
    job = store.enqueue(chapter(store))
    with connect(store.database) as db:
        db.execute('DROP TABLE generation_sequence')
        db.execute('PRAGMA user_version=3')
    initialise(store.database)
    store.cancel(job['id'])
    store.delete(job['id'])
    assert store.enqueue(job['chapter_id'])['id'] > job['id']


def test_circular_symlink_reference_returns_controlled_error_before_cleanup(store):
    job = store.enqueue(chapter(store))
    store.cancel(job['id'])
    paths = artifact_dirs(store, job['id'])
    (paths[1] / 'loop').symlink_to('loop', target_is_directory=True)
    with connect(store.database) as db:
        db.execute('INSERT INTO audio_outputs (job_id, format, path) VALUES (?, ?, ?)',
                   (job['id'], 'opus', f"audio/ready/{job['id']}/loop/chapter.opus"))
    with pytest.raises(JobFailure) as failure:
        store.delete(job['id'])
    assert failure.value.code == 'invalid_path'
    assert all((path / 'original-fixture').exists() for path in paths)


def test_migrated_legacy_leaf_output_cleanup_is_bounded_and_preserves_chapter(store):
    identifier = chapter(store)
    legacy = store.root / 'audio/legacy.opus'
    legacy.write_bytes(b'Original legacy output fixture')
    with connect(store.database) as db:
        job_id = db.execute("INSERT INTO jobs (chapter_id, status) VALUES (?, 'completed')", (identifier,)).lastrowid
        db.execute('INSERT INTO chapter_jobs (chapter_id,job_id) VALUES (?,?)', (identifier,job_id))
        db.execute("INSERT INTO audio_outputs (job_id,format,path) VALUES (?,'opus','audio/legacy.opus')", (job_id,))
    store.delete(job_id)
    assert not legacy.exists()
    assert store.library(0,20)['chapters'][0]['id'] == identifier
    assert (store.root / 'uploads/fixture.txt').exists()
    assert store.enqueue(identifier)['id'] > job_id
