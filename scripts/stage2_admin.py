"""Prepare/operate a Stage 2 backend release only after explicit deploy approval."""
from __future__ import annotations

import argparse
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.stage1_admin import NAMES,backup_db,command,config,processes,restore_db,validate_pm2_config,verify_release

PREVIOUS_COMMIT="1562fa2162e1c6619fcebed1425cf712f208ad9e"


def verify_process_paths(expected_root):
    rows=[row for row in json.loads(command(["pm2","jlist"])) if row.get("name") in NAMES]
    if len(rows)!=2 or {row["name"] for row in rows}!=NAMES:
        raise ValueError("Expected exactly the API and bot")
    for row in rows:
        env=row["pm2_env"]
        actual=Path("/proc")/str(row["pid"])/"cwd"
        if env["status"]!="online" or Path(env["pm_cwd"]).resolve()!=expected_root.resolve() or actual.resolve()!=expected_root.resolve():
            raise ValueError("API/bot is not running the expected source directory")
    return rows


def switch_processes(config_path,expected_root):
    validate_pm2_config(config_path)
    existing={row["name"] for row in json.loads(command(["pm2","jlist"])) if row.get("name") in NAMES}
    if existing:
        subprocess.run(["pm2","delete",*sorted(existing)],check=True)
    subprocess.run(["pm2","start",str(config_path),"--only",",".join(sorted(NAMES))],check=True)
    verify_process_paths(expected_root)


def stop_release_processes():
    existing = sorted({row["name"] for row in json.loads(command(["pm2","jlist"])) if row.get("name") in NAMES})
    if existing:
        subprocess.run(["pm2","stop",*existing],check=True)


def tuning_defaults():
    sys.path.insert(0,str(ROOT/"backend"))
    from app.config import Settings
    names={"MIN_PERP_VOLUME_24H":"min_perp_volume_24h",
           "CRYPTO_MAX_SIGNALS_PER_SCAN":"crypto_max_signals_per_scan",
           "CRYPTO_MAX_SIGNALS_PER_DIRECTION":"crypto_max_signals_per_direction",
           "CRYPTO_TELEGRAM_DISPATCH_INTERVAL_SECONDS":"crypto_telegram_dispatch_interval_seconds"}
    return {key:Settings.model_fields[field].default for key,field in names.items()}


def exported_value(shell_command,name):
    lexer=shlex.shlex(shell_command,posix=True,punctuation_chars=";")
    lexer.whitespace_split=True
    values=[token.split("=",1)[1] for token in lexer if token.startswith(name+"=")]
    if len(values)!=1:raise ValueError("Missing or ambiguous saved process export: "+name)
    return values[0]


def prepare(root,previous_root,state,previous_state,expected_commit,expected_previous_commit=PREVIOUS_COMMIT):
    verify_release(root,expected_commit)
    verify_release(previous_root,expected_previous_commit)
    verify_process_paths(previous_root)
    apps=processes(previous_root)
    receipt=json.loads((previous_state/"state.json").read_text())
    if receipt["release_commit"]!=expected_previous_commit or Path(receipt["release_root"]).resolve()!=previous_root.resolve():
        raise ValueError("Previous state does not match the deployed source")
    db=Path(receipt["database_path"])
    if not db.is_absolute() or not db.is_file():raise ValueError("Expected live database missing")
    for app in apps:
        if app["script"]!="/usr/bin/bash" or len(app["args"])!=2 or app["args"][0]!="-lc":
            raise ValueError("Unexpected previous process command")
        if exported_value(app["args"][1],"DATABASE_URL")!="sqlite:///"+str(db):
            raise ValueError("Live database path drift")
        if not Path(exported_value(app["args"][1],"BOT_STATE_PATH")).is_absolute():
            raise ValueError("Bot state must retain its existing absolute path")
    if state.exists():raise ValueError("Release state already exists; refusing overwrite")
    state.mkdir(parents=True,mode=0o700);os.umask(0o077)
    tuning=tuning_defaults()
    if not all(isinstance(value,(int,float)) for value in tuning.values()):raise ValueError("Expected numeric tuning defaults")
    data={"release_commit":expected_commit,"release_root":str(root),
          "previous_commit":expected_previous_commit,"previous_source_root":str(previous_root),
          "database_path":str(db),"prepared_at_utc":datetime.now(UTC).isoformat(),"tuning":tuning}
    (state/"state.json").write_text(json.dumps(data,indent=2)+"\n")
    config(state/"previous-processes.json",apps)
    for app in apps:
        shell=app["args"][1];position=shell.rfind("exec ")
        if position<0:raise ValueError("Saved process lacks exec")
        overrides="".join("export "+key+"="+shlex.quote(str(value))+"; " for key,value in tuning.items())
        app["args"]=["-lc",shell[:position]+overrides+shell[position:]]
        app["cwd"]=str(root)
    config(state/"release-processes.json",apps)
    validate_pm2_config(state/"previous-processes.json")
    validate_pm2_config(state/"release-processes.json")
    print(json.dumps({"prepared":True,"previous_commit":expected_previous_commit,"release_commit":expected_commit,"tuning":tuning}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["prepare","backup","schema","activate","verify","rollback"])
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--previous-root",type=Path,default=Path("/opt/swiftchart-releases")/PREVIOUS_COMMIT)
    parser.add_argument("--previous-state",type=Path,default=Path("/opt/swiftchart-stage1-state")/PREVIOUS_COMMIT)
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--dependency-root",type=Path,default=Path("/opt/swiftchart"))
    args=parser.parse_args()
    if args.action=="prepare":
        if not args.expected_commit:raise ValueError("Expected commit required")
        prepare(args.root,args.previous_root,args.state,args.previous_state,args.expected_commit)
        return
    receipt=json.loads((args.state/"state.json").read_text());db=Path(receipt["database_path"])
    if args.action=="backup":backup_db(db,args.state/"before.db")
    elif args.action=="schema":
        verify_release(args.root,receipt["release_commit"])
        env={**os.environ,"DATABASE_URL":"sqlite:///"+str(db),"PYTHONPATH":str(args.root/"backend"),"PYTHONDONTWRITEBYTECODE":"1"}
        code="from app.utils.database import init_db;from app.services.telegram_dispatch import migrate_permanent_telegram_failures;import json;init_db();print(json.dumps(migrate_permanent_telegram_failures()))"
        subprocess.run([str(args.dependency_root/"backend/.venv/bin/python"),"-c",code],cwd=args.root,env=env,check=True)
    elif args.action=="activate":
        verify_release(args.root,receipt["release_commit"])
        switch_processes(args.state/"release-processes.json",args.root)
    elif args.action=="verify":
        verify_release(args.root,receipt["release_commit"]);verify_process_paths(args.root)
    elif args.action=="rollback":
        previous=Path(receipt["previous_source_root"])
        verify_release(previous,receipt["previous_commit"])
        if (args.state/"before-rollback.db").exists():raise ValueError("Rollback archive already exists")
        stop_release_processes()
        restore_db(args.state/"before.db",db,args.state/"before-rollback.db")
        switch_processes(args.state/"previous-processes.json",previous)
    print(json.dumps({"action":args.action,"completed":True}))


if __name__=="__main__":main()
