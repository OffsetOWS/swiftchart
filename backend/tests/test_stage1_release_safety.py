import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile

from scripts.stage1_admin import backup_db, restore_db, verify_release
from scripts import stage1_pack


def rows(path):
    with sqlite3.connect(path) as c:return c.execute("SELECT value FROM events ORDER BY value").fetchall()


def test_rollback_preserves_post_deployment_rows_before_restoring_backup(tmp_path):
    database=tmp_path/"live.db";backup=tmp_path/"before.db";archive=tmp_path/"after.db"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE events(value INTEGER)");c.execute("INSERT INTO events VALUES (1)")
    backup_db(database,backup)
    with sqlite3.connect(database) as c:c.execute("INSERT INTO events VALUES (2)")
    restore_db(backup,database,archive)
    assert rows(database)==[(1,)]
    assert rows(archive)==[(1,),(2,)]


def test_runtime_payload_excludes_frontend_okx_tests_and_dependency_installation(monkeypatch,tmp_path):
    bodies={"a":b"backend", "b":b"bot", "c":b"optional", "d":b"tests", "e":b"frontend", "f":b"dependencies"}
    entries=[("a","backend/app/main.py"),("b","bot/main.py"),
             ("c","backend/app/integrations/okx_asp/service.py"),("c","backend/app/routes/okx_asp.py"),
             ("d","backend/tests/test_okx_asp.py"),("e","frontend/src/App.jsx"),("f","backend/requirements.txt")]
    def output(args):
        command=args[3:]
        if command[0]=="rev-parse":return b"0"*40+b"\n"
        if command[0]=="ls-tree":return b"".join(f"100644 blob {oid}\t{name}\0".encode() for oid,name in entries)
        return bodies[command[-1]]
    monkeypatch.setattr(stage1_pack.subprocess,"check_output",output)
    file=tmp_path/"release.tar.gz"
    stage1_pack.pack(tmp_path,"HEAD",file)
    with tarfile.open(file) as archive:
        assert set(archive.getnames())=={"backend/app/main.py","bot/main.py","release-manifest.json"}


def test_release_hash_check_rejects_post_pack_source_changes(tmp_path):
    path=tmp_path/"backend/app/main.py";path.parent.mkdir(parents=True);path.write_text("original")
    manifest={"release_commit":"0"*40,"files":{"backend/app/main.py":hashlib.sha256(path.read_bytes()).hexdigest()}}
    (tmp_path/"release-manifest.json").write_text(json.dumps(manifest))
    verify_release(tmp_path)
    import pytest
    with pytest.raises(ValueError,match="approved commit"):verify_release(tmp_path,"1"*40)
    path.write_text("changed")
    with pytest.raises(ValueError,match="content mismatch"):verify_release(tmp_path)
