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


def test_prepare_uses_pm2_json_and_preserves_relative_bot_state(monkeypatch,tmp_path):
    from scripts import stage1_admin
    old=tmp_path/"old";new=tmp_path/"new";state=tmp_path/"state"
    old.mkdir();new.mkdir();db=old/"data.db";db.touch()
    apps=[{"name":name,"cwd":str(old),"script":"/usr/bin/bash","args":["-lc","original"],"interpreter":"none"}
          for name in ("swiftchart-api","swiftchart-bot")]
    monkeypatch.setenv("BOT_STATE_PATH","saved/subscriptions.json")
    monkeypatch.setattr(stage1_admin,"verify_release",lambda _: {"release_commit":"new-commit"})
    monkeypatch.setattr(stage1_admin,"verify_baseline",lambda *_: "old-commit")
    monkeypatch.setattr(stage1_admin,"processes",lambda _: apps)
    monkeypatch.setattr(stage1_admin,"command",lambda *_,**__:str(db))
    validated=[]
    monkeypatch.setattr(stage1_admin,"validate_pm2_config",lambda path: validated.append(path))
    stage1_admin.prepare(old,new,state)
    previous=json.loads((state/"previous-processes.json").read_text())
    release=json.loads((state/"release-processes.json").read_text())
    assert all(p["cwd"]==str(old) and p["args"]==["-lc","original"] for p in previous["apps"])
    assert all(p["cwd"]==str(new) for p in release["apps"])
    assert all("export BOT_STATE_PATH="+str(old/"saved/subscriptions.json") in p["args"][1] for p in release["apps"])
    assert validated==[state/"previous-processes.json",state/"release-processes.json"]


def test_process_switch_recreates_only_api_bot_and_checks_actual_cwd(monkeypatch,tmp_path):
    from scripts import stage1_admin
    root=tmp_path/"release";root.mkdir();calls=[]
    monkeypatch.setattr(stage1_admin,"validate_pm2_config",lambda _:None)
    monkeypatch.setattr(stage1_admin.subprocess,"run",lambda args,**kwargs:calls.append(args))
    processes=[{"name":name,"pid":123,"pm2_env":{"pm_cwd":str(root)}} for name in ("swiftchart-api","swiftchart-bot")]
    monkeypatch.setattr(stage1_admin,"command",lambda _:json.dumps(processes))
    resolve=Path.resolve
    monkeypatch.setattr(Path,"resolve",lambda p,*a,**kw:root if str(p)=="/proc/123/cwd" else resolve(p,*a,**kw))
    config=tmp_path/"processes.json"
    stage1_admin.switch_processes(config,root)
    assert calls==[["pm2","delete","swiftchart-api","swiftchart-bot"],
        ["pm2","start",str(config),"--only","swiftchart-api,swiftchart-bot"]]
