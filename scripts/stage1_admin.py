"""Backend release administration. Execute on the VPS only after explicit approval.

No live environment contents are copied or serialized. Database restore keeps an
additional snapshot of writes made after deployment before restoring the backup.
"""
import argparse
from datetime import UTC,datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys

NAMES={"swiftchart-api","swiftchart-bot"}


def command(args,**kwargs):
    return subprocess.check_output(args,text=True,**kwargs)


def processes(old_root):
    rows=json.loads(command(["pm2","jlist"]))
    result=[]
    for row in rows:
        if row.get("name") not in NAMES:continue
        env=row["pm2_env"]
        if Path(env["pm_cwd"]).resolve()!=old_root.resolve():
            raise ValueError("API/bot source directory drift; stop")
        args=env.get("args",[])
        # Existing source statements reference the env file, not inline values.
        text=str(args).lower()
        if any(x in text for x in ("token=","password=","api_key=","secret=")):
            raise ValueError("Possible inline credential; stop without saving process arguments")
        result.append({"name":row["name"],"cwd":str(old_root),"script":env["pm_exec_path"],
                       "args":args,"interpreter":env.get("exec_interpreter","none"),
                       "instances":1,"autorestart":True})
    if {row["name"] for row in result}!=NAMES:raise ValueError("Expected API/bot processes missing")
    return result


def verify_release(root, expected_commit=None):
    data=json.loads((root/"release-manifest.json").read_text())
    if expected_commit and data["release_commit"]!=expected_commit:
        raise ValueError("Payload does not match the approved commit")
    for name,digest in data["files"].items():
        path=root/name
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Unsafe release path")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError("Release content mismatch: "+name)
    return data


def verify_baseline(old_root,new_root):
    baseline=json.loads((new_root/"docs/stage1_vps_baseline.json").read_text())
    env={**os.environ,"GIT_OPTIONAL_LOCKS":"0"}
    branch=command(["git","-C",str(old_root),"branch","--show-current"],env=env).strip()
    head=command(["git","-C",str(old_root),"rev-parse","HEAD"],env=env).strip()
    origin=command(["git","-C",str(old_root),"remote","get-url","origin"],env=env).strip()
    if branch!="main" or head!=baseline["vps_head"]:
        raise ValueError("VPS branch/HEAD changed; stop")
    if origin not in {"https://github.com/OffsetOWS/swiftchart.git","git@github.com:OffsetOWS/swiftchart.git"}:
        raise ValueError("Unexpected origin; stop")
    for name,digest in baseline["runtime_source_hashes"].items():
        path=old_root/name
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError("Preserved VPS source changed: "+name)
    return head


