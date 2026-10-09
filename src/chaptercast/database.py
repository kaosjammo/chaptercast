"""SQLite metadata foundation. Queue execution is a later issue."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

SCHEMA_VERSION = 1
SCHEMA = """
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
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported SQLite schema version: {version}")
        if version == 0:
            # A transaction keeps the tables and their version marker together.
            db.executescript(
                "BEGIN;\n" + SCHEMA + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
            )
