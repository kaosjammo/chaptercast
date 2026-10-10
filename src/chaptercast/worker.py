"""Server-owned single worker, persistent checkpoints and atomic publication.

The local process lock is held for the full worker lifetime, including synthesis.
There are no expiring leases: a paused live process cannot lose ownership to a
second process. Process death releases the lock before recovery can begin.
"""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import wave
from typing import Callable, Protocol
from uuid import uuid4

from chaptercast.audio_validation import validate_chunk, validate_output
from chaptercast.database import connect
from chaptercast.jobs import GenerationSettings, JobStore


class Backend(Protocol):
    """Issue #4 adapter: CPU-first, bounded writes and interruptible operations.

    Write mono PCM16 WAV to destination; encode every ordered chunk to every
    requested destination directly from ordered WAV checkpoints or a bounded
    stream. Do not create a second full-size concatenated PCM copy on disk.
    max_bytes bounds peak additional disk usage across destinations and all
    temporary work combined; existing checkpoints are already accounted for
    when the worker calculates the remaining encoding budget. Call check
    frequently, honour max_bytes while writing, bound model/subprocess calls,
    and never publish files or mutate queue state.
    The pipeline version must change whenever the model/rendering contract changes.
    """

    def synthesize(self, text: str, settings: GenerationSettings, destination: Path,
                   check: Callable[[], None], *, max_bytes: int) -> None: ...

    def encode(self, chunks: list[Path], settings: GenerationSettings,
               destinations: dict[str, Path], check: Callable[[], None], *, max_bytes: int) -> None: ...


class Interrupted(Exception):
    pass


