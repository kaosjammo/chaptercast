"""Original synthetic queue fixtures; these do not establish real TTS success."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.chunks import split_chunks
from chaptercast.config import Settings
from chaptercast.database import connect, initialise
from chaptercast.jobs import GenerationSettings, JobFailure, JobStore, Limits
from chaptercast.worker import Worker


@pytest.fixture
def store(tmp_path):
    for name in ("uploads", "audio"):
        (tmp_path / name).mkdir()
    initialise(tmp_path / "metadata.sqlite3")
    return JobStore(tmp_path)


def chapter(store, text="Original queue fixture. The lantern lights the path."):
    with connect(store.database) as db:
        identifier = db.execute("INSERT INTO chapters (title, text_path) VALUES (?, ?)",
                                ("Original synthetic fixture", "uploads/fixture.txt")).lastrowid
    (store.root / "uploads/fixture.txt").write_text(text)
    return identifier


def test_sentence_chunks_cover_every_character_and_bound_long_sentences():
    text = 'Dr. Reed waits. "Ready?"\n\nOriginal fixture: ' + "longword" * 20 + ". End."
    chunks = split_chunks(text, 50)
    assert "".join(chunks) == text
    assert all(0 < len(c) <= 50 for c in chunks)
    assert chunks[0].startswith('Dr. Reed waits. "Ready?"')
    assert split_chunks("First sentence. Second sentence.", 20) == ["First sentence. ", "Second sentence."]


def test_default_budget_fits_one_hour_checkpoints_and_both_compact_formats():
    limits = Limits()
    seconds = 3600
    pcm_bytes = seconds * 24_000 * 1 * 2  # Mono 24 kHz PCM16.
    encoded_bytes = seconds * (32_000 + 48_000) // 8  # Nominal Opus + AAC.
    wav_headers = limits.max_chunks * 44
    assert pcm_bytes == 172_800_000
    assert encoded_bytes == 36_000_000
    assert limits.job_bytes == 256 * 1024 * 1024
    assert pcm_bytes + wav_headers + encoded_bytes < limits.job_bytes
    # Encoding must read the checkpoints/stream, without duplicating all PCM.
    assert pcm_bytes * 2 + encoded_bytes > limits.job_bytes
    assert limits.max_active * limits.job_bytes == 1024 * 1024 * 1024
    assert limits.storage_bytes == 2 * 1024 * 1024 * 1024
    assert limits.max_active * limits.job_bytes <= limits.storage_bytes


def test_content_voice_and_settings_keys_and_concurrent_deduplication(store):
    identifier = chapter(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: store.enqueue(identifier), range(8)))
    assert len({j["id"] for j in jobs}) == 1
    first = jobs[0]
    assert first["status"] == "queued" and first["completed_chunks"] == 0
    assert first["total_chunks"] == 1
    voice = store.enqueue(identifier, GenerationSettings(voice="bm_george"))
    speed = store.enqueue(identifier, GenerationSettings(speed=1.2))
    assert len({j["cache_key"] for j in (first, voice, speed)}) == 3
    assert first["content_key"] == voice["content_key"] == speed["content_key"]
    assert first["voice_key"] != voice["voice_key"]
    assert first["settings_key"] != speed["settings_key"]
    # Persist a text snapshot; changes to the source upload cannot change an existing job.
    (store.root / "uploads/fixture.txt").write_text("Original replacement fixture.")
    assert store.enqueue(identifier)["cache_key"] != first["cache_key"]


def test_input_pending_history_and_storage_quotas(store):
    identifier = chapter(store)
    with pytest.raises(JobFailure, match="text limit"):
        JobStore(store.root, Limits(max_characters=5)).enqueue(identifier)
    with pytest.raises(JobFailure, match="storage quota"):
        JobStore(store.root, Limits(storage_bytes=1)).enqueue(identifier)
    long_identifier = chapter(store, "Original bounded chunk fixture. " * 50)
    with pytest.raises(JobFailure, match="chunk limit"):
        JobStore(store.root, Limits(max_chunks=1)).enqueue(long_identifier)
    bounded = JobStore(store.root, Limits(max_active=1, max_jobs=2))
    job = bounded.enqueue(identifier)
    with pytest.raises(JobFailure, match="active job"):
        bounded.enqueue(identifier, GenerationSettings(speed=1.1))
    bounded.cancel(job["id"])
    second = bounded.enqueue(identifier, GenerationSettings(speed=1.1))
    bounded.cancel(second["id"])
    with pytest.raises(JobFailure, match="job history"):
        bounded.enqueue(identifier, GenerationSettings(speed=1.2))


def test_cancelled_jobs_are_terminal_and_private_paths_are_not_returned(store):
    job = store.enqueue(chapter(store))
    assert store.cancel(job["id"])["status"] == "cancelled"
    assert store.cancel(job["id"])["status"] == "cancelled"
    assert store.get(job["id"])["outputs"] == []
    assert Worker(store, None).run_once() is False
    assert "text" not in store.get(job["id"])


def test_api_submission_survives_client_disconnect_and_server_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        identifier = client.post("/api/import/text", json={"text": "Original durable API fixture."}).json()["id"]
        response = client.post("/api/jobs", json={"chapter_id": identifier})
        assert response.status_code == 202
        job = response.json()
        assert client.get("/api/jobs/worker").json()["backend_ready"] is False
        assert client.get(f'/api/jobs/{job["id"]}/outputs/opus').status_code == 404
        assert client.post("/api/jobs", json={"chapter_id": True}).status_code == 400
        for invalid in ({"voice": "unsupported"}, {"voice": []}, {"speed": True}, {"speed": 10**500}, {"formats": ["opus", "opus"]}, {"formats": "opus"}, {"unexpected": True}):
            assert client.post("/api/jobs", json={"chapter_id": identifier, **invalid}).status_code == 400
        assert client.post("/api/jobs", content=b"x" * 8193).status_code == 413
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/jobs/{job["id"]}').json()["status"] == "queued"
        assert client.post(f'/api/jobs/{job["id"]}/cancel').json()["status"] == "cancelled"
        assert client.get("/api/jobs/99999").status_code == 404


def test_single_worker_lock_is_exclusive_and_released_after_shutdown(store):
    first, second = Worker(store, None), Worker(store, None)
    assert first.start() is True
    try:
        assert second.start() is False
    finally:
        first.stop()
    assert second.start() is True
    second.stop()


def test_running_cancel_cannot_checkpoint_or_publish(store):
    entered, release = threading.Event(), threading.Event()

    class BlockingBackend:
        def synthesize(self, text, settings, destination, check, *, max_bytes):
            entered.set()
            assert release.wait(5)
            check()

    job = store.enqueue(chapter(store))
    worker = Worker(store, BlockingBackend())
    assert worker.start()
    try:
        assert entered.wait(5)
        assert store.cancel(job["id"])["status"] == "cancelled"
        release.set()
    finally:
        release.set()
        worker.stop()
    assert store.get(job["id"])["completed_chunks"] == 0
    assert store.get(job["id"])["outputs"] == []
