import sqlite3
import threading
from types import SimpleNamespace

from app.utils import database


def test_lifecycle_style_commit_survives_a_reader_longer_than_sqlite_default(tmp_path, monkeypatch):
    path = tmp_path / 'contention.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE probe(value INTEGER)')
        connection.execute('INSERT INTO probe VALUES (1)')
    monkeypatch.setattr(database, 'get_settings', lambda: SimpleNamespace(database_url='sqlite:///' + str(path)))
    monkeypatch.setattr(database, '_INITIALIZED', True)
    reader = sqlite3.connect(path)
    reader.execute('BEGIN')
    assert reader.execute('SELECT value FROM probe').fetchone() == (1,)
    updated = threading.Event()
    finished = threading.Event()
    errors = []
    def write():
        try:
            with database.get_connection() as connection:
                connection.execute('UPDATE probe SET value=2')
                updated.set()
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            finished.set()
    writer = threading.Thread(target=write)
    writer.start()
    try:
        assert updated.wait(2)
        # This exceeds the old five-second commit wait. A real shared read
        # transaction stays open; no busy handler or error is mocked.
        finished.wait(5.3)
        assert not finished.is_set(), errors
    finally:
        reader.rollback()
        reader.close()
        writer.join(timeout=5)
    assert not writer.is_alive() and not errors
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT value FROM probe').fetchone() == (2,)
