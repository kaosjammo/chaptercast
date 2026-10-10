"""Versioned SQLite metadata and durable queue schema."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

SCHEMA_VERSION = 2
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    text_path TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS audio_outputs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    format TEXT NOT NULL CHECK (format IN ('opus', 'm4a')),
    path TEXT NOT NULL,
    duration_seconds REAL CHECK (duration_seconds >= 0),
    byte_size INTEGER CHECK (byte_size >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (job_id, format)
);
"""

MIGRATION_V2 = """
ALTER TABLE audio_outputs RENAME TO old_audio_outputs;
ALTER TABLE jobs RENAME TO old_jobs;
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    content_key TEXT,
    voice_key TEXT,
    settings_key TEXT,
    cache_key TEXT,
    settings_json TEXT,
    total_chunks INTEGER NOT NULL DEFAULT 0 CHECK (total_chunks >= 0),
    completed_chunks INTEGER NOT NULL DEFAULT 0
        CHECK (completed_chunks >= 0 AND completed_chunks <= total_chunks),
    artifact_bytes INTEGER NOT NULL DEFAULT 0 CHECK (artifact_bytes >= 0),
    owner TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT
);
INSERT INTO jobs (id, chapter_id, status, error, created_at, started_at, finished_at)
SELECT id, chapter_id,
    CASE WHEN status IN ('queued', 'running') THEN 'failed' ELSE status END,
    CASE WHEN status IN ('queued', 'running') THEN 'legacy_job_not_resumable' ELSE error END,
    created_at, started_at, finished_at FROM old_jobs;
CREATE UNIQUE INDEX jobs_cache ON jobs(cache_key)
    WHERE status IN ('queued', 'running', 'completed');
CREATE TABLE audio_outputs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    format TEXT NOT NULL CHECK (format IN ('opus', 'm4a')),
    path TEXT NOT NULL,
    duration_seconds REAL CHECK (duration_seconds >= 0),
    byte_size INTEGER CHECK (byte_size >= 0),
    sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (job_id, format)
);
INSERT INTO audio_outputs (id, job_id, format, path, duration_seconds, byte_size, created_at)
SELECT id, job_id, format, path, duration_seconds, byte_size, created_at FROM old_audio_outputs;
DROP TABLE old_audio_outputs;
DROP TABLE old_jobs;
CREATE TABLE job_chunks (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    position INTEGER NOT NULL CHECK (position >= 0),
    text TEXT NOT NULL,
    content_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'completed')),
    path TEXT,
    sha256 TEXT,
    byte_size INTEGER,
    duration_seconds REAL,
    PRIMARY KEY (job_id, position)
);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close the connection."""
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            yield db
    finally:
        db.close()


def initialise(path: Path) -> None:
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported SQLite schema version: {version}")
        if version < SCHEMA_VERSION:
            # A transaction keeps the tables and their version marker together.
            # execute() retains the admission lock; executescript() would first
            # commit it and allow another startup to race the schema migration.
            script = (SCHEMA_V1 if version == 0 else "") + MIGRATION_V2
            for statement in script.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