class Worker:
    def __init__(self, store: JobStore, backend: Backend | None):
        self.store, self.backend = store, backend
        self.owner = uuid4().hex
        self._lock = None
        self._thread = None
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self.last_error = None

    @property
    def owns_lock(self) -> bool:
        return self._lock is not None

    def _acquire(self) -> bool:
        lock = (self.store.root / "worker.lock").open("a+b")
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        self._lock = lock
        return True

    def _release(self):
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.close()

    def start(self) -> bool:
        with self._guard:
            if self.owns_lock or not self._acquire():
                return False
            try:
                self._recover()
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="chaptercast-worker", daemon=True)
                self._thread.start()
            except BaseException:
                self._release()
                raise
            return True

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        # A slow adapter retains the lock until its thread actually exits.

    def _loop(self):
        try:
            while not self._stop.is_set():
                if not self._run_once():
                    self._stop.wait(0.1)
        except Exception:
            self.last_error = "worker_failed"
        finally:
            self._release()

    def run_once(self) -> bool:
        """Bounded synchronous foreground helper, also protected by the process lock."""
        with self._guard:
            if self.owns_lock or not self._acquire():
                return False
            try:
                self._stop.clear()
                self._recover()
                return self._run_once()
            finally:
                self._release()

    @contextmanager
    def _db(self):
        with connect(self.store.database) as db:
            db.row_factory = sqlite3.Row
            yield db

    def _work(self, identifier: int) -> Path:
        return self.store.root / "audio" / ".work" / str(identifier)

    def _ready(self, identifier: int) -> Path:
        return self.store.root / "audio" / "ready" / str(identifier)

    def _bytes(self, identifier: int) -> int:
        return sum(p.stat().st_size for root in (self._work(identifier), self._ready(identifier))
                   if root.exists() for p in root.rglob("*") if p.is_file())

    def _recover(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET status = 'queued', owner = NULL WHERE status = 'running'")
            jobs = list(db.execute("SELECT id, status FROM jobs"))
            for job in jobs:
                identifier = job["id"]
                work = self._work(identifier)
                if job["status"] == "completed":
                    # Legacy outputs have no validation manifest and are retained
                    # without being served. New cached outputs must stay intact.
                    if db.execute("SELECT cache_key FROM jobs WHERE id=?", (identifier,)).fetchone()[0] is not None and not self.store.completed_valid(db, identifier):
                        db.execute("UPDATE jobs SET status='failed', error='output_integrity_failed' WHERE id=?", (identifier,))
                    shutil.rmtree(work, ignore_errors=True)
                else:
                    # A crash after directory rename but before SQLite commit leaves
                    # an invisible orphan bundle. Rebuild it from completed chunks.
                    shutil.rmtree(self._ready(identifier), ignore_errors=True)
                    shutil.rmtree(work / "bundle", ignore_errors=True)
                    if work.exists():
                        for part in work.glob("*.part"):
                            part.unlink()
                    for chunk in db.execute("SELECT * FROM job_chunks WHERE job_id = ? AND status = 'completed'", (identifier,)).fetchall():
                        try:
                            path = self.store.private_path(chunk["path"], "audio/.work")
                            if path != (work / f"{chunk['position']:04d}.wav").resolve():
                                raise ValueError("incorrect checkpoint path")
                            info = validate_chunk(path, self.store.limits.chunk_bytes)
                            if info.sha256 != chunk["sha256"] or info.byte_size != chunk["byte_size"]:
                                raise ValueError("checkpoint integrity")
                        except (OSError, ValueError, EOFError, TypeError, wave.Error):
                            db.execute("""UPDATE job_chunks SET status = 'queued', path = NULL, sha256 = NULL,
                                byte_size = NULL, duration_seconds = NULL WHERE job_id = ? AND position = ?""",
                                (identifier, chunk["position"]))
                    # Keep only validated checkpoints, including after a crash
                    # inside an adapter's temporary encoding workspace.
                    keep = {f"{chunk['position']:04d}.wav" for chunk in db.execute(
                        "SELECT position FROM job_chunks WHERE job_id = ? AND status = 'completed'", (identifier,))}
                    if work.exists():
                        for artifact in work.iterdir():
                            if artifact.name not in keep:
                                if artifact.is_dir() and not artifact.is_symlink():
                                    shutil.rmtree(artifact)
                                else:
                                    artifact.unlink()
                    db.execute("""UPDATE jobs SET completed_chunks =
                        (SELECT count(*) FROM job_chunks WHERE job_id = jobs.id AND status = 'completed')
                        WHERE id = ?""", (identifier,))
                db.execute("UPDATE jobs SET owner = NULL, artifact_bytes = ? WHERE id = ?", (self._bytes(identifier), identifier))

    def _check(self, identifier: int, deadline: float):
        if self._stop.is_set():
            raise Interrupted()
        if time.monotonic() > deadline:
            raise TimeoutError("job deadline")
        with self._db() as db:
            row = db.execute("SELECT status, owner FROM jobs WHERE id = ?", (identifier,)).fetchone()
        if row is None or row["status"] != "running" or row["owner"] != self.owner:
            raise Interrupted()

    def _run_once(self) -> bool:
        if self.backend is None:
            return False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if job is None:
                return False
            identifier = job["id"]
            db.execute("""UPDATE jobs SET status = 'running', owner = ?,
                started_at = coalesce(started_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id = ?""", (self.owner, identifier))
        deadline = time.monotonic() + self.store.limits.job_seconds
        check = lambda: self._check(identifier, deadline)
        work = self._work(identifier)
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            settings = GenerationSettings.restore(job["settings_json"])
            with self._db() as db:
                chunks = list(db.execute("SELECT * FROM job_chunks WHERE job_id = ? ORDER BY position", (identifier,)))
            if not chunks or len(chunks) != job["total_chunks"]:
                raise ValueError("missing chunk snapshot")
            paths, duration = [], 0.0
            for chunk in chunks:
                check()
                destination = work / f"{chunk['position']:04d}.wav"
                if chunk["status"] == "completed":
                    info = validate_chunk(destination, self.store.limits.chunk_bytes)
                    if info.sha256 != chunk["sha256"]:
                        raise ValueError("checkpoint changed during execution")
                else:
                    part = work / f"{chunk['position']:04d}.part"
                    budget = min(self.store.limits.chunk_bytes, self.store.limits.job_bytes - self._bytes(identifier))
                    self.backend.synthesize(chunk["text"], settings, part, check, max_bytes=budget)
                    check()
                    info = validate_chunk(part, budget)
                    durable_file(part)
                    os.replace(part, destination)
                    durable_directory(work)
                    with self._db() as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = db.execute("SELECT status, owner FROM jobs WHERE id = ?", (identifier,)).fetchone()
                        if row["status"] != "running" or row["owner"] != self.owner:
                            raise Interrupted()
                        db.execute("""UPDATE job_chunks SET status = 'completed', path = ?, sha256 = ?,
                            byte_size = ?, duration_seconds = ? WHERE job_id = ? AND position = ?""",
                            (destination.relative_to(self.store.root).as_posix(), info.sha256, info.byte_size,
                             info.duration_seconds, identifier, chunk["position"]))
                        db.execute("UPDATE jobs SET completed_chunks = completed_chunks + 1, artifact_bytes = ? WHERE id = ?", (self._bytes(identifier), identifier))
                paths.append(destination)
                duration += info.duration_seconds
            check()
            if duration > 3600:
                raise ValueError("audio duration quota")
            bundle = work / "bundle"
            bundle.mkdir(mode=0o700, exist_ok=True)
            destinations = {format: bundle / f"chapter.{format}" for format in settings.formats}
            budget = self.store.limits.job_bytes - self._bytes(identifier)
            self.backend.encode(paths, settings, destinations, check, max_bytes=budget)
            check()
            if self._bytes(identifier) > self.store.limits.job_bytes or set(bundle.iterdir()) != set(destinations.values()):
                raise ValueError("artifact quota or unexpected output")
            outputs = {}
            for format, path in destinations.items():
                check()
                outputs[format] = validate_output(path, format, budget, duration)
                durable_file(path)
            durable_directory(bundle)
            ready = self._ready(identifier)
            ready.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            check()
            # Cancellation and publication compete in one SQLite write transaction.
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status, owner FROM jobs WHERE id = ?", (identifier,)).fetchone()
                if row["status"] != "running" or row["owner"] != self.owner:
                    raise Interrupted()
                os.replace(bundle, ready)
                durable_directory(ready.parent)
                for format, info in outputs.items():
                    path = (ready / destinations[format].name).relative_to(self.store.root).as_posix()
                    db.execute("""INSERT INTO audio_outputs (job_id, format, path, duration_seconds, byte_size, sha256)
                        VALUES (?, ?, ?, ?, ?, ?)""", (identifier, format, path, info.duration_seconds, info.byte_size, info.sha256))
                db.execute("""UPDATE jobs SET status = 'completed', owner = NULL, artifact_bytes = ?,
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?""", (self._bytes(identifier), identifier))
            shutil.rmtree(work, ignore_errors=True)
            with self._db() as db:
                db.execute("UPDATE jobs SET artifact_bytes = ? WHERE id = ?", (self._bytes(identifier), identifier))
        except Interrupted:
            self._finish(identifier, "queued", None)
        except Exception:
            self._finish(identifier, "failed", "generation_failed")
        return True

    def _finish(self, identifier: int, status: str, error: str | None):
        with self._db() as db:
            if db.execute("SELECT status FROM jobs WHERE id = ?", (identifier,)).fetchone()["status"] == "completed":
                return
        shutil.rmtree(self._work(identifier) / "bundle", ignore_errors=True)
        shutil.rmtree(self._ready(identifier), ignore_errors=True)
        for part in self._work(identifier).glob("*.part"):
            part.unlink(missing_ok=True)
        with self._db() as db:
            # Preserve an API cancellation made while an adapter was running.
            db.execute("""UPDATE jobs SET status = ?, error = ?, owner = NULL, artifact_bytes = ?,
                finished_at = CASE WHEN ? = 'failed' THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END
                WHERE id = ? AND status = 'running' AND owner = ?""",
                (status, error, self._bytes(identifier), status, identifier, self.owner))
            db.execute("UPDATE jobs SET owner = NULL, artifact_bytes = ? WHERE id = ? AND status = 'cancelled' AND owner = ?",
                       (self._bytes(identifier), identifier, self.owner))


def durable_file(path: Path):
    path.chmod(0o600)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def durable_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
