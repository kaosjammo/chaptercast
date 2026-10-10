"""Synthetic PCM and real FFmpeg validation; no Kokoro/TTS claim is made."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect
from chaptercast.jobs import GenerationSettings, JobFailure, JobStore, Limits
from chaptercast.worker import Worker
from test_jobs import chapter, store


class SyntheticBackend:
    """Original synthetic tone fixture, never a substitute for real narration."""
    def __init__(self):
        self.texts = []

    def synthesize(self, text, settings, destination, check, *, max_bytes):
        check()
        self.texts.append(text)
        samples = b"".join(struct.pack("<h", int(1000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(4800))
        assert len(samples) + 44 <= max_bytes
        with wave.open(str(destination), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(samples)

    def encode(self, chunks, settings, destinations, check, *, max_bytes):
        with tempfile.TemporaryDirectory(dir=next(iter(destinations.values())).parent.parent) as temporary:
            combined = Path(temporary) / "original-tone.wav"
            with wave.open(str(combined), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                for chunk in chunks:
                    check()
                    with wave.open(str(chunk), "rb") as audio:
                        output.writeframes(audio.readframes(audio.getnframes()))
            for format, destination in destinations.items():
                check()
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(combined),
                                "-ac", "1", "-c:a", "libopus" if format == "opus" else "aac",
                                "-b:a", "32k", "-threads", "1", str(destination)], check=True, timeout=10)
            assert sum(p.stat().st_size for p in destinations.values()) <= max_bytes


@pytest.fixture
def ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("real FFmpeg/ffprobe required for synthetic output validation")


def child_env():
    return {**os.environ, "PYTHONPATH": str(Path(__file__).parent.resolve())}


def test_process_lock_excludes_another_process(store):
    worker = Worker(store, None)
    assert worker.start()
    try:
        result = subprocess.run([sys.executable, "-c", """
from pathlib import Path
import sys
from chaptercast.jobs import JobStore
from chaptercast.worker import Worker
worker = Worker(JobStore(Path(sys.argv[1])), None)
print(worker.start())
worker.stop()
""", str(store.root)], capture_output=True, text=True, timeout=10, check=True)
        assert result.stdout.strip() == "False"
    finally:
        worker.stop()


@pytest.mark.parametrize("crash_at", ["chunk", "publication"])
def test_real_process_crash_resumes_completed_chunks_once(store, ffmpeg, crash_at):
    text = "Original recovery fixture. " * 45
    job = store.enqueue(chapter(store, text))
    result = subprocess.run([sys.executable, "-c", """
from pathlib import Path
import os, sys
from chaptercast.jobs import JobStore
from chaptercast.worker import Worker
from test_worker import SyntheticBackend
crash_at = sys.argv[2]
if crash_at == 'publication':
    original_replace = os.replace
    def interrupted_replace(source, destination):
        original_replace(source, destination)
        if Path(destination).parent.name == 'ready':
            os._exit(24)
    os.replace = interrupted_replace
class CrashBackend(SyntheticBackend):
    def synthesize(self, *args, **kwargs):
        if self.texts and crash_at == 'chunk':
            os._exit(23)
        super().synthesize(*args, **kwargs)
