"""Transactional queue operations. Stored content and paths stay private."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from chaptercast.chunks import split_chunks
from chaptercast.database import connect
from chaptercast.imports import MAX_TEXT_BYTES, text_chapter

PIPELINE_VERSION = "kokoro-0.9.4:82m-f3ff3571791e39611d31c381e3a41a3af07b4987:misaki-0.9.4:spacy-sm-3.8.0:pcm16-24k:opus32-cbr:aac48-fragmented:v2"
CHUNK_CHARACTERS = 1000
PREVIEW_TEXT = "This is an original ChapterCast British voice preview. The rain has stopped, and the garden gate is open. Take your time; the next chapter is waiting."


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class JobFailure(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Limits:
    max_characters: int = 100_000
    max_chunks: int = 256
    max_active: int = 4
    max_jobs: int = 100
    job_bytes: int = 256 * 1024 * 1024
    storage_bytes: int = 2 * 1024 * 1024 * 1024
    chunk_bytes: int = 8 * 1024 * 1024
    job_seconds: float = 1800


@dataclass(frozen=True)
class GenerationSettings:
    voice: str = "bf_emma"
    speed: float = 1.0
    formats: tuple[str, ...] = ("opus", "m4a")

    def __post_init__(self):
        if self.voice not in {"bf_emma", "bf_isabella", "bm_george", "bm_lewis"}:
            raise JobFailure("invalid_settings", "Select a supported British voice.")
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)) or not 0.5 <= self.speed <= 2 or not math.isfinite(self.speed):
            raise JobFailure("invalid_settings", "Speed must be between 0.5 and 2.")
        if not isinstance(self.formats, (tuple, list)) or not 1 <= len(self.formats) <= 2 or any(f not in ("opus", "m4a") for f in self.formats) or len(set(self.formats)) != len(self.formats):
            raise JobFailure("invalid_settings", "Formats must contain opus and/or m4a once each.")
        object.__setattr__(self, "speed", float(self.speed))
        object.__setattr__(self, "formats", tuple(sorted(self.formats)))

    def serialise(self) -> str:
        return json.dumps({**asdict(self), "pipeline": PIPELINE_VERSION, "chunk_characters": CHUNK_CHARACTERS}, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, raw: str):
        data = json.loads(raw)
        if data.pop("pipeline") != PIPELINE_VERSION or data.pop("chunk_characters") != CHUNK_CHARACTERS:
            raise JobFailure("unsupported_pipeline", "Job uses an unsupported pipeline version.")
        return cls(**data)


class JobStore:
    def __init__(self, root: Path, limits: Limits | None = None):
        self.root = Path(root).resolve()
        self.database = self.root / "metadata.sqlite3"
        self.limits = limits or Limits()

    def private_path(self, relative: str, directory: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / directory):
            raise JobFailure("invalid_path", "Private artifact path is invalid.")
        return path

    def preview(self, settings: GenerationSettings) -> dict:
        # A fixed original sample uses the same durable queue, quotas and cache.
        # Reuse one private chapter instead of accumulating imports per click.
        relative = "uploads/voice-preview.txt"
        with connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM chapters WHERE text_path=?", (relative,)).fetchone()
            path = self.private_path(relative, "uploads")
            with path.open("wb") as stream:
                path.chmod(0o600)
                stream.write(PREVIEW_TEXT.encode())
            identifier = row[0] if row else db.execute(
                "INSERT INTO chapters (title, text_path) VALUES (?, ?)",
                ("Original British voice preview", relative)).lastrowid
        return self.enqueue(identifier, settings)

    def enqueue(self, chapter_id: int, settings: GenerationSettings | None = None) -> dict:
        if type(chapter_id) is not int or chapter_id <= 0:
            raise JobFailure("invalid_input", "chapter_id must be a positive integer.")
        settings = settings or GenerationSettings()
        with connect(self.database) as db:
            db.row_factory = sqlite3.Row
            # Serialise deduplication and admission quotas across all API processes.
            db.execute("BEGIN IMMEDIATE")
            chapter = db.execute("SELECT text_path FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
            if chapter is None:
                raise JobFailure("missing_chapter", "Chapter does not exist.", 404)
            try:
                with self.private_path(chapter["text_path"], "uploads").open("rb") as stream:
                    raw = stream.read(MAX_TEXT_BYTES + 1)
                text = text_chapter(raw.decode("utf-8")).text
            except (OSError, UnicodeError) as exc:
                raise JobFailure("missing_text", "Chapter text is unavailable or invalid.") from exc
            if len(text) > self.limits.max_characters:
                raise JobFailure("too_large", "Chapter exceeds the generation text limit.", 413)
            chunks = split_chunks(text, CHUNK_CHARACTERS)
            if len(chunks) > self.limits.max_chunks:
                raise JobFailure("too_large", "Chapter exceeds the generation chunk limit.", 413)
            content_key = digest(text.encode())
            voice_key = digest(settings.voice.encode())
            settings_json = settings.serialise()
            settings_key = digest(settings_json.encode())
            cache_key = digest(f"{content_key}:{voice_key}:{settings_key}".encode())
            existing = db.execute("SELECT id, status FROM jobs WHERE cache_key = ? AND status IN ('queued','running','completed')", (cache_key,)).fetchone()
            if existing and existing["status"] == "completed" and not self.completed_valid(db, existing["id"]):
                db.execute("UPDATE jobs SET status='failed', error='output_integrity_failed' WHERE id=?", (existing["id"],))
                existing = None
            if existing:
                return self._get(db, existing["id"])
            count, active, reserved = db.execute("""SELECT count(*),
                coalesce(sum(status IN ('queued','running') OR owner IS NOT NULL), 0),
                coalesce(sum(CASE WHEN status IN ('queued','running') OR owner IS NOT NULL THEN ? ELSE artifact_bytes END), 0)
                FROM jobs""", (self.limits.job_bytes,)).fetchone()
            if count >= self.limits.max_jobs:
                raise JobFailure("quota", "The job history quota is full.", 429)
            if active >= self.limits.max_active:
                raise JobFailure("quota", "The active job quota is full.", 429)
            if reserved + self.limits.job_bytes > self.limits.storage_bytes:
                raise JobFailure("quota", "The generation storage quota is full.", 429)
            identifier = db.execute("""INSERT INTO jobs
                (chapter_id, content_key, voice_key, settings_key, cache_key, settings_json, total_chunks)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chapter_id, content_key, voice_key, settings_key, cache_key, settings_json, len(chunks))).lastrowid
            db.executemany("INSERT INTO job_chunks (job_id, position, text, content_key) VALUES (?, ?, ?, ?)",
                           [(identifier, i, chunk, digest(chunk.encode())) for i, chunk in enumerate(chunks)])
            return self._get(db, identifier)

    def _get(self, db, identifier: int) -> dict:
        job = db.execute("""SELECT id, chapter_id, status, content_key, voice_key, settings_key, cache_key,
            total_chunks, completed_chunks, error, created_at, started_at, finished_at
            FROM jobs WHERE id = ?""", (identifier,)).fetchone()
        if job is None:
            raise JobFailure("missing_job", "Job does not exist.", 404)
        result = dict(job)
        result["progress"] = job["completed_chunks"] / job["total_chunks"] if job["total_chunks"] else 0
        result["outputs"] = []
        if job["status"] == "completed":
            for output in db.execute("SELECT format, duration_seconds, byte_size FROM audio_outputs WHERE job_id = ? AND sha256 IS NOT NULL", (identifier,)):
                result["outputs"].append({**dict(output), "url": f"/api/jobs/{identifier}/outputs/{output['format']}"})
        return result

    def get(self, identifier: int) -> dict:
        with connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return self._get(db, identifier)

    def completed_valid(self, db, identifier: int) -> bool:
        """Reuse only the intact set that originally passed media validation."""
        try:
            raw = db.execute("SELECT settings_json FROM jobs WHERE id=?", (identifier,)).fetchone()[0]
            formats = GenerationSettings.restore(raw).formats
            rows = list(db.execute("SELECT format, path, byte_size, sha256 FROM audio_outputs WHERE job_id=?", (identifier,)))
            if sorted(row[0] for row in rows) != list(formats):
                return False
            for row in rows:
                path = self.private_path(row[1], "audio/ready")
                if not 0 < row[2] <= self.limits.job_bytes or path.stat().st_size != row[2] or file_digest(path) != row[3]:
                    return False
            return True
        except (OSError, ValueError, TypeError, KeyError, JobFailure):
            return False

    def cancel(self, identifier: int) -> dict:
        with connect(self.database) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE jobs SET status = 'cancelled', finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ? AND status IN ('queued','running')""", (identifier,))
            return self._get(db, identifier)

    def output(self, identifier: int, format: str) -> Path:
        with connect(self.database) as db:
            row = db.execute("""SELECT path, byte_size, sha256 FROM audio_outputs JOIN jobs ON jobs.id = job_id
                WHERE job_id = ? AND format = ? AND status = 'completed' AND sha256 IS NOT NULL""", (identifier, format)).fetchone()
        if row is None:
            raise JobFailure("missing_output", "Validated output is not available.", 404)
        path = self.private_path(row[0], "audio/ready")
        try:
            if path.stat().st_size != row[1] or file_digest(path) != row[2]:
                raise OSError("integrity")
        except OSError as exc:
            raise JobFailure("missing_output", "Validated output is not available.", 404) from exc
        return path


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