def backup_db(source,target):
    if not source.is_file():raise ValueError("Database missing")
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with sqlite3.connect(source.as_uri()+"?mode=ro",uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("Database backup integrity check failed")


def restore_db(source,target,archive):
    with sqlite3.connect(source.as_uri()+"?mode=ro",uri=True) as src:
        if src.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Invalid rollback backup")
        backup_db(target,archive)
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Restore integrity failed")


def config(path,apps):
    path.write_text(json.dumps({"apps":apps},indent=2)+"\n")
    path.chmod(0o600)


def validate_pm2_config(path):
    """Ask the installed PM2 parser to validate configuration before downtime."""
    binary=shutil.which("pm2")
    if not binary:raise ValueError("PM2 binary missing")
    common=Path(binary).resolve().parents[1]/"lib/Common.js"
    code="""const fs=require('fs'); const common=require(process.argv[1]);
const result=common.parseConfig(fs.readFileSync(process.argv[2],'utf8'),process.argv[2]);
if (!result || !Array.isArray(result.apps) || result.apps.length !== 2 ||
    result.apps.map(x=>x.name).sort().join(',') !== 'swiftchart-api,swiftchart-bot')
  throw new Error('PM2 rejected the API/bot configuration');"""
    command(["node","-e",code,str(common),str(path)])


def switch_processes(config_path, expected_root):
    """Recreate saved definitions: PM2 restart retains the old pm_cwd."""
    validate_pm2_config(config_path)
    subprocess.run(["pm2","delete",*sorted(NAMES)],check=True)
    subprocess.run(["pm2","start",str(config_path),"--only",",".join(sorted(NAMES))],check=True)
    rows=json.loads(command(["pm2","jlist"]))
    selected=[row for row in rows if row.get("name") in NAMES]
    if {row["name"] for row in selected}!=NAMES:raise ValueError("API/bot missing after switch")
    for row in selected:
        actual=Path("/proc")/str(row["pid"])/"cwd"
        if Path(row["pm2_env"]["pm_cwd"]).resolve()!=expected_root.resolve() or actual.resolve()!=expected_root.resolve():
            raise ValueError("Process did not switch to the requested source directory")


def prepare(old_root,new_root,state):
    release=verify_release(new_root)
    previous_head=verify_baseline(old_root,new_root)
    apps=processes(old_root)
    if state.exists():raise ValueError("Release state directory already exists; refusing overwrite")
    state.mkdir(parents=True,mode=0o700)
    os.umask(0o077)
    # Resolve relative SQLite settings from the unchanged old working directory.
    code="from app.utils.database import _sqlite_path;print(_sqlite_path().resolve())"
    env={**os.environ,"PYTHONPATH":str(old_root/"backend"),"PYTHONDONTWRITEBYTECODE":"1"}
    db=Path(command([str(old_root/"backend/.venv/bin/python"),"-c",code],cwd=old_root,env=env).strip())
    if not db.is_absolute() or not db.is_file():raise ValueError("Configured SQLite database missing")
    receipt={"previous_commit":previous_head,"previous_source_root":str(old_root),"database_path":str(db),
             "release_commit":release["release_commit"],"release_root":str(new_root),
             "prepared_at_utc":datetime.now(UTC).isoformat()}
    (state/"state.json").write_text(json.dumps(receipt,indent=2)+"\n")
    config(state/"previous-processes.json",apps)
    bot_state=Path(os.environ.get("BOT_STATE_PATH") or ".swiftchart_bot_state.json")
    if not bot_state.is_absolute():bot_state=(old_root/bot_state).resolve()
    for app in apps:
        name=app["name"]
        if name=="swiftchart-api":
            executable=shlex.quote(str(old_root/"backend/.venv/bin/uvicorn"))+" app.main:app --app-dir backend --host 127.0.0.1 --port 8000"
        else:executable=shlex.quote(str(old_root/"bot/.venv/bin/python"))+" -m bot.main"
        app["cwd"]=str(new_root)
        app["args"]=["-lc","set -a; source "+shlex.quote(str(old_root/".env"))+"; set +a; export DATABASE_URL="+shlex.quote("sqlite:///"+str(db))+"; export BOT_STATE_PATH="+shlex.quote(str(bot_state))+"; exec "+executable]
    config(state/"release-processes.json",apps)
    validate_pm2_config(state/"previous-processes.json")
    validate_pm2_config(state/"release-processes.json")
    print(json.dumps({"prepared":True,"previous_commit":previous_head,"release_commit":release["release_commit"]}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["verify","prepare","backup","schema","activate","rollback"])
    parser.add_argument("--old-root",type=Path,default=Path("/opt/swiftchart"))
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--expected-commit")
    args=parser.parse_args()
    if args.action=="verify":
        verify_release(args.root,args.expected_commit);verify_baseline(args.old_root,args.root);processes(args.old_root)
        print('{"preflight":"PASS"}');return
    if args.action=="prepare":prepare(args.old_root,args.root,args.state);return
    state=json.loads((args.state/"state.json").read_text())
    db=Path(state["database_path"])
    if args.action=="backup":
        backup_db(db,args.state/"before.db")
    elif args.action=="schema":
        env={**os.environ,"DATABASE_URL":"sqlite:///"+str(db),"PYTHONPATH":str(args.root/"backend"),"PYTHONDONTWRITEBYTECODE":"1"}
        code="from app.utils.database import init_db,get_connection;from app.services.tracking_ledger import ensure_tracking_ledger;init_db();\nwith get_connection() as c:ensure_tracking_ledger(c)"
        subprocess.run([str(args.old_root/"backend/.venv/bin/python"),"-c",code],cwd=args.root,env=env,check=True)
    elif args.action=="activate":
        verify_release(args.root,state["release_commit"])
        switch_processes(args.state/"release-processes.json",args.root)
    elif args.action=="rollback":
        # Confirm that the preserved previous source still represents this release's baseline.
        verify_baseline(args.old_root,args.root)
        if (args.state/"before-rollback.db").exists():raise ValueError("Rollback snapshot already exists; stop")
        subprocess.run(["pm2","stop",*sorted(NAMES)],check=True)
        restore_db(args.state/"before.db",db,args.state/"before-rollback.db")
        switch_processes(args.state/"previous-processes.json",Path(state["previous_source_root"]))
    print(json.dumps({"action":args.action,"completed":True}))


if __name__=="__main__":main()