Worker(JobStore(Path(sys.argv[1])), CrashBackend()).run_once()
""", str(store.root), crash_at], env=child_env(), capture_output=True, timeout=10)
    assert result.returncode == (23 if crash_at == "chunk" else 24), result.stderr.decode()
    interrupted = store.get(job["id"])
    assert interrupted["status"] == "running"
    assert interrupted["completed_chunks"] == (1 if crash_at == "chunk" else interrupted["total_chunks"])
    assert interrupted["outputs"] == []
    with connect(store.database) as db:
        texts = [r[0] for r in db.execute("SELECT text FROM job_chunks WHERE job_id=? ORDER BY position", (job["id"],))]
        first_hash = db.execute("SELECT sha256 FROM job_chunks WHERE job_id=? AND position=0", (job["id"],)).fetchone()[0]
    backend = SyntheticBackend()
    assert Worker(store, backend).run_once()
    complete = store.get(job["id"])
    assert complete["status"] == "completed"
    assert complete["completed_chunks"] == len(texts)
    assert backend.texts == (texts[1:] if crash_at == "chunk" else [])
    assert "".join(texts) == text.strip()
    with connect(store.database) as db:
        assert db.execute("SELECT sha256 FROM job_chunks WHERE position=0 AND job_id=?", (job["id"],)).fetchone()[0] == first_hash
    assert {o["format"] for o in complete["outputs"]} == {"opus", "m4a"}
    assert all(o["duration_seconds"] > 0 and o["byte_size"] > 0 for o in complete["outputs"])
    assert not Worker(store, backend).run_once()
    assert store.enqueue(job["chapter_id"])["id"] == job["id"]


def test_outputs_are_hidden_during_encoding_and_visible_together(store, ffmpeg):
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
        running = store.get(job["id"])
        assert running["status"] == "running" and running["progress"] == 1
        assert running["outputs"] == []
        with pytest.raises(JobFailure):
            store.output(job["id"], "opus")
        release.set()
        deadline = time.monotonic() + 5
        while store.get(job["id"])["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store.get(job["id"])["status"] == "completed"
        assert len(store.get(job["id"])["outputs"]) == 2
    finally:
        release.set()
        worker.stop()


@pytest.mark.parametrize("failure", ["bad_chunk", "bad_output", "missing_format", "missing_chunk", "wrong_duration", "extra_file", "exception"])
def test_invalid_artifacts_never_publish_and_error_does_not_leak_content(store, ffmpeg, failure):
    class InvalidBackend(SyntheticBackend):
        def synthesize(self, *args, **kwargs):
            if failure == "exception":
                raise RuntimeError("private story text and private paths must not escape")
            super().synthesize(*args, **kwargs)
            if failure == "bad_chunk":
                args[2].write_bytes(b"invalid PCM")

        def encode(self, chunks, settings, destinations, check, **kwargs):
            if failure == "wrong_duration":
                chunks = chunks + chunks * 5
            if failure == "missing_chunk":
                chunks = chunks[:-1]
            super().encode(chunks, settings, destinations, check, **kwargs)
            if failure == "bad_output":
                destinations["m4a"].write_bytes(b"invalid compressed audio")
            if failure == "missing_format":
                destinations["m4a"].unlink()
            if failure == "extra_file":
                (destinations["m4a"].parent / "unexpected.txt").write_text("Original synthetic fixture")

    job = store.enqueue(chapter(store, "Original missing chunk fixture. " * 45) if failure == "missing_chunk" else chapter(store))
    assert Worker(store, InvalidBackend()).run_once()
    failed = store.get(job["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "generation_failed"
    assert failed["outputs"] == []
    assert not (store.root / "audio/ready" / str(job["id"])).exists()
    with connect(store.database) as db:
        assert db.execute("SELECT count(*) FROM audio_outputs").fetchone()[0] == 0


def test_corrupt_checkpoint_and_uncommitted_bundle_are_recovered(store, ffmpeg):
    job = store.enqueue(chapter(store))
    work = store.root / "audio/.work" / str(job["id"])
    work.mkdir(parents=True)
    (work / "0000.wav").write_bytes(b"damaged checkpoint")
    (work / "0000.part").write_bytes(b"interrupted write")
    scratch = work / "interrupted-encoding"
    scratch.mkdir()
    (scratch / "partial.wav").write_bytes(b"interrupted adapter workspace")
    ready = store.root / "audio/ready" / str(job["id"])
    ready.mkdir(parents=True)
    (ready / "chapter.opus").write_bytes(b"uncommitted publication")
    with connect(store.database) as db:
        db.execute("UPDATE jobs SET status='running', owner='dead-process', completed_chunks=1 WHERE id=?", (job["id"],))
        db.execute("""UPDATE job_chunks SET status='completed', path=?, sha256='invalid', byte_size=18
                   WHERE job_id=?""", (f'audio/.work/{job["id"]}/0000.wav', job["id"]))
    backend = SyntheticBackend()
    assert Worker(store, backend).run_once()
    assert len(backend.texts) == 1
    assert not scratch.exists()
    assert store.get(job["id"])["status"] == "completed"
    assert store.output(job["id"], "opus").read_bytes().startswith(b"OggS")


def test_deadline_and_artifact_limits_fail_bounded_work(store):
    identifier = chapter(store)
    bounded = JobStore(store.root, replace(Limits(), job_seconds=0))
    job = bounded.enqueue(identifier)
    backend = SyntheticBackend()
    Worker(bounded, backend).run_once()
    assert store.get(job["id"])["status"] == "failed"
    assert backend.texts == []
    bounded = JobStore(store.root, replace(Limits(), chunk_bytes=10))
    job = bounded.enqueue(identifier)
    Worker(bounded, backend).run_once()
    assert store.get(job["id"])["status"] == "failed"


def test_worker_rejects_an_adapter_that_exceeds_the_chunk_budget(store):
    class OversizedBackend:
        def synthesize(self, text, settings, destination, check, *, max_bytes):
            destination.write_bytes(b"x" * (max_bytes + 1))

    bounded = JobStore(store.root, replace(Limits(), chunk_bytes=100))
    job = bounded.enqueue(chapter(store))
    Worker(bounded, OversizedBackend()).run_once()
    assert store.get(job["id"])["status"] == "failed"
    assert store.get(job["id"])["completed_chunks"] == 0
    assert not list((store.root / "audio/.work" / str(job["id"])).iterdir())


def test_api_output_requires_validated_integrity(tmp_path, ffmpeg):
    backend = SyntheticBackend()
    with TestClient(create_app(Settings(data_dir=tmp_path), backend=backend), base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/import/text", json={"text": "Original output integrity fixture."}).json()["id"]
        job = client.post("/api/jobs", json={"chapter_id": identifier}).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f'/api/jobs/{job["id"]}').json()
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        response = client.get(job["outputs"][0]["url"])
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        store = JobStore(tmp_path)
        store.output(job["id"], "opus").write_bytes(b"tampered output")
        assert client.get(f'/api/jobs/{job["id"]}/outputs/opus').status_code == 404
        replacement = client.post("/api/jobs", json={"chapter_id": identifier}).json()
        assert replacement["id"] != job["id"]
        assert client.get(f'/api/jobs/{job["id"]}').json()["error"] == "output_integrity_failed"


def test_cancellation_during_encoding_retains_reservation_until_worker_stops(store, ffmpeg):
    entered, release = threading.Event(), threading.Event()

    class PausingBackend(SyntheticBackend):
        def encode(self, *args, **kwargs):
            super().encode(*args, **kwargs)
            entered.set()
            assert release.wait(5)

    bounded = JobStore(store.root, replace(Limits(), max_active=1))
    identifier = chapter(store)
    job = bounded.enqueue(identifier)
    worker = Worker(bounded, PausingBackend())
    assert worker.start()
    try:
        assert entered.wait(5)
        bounded.cancel(job["id"])
        with pytest.raises(JobFailure, match="active job"):
            bounded.enqueue(identifier, GenerationSettings(speed=1.1))
    finally:
        release.set()
        worker.stop()
    assert store.get(job["id"])["status"] == "cancelled"
    assert store.get(job["id"])["outputs"] == []
    assert bounded.enqueue(identifier, GenerationSettings(speed=1.1))["status"] == "queued"


def test_graceful_shutdown_keeps_completed_checkpoints_for_restart(store, ffmpeg):
    entered, release = threading.Event(), threading.Event()

    class PausingBackend(SyntheticBackend):
        def synthesize(self, *args, **kwargs):
            if self.texts:
                entered.set()
                assert release.wait(5)
                args[3]()
            super().synthesize(*args, **kwargs)

    job = store.enqueue(chapter(store, "Original shutdown fixture. " * 45))
    worker = Worker(store, PausingBackend())
    worker.start()
    assert entered.wait(5)
    # stop() waits on the adapter; release it after the stop signal is delivered.
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(worker.stop)
        assert worker._stop.wait(5)
        release.set()
        stopped.result(timeout=5)
    assert store.get(job["id"])["status"] == "queued"
    assert store.get(job["id"])["completed_chunks"] == 1
    backend = SyntheticBackend()
    Worker(store, backend).run_once()
    assert len(backend.texts) == store.get(job["id"])["total_chunks"] - 1
    assert store.get(job["id"])["status"] == "completed"
