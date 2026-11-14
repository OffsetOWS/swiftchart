from copy import deepcopy
import json
from pathlib import Path
import shlex

import pytest

from scripts import stage2_admin as admin


def test_stage2_prepare_retains_previous_commands_and_data_without_dumping_environment(monkeypatch,tmp_path):
    previous=tmp_path/"previous";new=tmp_path/"new";oldstate=tmp_path/"oldstate";state=tmp_path/"state"
    previous.mkdir();new.mkdir();oldstate.mkdir();db=tmp_path/"live data.db";db.touch()
    old={"release_commit":admin.PREVIOUS_COMMIT,"release_root":str(previous),"database_path":str(db)}
    (oldstate/"state.json").write_text(json.dumps(old))
    shell="set -a; source /opt/swiftchart/.env; set +a; export DATABASE_URL="+shlex.quote("sqlite:///"+str(db))+"; export BOT_STATE_PATH=/opt/swiftchart/.swiftchart_bot_state.json; exec /opt/swiftchart/backend/.venv/bin/python -m app"
    apps=[{"name":name,"cwd":str(previous),"script":"/usr/bin/bash","args":["-lc",shell],"interpreter":"none"} for name in sorted(admin.NAMES)]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","DO_NOT_CAPTURE_RUNTIME_ENVIRONMENT")
    monkeypatch.setattr(admin,"verify_release",lambda *args:None)
    monkeypatch.setattr(admin,"verify_process_paths",lambda *args:None)
    monkeypatch.setattr(admin,"processes",lambda _:deepcopy(apps))
    monkeypatch.setattr(admin,"validate_pm2_config",lambda _:None)
    admin.prepare(new,previous,state,oldstate,"candidate")
    before=json.loads((state/"previous-processes.json").read_text())
    after=json.loads((state/"release-processes.json").read_text())
    assert before["apps"]==apps
    for app in after["apps"]:
        assert app["cwd"]==str(new)
        assert admin.exported_value(app["args"][1],"DATABASE_URL")=="sqlite:///"+str(db)
        assert admin.exported_value(app["args"][1],"BOT_STATE_PATH")=="/opt/swiftchart/.swiftchart_bot_state.json"
        assert "MIN_PERP_VOLUME_24H=250000" in app["args"][1]
        assert "CRYPTO_MAX_SIGNALS_PER_SCAN=3" in app["args"][1]
        assert "CRYPTO_MAX_SIGNALS_PER_DIRECTION=2" in app["args"][1]
    assert all("DO_NOT_CAPTURE_RUNTIME_ENVIRONMENT" not in p.read_text() for p in state.iterdir())
    receipt=json.loads((state/"state.json").read_text())
    assert receipt["previous_commit"]==admin.PREVIOUS_COMMIT
    assert receipt["database_path"]==str(db)


def test_deployed_environment_does_not_silently_override_the_reviewed_default(monkeypatch):
    monkeypatch.setenv("MIN_PERP_VOLUME_24H","100000")
    tuning=admin.tuning_defaults()
    assert tuning["MIN_PERP_VOLUME_24H"]==250000
    assert len(tuning)==4
    assert not any("FUNDING" in key or "FEE" in key for key in tuning)


@pytest.mark.parametrize("names",[[],["swiftchart-api"]])
def test_switch_can_recover_from_missing_or_partial_failed_activation(monkeypatch,tmp_path,names):
    calls=[];verified=[]
    monkeypatch.setattr(admin,"validate_pm2_config",lambda _:None)
    monkeypatch.setattr(admin,"command",lambda _:json.dumps([{"name":name} for name in names]+[{"name":"swiftchart-executor"}]))
    monkeypatch.setattr(admin.subprocess,"run",lambda args,**kwargs:calls.append(args))
    monkeypatch.setattr(admin,"verify_process_paths",lambda path:verified.append(path))
    config=tmp_path/"processes.json";root=tmp_path/"release"
    admin.switch_processes(config,root)
    if names:assert calls[0]==["pm2","delete",*names]
    else:assert len(calls)==1
    assert calls[-1]==["pm2","start",str(config),"--only","swiftchart-api,swiftchart-bot"]
    assert verified==[root]
    assert all("swiftchart-executor" not in call for call in calls)


def test_manifest_drift_blocks_prepare_before_creating_state(monkeypatch,tmp_path):
    def fail(*args):raise ValueError("Release content mismatch")
    monkeypatch.setattr(admin,"verify_release",fail)
    state=tmp_path/"state"
    with pytest.raises(ValueError,match="content mismatch"):
        admin.prepare(tmp_path,tmp_path,state,tmp_path,"candidate")
    assert not state.exists()


def test_rollback_stop_handles_a_missing_role_without_touching_the_executor(monkeypatch):
    calls=[]
    monkeypatch.setattr(admin,"command",lambda _:json.dumps([{"name":"swiftchart-bot"},{"name":"swiftchart-executor"}]))
    monkeypatch.setattr(admin.subprocess,"run",lambda args,**kwargs:calls.append(args))
    admin.stop_release_processes()
    assert calls==[["pm2","stop","swiftchart-bot"]]
