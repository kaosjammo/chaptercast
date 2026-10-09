import sqlite3

import pytest

from chaptercast.database import connect, initialise


def test_schema_is_idempotent_and_preserves_metadata(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    initialise(path)
    with connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        assert tables == {"chapters", "jobs", "audio_outputs"}
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
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(RuntimeError, match="schema version"):
        initialise(path)
