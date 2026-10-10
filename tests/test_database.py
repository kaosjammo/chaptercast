import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from chaptercast.database import SCHEMA_VERSION, SCHEMA_V1, connect, initialise


def test_schema_is_idempotent_and_preserves_metadata(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    initialise(path)
    with connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        assert tables == {"chapters", "jobs", "audio_outputs", "job_chunks", "chapter_jobs"}
        db.execute(
            "INSERT INTO chapters (title, text_path) VALUES (?, ?)",
            ("Original test fixture", "uploads/original.txt"),
        )
    initialise(path)
    with connect(path) as db:
        assert db.execute("SELECT title FROM chapters").fetchone()[0] == "Original test fixture"


def test_schema_enforces_relationships_and_states(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    initialise(path)
    with connect(path) as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO jobs (chapter_id) VALUES (999)")
        db.execute("INSERT INTO chapters (title, text_path) VALUES ('Original fixture', 'uploads/test.txt')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO jobs (chapter_id, status) VALUES (1, 'unknown')")
        db.execute("INSERT INTO jobs (chapter_id) VALUES (1)")
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "queued"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO audio_outputs (job_id, format, path) VALUES (1, 'unknown', 'audio/test')")


def test_newer_database_version_is_not_overwritten(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="schema version"):
        initialise(path)


def test_version_one_migrates_without_losing_imports_or_legacy_outputs(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_V1 + "PRAGMA user_version = 1;")
        db.execute("INSERT INTO chapters (title, text_path) VALUES ('Original migration fixture', 'uploads/test.txt')")
        db.execute("INSERT INTO jobs (chapter_id, status) VALUES (1, 'running')")
        db.execute("INSERT INTO jobs (chapter_id, status) VALUES (1, 'completed')")
        db.execute("INSERT INTO audio_outputs (job_id, format, path) VALUES (2, 'opus', 'audio/legacy.opus')")
    initialise(path)
    with connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT status, error FROM jobs WHERE id=1").fetchone() == ("failed", "legacy_job_not_resumable")
        assert db.execute("SELECT path, sha256 FROM audio_outputs").fetchone() == ("audio/legacy.opus", None)
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=1")


def test_concurrent_startups_apply_schema_once(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: initialise(path), range(8)))
    with connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
