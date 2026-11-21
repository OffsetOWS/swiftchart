"""Owner-approved API/agent switch. Never deploy frontend or operate the legacy ECS service.

The operator must stop and verify the old poller separately before start-agent.
Rollback preserves live database writes and never resets the shared checkout.
"""
from __future__ import annotations

import argparse
import configparser
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.stage1_admin import backup_db, config, processes, verify_release
from scripts.stage2_admin import exported_value
from scripts.crypto_soak_gate import validate_summary

TOKEN_FILE=Path('/etc/swiftchart-agent/api-token.env')
WORKLOAD_AUTH_DIR=Path('/etc/swiftchart-agent/aws')


def role_credentials(data):
    """Validate the dedicated, read-only mounted renewable workload profile."""
    if not (data.get('AWS_PROFILE') or data.get('AWS_CONFIG_FILE')):
        return False
    if data.get('AWS_PROFILE')!='swiftchart-agent-vps' or data.get('AWS_CONFIG_FILE')!='/run/bedrock/config':
        raise ValueError('Only the dedicated VPS workload profile is permitted')
    if any(data.get(key) for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_BEARER_TOKEN_BEDROCK')):
        raise ValueError('Do not mix the renewable workload profile with static credentials')
    for name in ('config','workload.key','workload.crt'):
        path=WORKLOAD_AUTH_DIR/name
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('Workload configuration and certificate files must be private')
    helper=WORKLOAD_AUTH_DIR/'aws_signing_helper'
    if not helper.is_file() or helper.is_symlink() or helper.stat().st_mode & 0o022:
        raise ValueError('Workload signing helper must not be writable by group or others')
    config=configparser.ConfigParser(interpolation=None)
    config.read(WORKLOAD_AUTH_DIR/'config')
    section='profile swiftchart-agent-vps'
    if config.sections()!=[section] or set(config[section])!={'region','credential_process'}:
        raise ValueError('Unexpected workload profile configuration')
    command=shlex.split(config[section]['credential_process'])
    if config[section]['region']!=data['TRADING_AGENT_AWS_REGION'] or command[:2]!=['/run/bedrock/aws_signing_helper','credential-process']:
        raise ValueError('Unexpected workload credential provider')
    flags=command[2:]
    if len(flags)!=14:
        raise ValueError('Unexpected workload credential provider arguments')
    values=dict(zip(flags[::2],flags[1::2]))
    expected={'--certificate','--private-key','--trust-anchor-arn','--profile-arn','--role-arn','--region','--session-duration'}
    if set(values)!=expected or values['--certificate']!='/run/bedrock/workload.crt' or values['--private-key']!='/run/bedrock/workload.key' or values['--region']!=data['TRADING_AGENT_AWS_REGION'] or values['--session-duration']!='3600':
        raise ValueError('Unexpected workload credential provider arguments')
    return True


def invoke(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE)


def load_env(path):
    data={}
    if path.stat().st_mode & 0o077:
        raise ValueError('Runtime environment file must be private mode 0600')
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or value.startswith('YOUR_'):
            raise ValueError('Invalid runtime environment file')
        data[key]=value
    required=['TRADING_AGENT_TELEGRAM_BOT_TOKEN']
    if any(not data.get(key) for key in required) or not (data.get('TRADING_AGENT_ALLOWED_USER_IDS') or data.get('TRADING_AGENT_ALLOWED_CHAT_IDS')):
        raise ValueError('Runtime credentials/model/unchanged allowlist are required')
    provider=data.get('TRADING_AGENT_WRITER_PROVIDER','xai')
    if provider not in {'xai','bedrock'}:raise ValueError('Unknown writer provider')
    if provider=='xai' and not data.get('XAI_API_KEY'):raise ValueError('Existing server XAI_API_KEY is required')
    bedrock=provider=='bedrock' or data.get('TRADING_AGENT_BEDROCK_FALLBACK_ENABLED','false').lower()=='true'
    if bedrock and (not data.get('TRADING_AGENT_AWS_REGION') or not data.get('TRADING_AGENT_BEDROCK_MODEL_ID') or not (role_credentials(data) or data.get('AWS_BEARER_TOKEN_BEDROCK') or (data.get('AWS_ACCESS_KEY_ID') and data.get('AWS_SECRET_ACCESS_KEY')))):
        raise ValueError('A dedicated VPS Bedrock credential source is required for enabled Bedrock; never copy the operator login session')
    return data


def private_write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(text)
    path.chmod(0o600)


def nginx_guard(text):
    """Insert a deny location only in API server blocks; nginx -t is mandatory."""
    inserts=[]
    for match in re.finditer(r'(?m)^[ \t]*server\s*\{',text):
        depth=1;quote=None;comment=False;escape=False;end=match.end()
        for index in range(match.end(),len(text)):
            char=text[index]
            if comment:
                if char=='\n':comment=False
                continue
            if quote:
                if escape:escape=False
                elif char=='\\':escape=True
                elif char==quote:quote=None
                continue
            if char=='#':comment=True
            elif char in '\"\'':quote=char
            elif char=='{':depth+=1
            elif char=='}':
                depth-=1
                if depth==0:end=index+1;break
        if depth:raise ValueError('Unbalanced nginx configuration')
        block=text[match.end():end]
        if re.search(r'proxy_pass\s+http://(?:127\.0\.0\.1|localhost):8000',block) and 'location ^~ /internal/' not in block:
            inserts.append(match.end())
    for position in reversed(inserts):
        text=text[:position]+'\n    location ^~ /internal/ { return 404; }\n'+text[position:]
    return text,len(inserts)


def verify_api(expected_root):
    rows=json.loads(invoke(['pm2','jlist']))
    row=next(p for p in rows if p.get('name')=='swiftchart-api')
    if row['pm2_env']['status']!='online' or Path(row['pm2_env']['pm_cwd']).resolve()!=expected_root.resolve():
        raise ValueError('API process source/status mismatch')
    actual=(Path('/proc')/str(row['pid'])/'cwd').resolve()
    if actual!=expected_root.resolve():raise ValueError('API working-directory mismatch')
    return rows


def prepare(args):
    manifest=verify_release(args.root,args.commit)
    summary=manifest.get('soak') or {}
    validate_summary(summary,args.commit,manifest['files'])
    load=summary.get('agent_load') or {}
    if not load.get('passed') or load.get('answered')!=100 or load.get('failures')!=0:
        raise ValueError('The exact release must pass the 100-question agent soak')
    env=load_env(args.runtime_env)
    apps=processes(args.previous_root)
    previous=next(app for app in apps if app['name']=='swiftchart-api')
    if args.state.exists():raise ValueError('State already exists; refusing overwrite')
    if args.nginx.is_symlink():args.nginx=args.nginx.resolve()
    original=args.nginx.read_text()
    patched,insertions=nginx_guard(original)
    if not insertions and 'location ^~ /internal/' not in original:
        raise ValueError('No matching API nginx server block')
    args.state.mkdir(parents=True,mode=0o700)
    os.umask(0o077)
    config(args.state/'previous-api.json',[previous])
    updated=deepcopy(previous);updated['cwd']=str(args.root)
    updated['args']=[value.replace(str(args.previous_root),str(args.root)) for value in updated['args']]
    if len(updated['args'])!=2 or updated['args'][0]!='-lc':raise ValueError('Unexpected API shell command')
    position=updated['args'][1].rfind('exec ')
    if position<0:raise ValueError('API command lacks exec')
    updated['args'][1]=updated['args'][1][:position]+'set -a; source '+shlex.quote(str(TOKEN_FILE))+'; set +a; '+updated['args'][1][position:]
    config(args.state/'release-api.json',[updated])
    rows=json.loads(invoke(['pm2','jlist']))
    preserved={row['name']:row['pid'] for row in rows if row.get('name') in {'swiftchart-bot','swiftchart-executor'}}
    db=Path(exported_value(previous['args'][1],'DATABASE_URL').removeprefix('sqlite:///'))
    backup_db(db,args.state/'before-api.private.db')
    shutil.copy2(args.nginx,args.state/'nginx.original')
    private_write(args.state/'nginx.candidate',patched)
    had_token=TOKEN_FILE.exists()
    if had_token:shutil.copy2(TOKEN_FILE,args.state/'api-token.original')
    token=secrets.token_urlsafe(32)
    private_write(TOKEN_FILE,'AGENT_INTERNAL_API_TOKEN='+token+'\n')
    env.update(AGENT_INTERNAL_API_TOKEN=token,SWIFTCHART_API_BASE_URL='http://127.0.0.1:8000',
               TRADING_AGENT_STATE_PATH='/state/sessions',TRADING_AGENT_LOG_PATH='/state/logs/agent.log',TRADING_AGENT_HERMES_HOME='/tmp/hermes')
    private_write(args.runtime_env,''.join(key+'='+value+'\n' for key,value in sorted(env.items())))
    state={'commit':args.commit,'root':str(args.root),'previous_root':str(args.previous_root),
           'nginx':str(args.nginx),'nginx_sha256':hashlib.sha256(original.encode()).hexdigest(),
           'runtime_env':str(args.runtime_env),'preserved_processes':preserved,'had_token_file':had_token}
    private_write(args.state/'state.json',json.dumps(state,indent=2))
    print(json.dumps({'prepared':True,'api_only':True,'nginx_blocks_protected':insertions}))


def switch_api(config_path,root):
    data=json.loads(config_path.read_text())
    if [app['name'] for app in data['apps']]!=['swiftchart-api']:raise ValueError('Only the API process may be switched')
    invoke(['pm2','delete','swiftchart-api'])
    invoke(['pm2','start',str(config_path),'--only','swiftchart-api'])
    verify_api(root)


def wait_api_ready(timeout=60):
    """Wait only for read readiness; never retry an API switch or deployment."""
    deadline=monotonic()+timeout
    while monotonic()<deadline:
        try:
            with urlopen('http://127.0.0.1:8000/health',timeout=2) as response:
                if response.status==200:
                    return
        except (URLError,TimeoutError,OSError):
            pass
        sleep(0.25)
    raise ValueError('API failed to become healthy before the readiness deadline')


def activate_api(args,state):
    verify_release(Path(state['root']),state['commit'])
    verify_api(Path(state['previous_root']))
    nginx=Path(state['nginx'])
    if hashlib.sha256(nginx.read_bytes()).hexdigest()!=state['nginx_sha256']:
        raise ValueError('Nginx drift since prepare')
    shutil.copy2(args.state/'nginx.candidate',nginx)
    try:invoke(['nginx','-t'])
    except Exception:
        shutil.copy2(args.state/'nginx.original',nginx)
        raise
    invoke(['systemctl','reload','nginx'])
    try:
        switch_api(args.state/'release-api.json',Path(state['root']))
        wait_api_ready()
        verify(args,state)
        invoke(['pm2','save'])
    except Exception:
        rollback(args,state)
        raise


def verify(args,state):
    rows=verify_api(Path(state['root']))
    for name,pid in state['preserved_processes'].items():
        if not any(row.get('name')==name and row.get('pid')==pid and row['pm2_env']['status']=='online' for row in rows):
            raise ValueError('An unrelated bot/executor changed; inspect before continuing')
    env=load_env(Path(state['runtime_env']))
    request=Request('http://127.0.0.1:8000/internal/agent/markets',headers={'X-Agent-Token':env['AGENT_INTERNAL_API_TOKEN']})
    with urlopen(request,timeout=30) as response:
        if response.status!=200:raise ValueError('Internal API probe failed')
        data=json.load(response)
        if not data.get('data',{}).get('crypto') or not data.get('data',{}).get('forex'):
            raise ValueError('Internal API market coverage is unavailable')
    with urlopen('http://127.0.0.1:8000/health',timeout=2) as response:
        if response.status!=200:raise ValueError('Health failed')
    print(json.dumps({'verified':True,'api_commit':state['commit'],'bot_and_executor_unchanged':True}))


def start_agent(args,state):
    receipt=json.loads((args.state/'legacy-stopped.json').read_text())
    if receipt.get('running')!=0 or receipt.get('desired')!=0 or receipt.get('all_previous_tasks_stopped') is not True:
        raise ValueError('Legacy ECS poller must be fully stopped first')
    directory=Path('/var/lib/swiftchart-agent')
    directory.mkdir(exist_ok=True,mode=0o700)
    os.chown(directory,10001,10001)
    if subprocess.run(['docker','inspect','swiftchart-agent'],capture_output=True).returncode==0:
        raise ValueError('An agent container already exists; refusing an overlapping poller')
    env=load_env(Path(state['runtime_env']))
    auth_mount=['--mount',f'type=bind,src={WORKLOAD_AUTH_DIR},dst=/run/bedrock,readonly'] if (env.get('TRADING_AGENT_WRITER_PROVIDER','xai')=='bedrock' or env.get('TRADING_AGENT_BEDROCK_FALLBACK_ENABLED','false').lower()=='true') and role_credentials(env) else []
    invoke(['docker','run','-d','--name','swiftchart-agent','--restart','unless-stopped',
            '--cpus','0.25','--memory','768m','--memory-swap','768m','--pids-limit','128',
            '--network','host','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=134217728',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--stop-timeout','30',
            '--env-file',state['runtime_env'],'--mount',f'type=bind,src={directory},dst=/state',
            '--log-opt','max-size=5m','--log-opt','max-file=3',
            *auth_mount,
            '--label','org.opencontainers.image.revision='+state['commit'],args.image])
    print(json.dumps({'agent_started':True,'cpu_quota':0.25,'memory_mib':768,'database_mounted':False}))


def rollback(args,state):
    existing=subprocess.run(['docker','inspect','swiftchart-agent'],capture_output=True,text=True)
    if existing.returncode==0:
        container=json.loads(existing.stdout)[0]
        if container['Config'].get('Labels',{}).get('org.opencontainers.image.revision')!=state['commit']:
            raise ValueError('Container belongs to another release')
        invoke(['docker','stop','--time','30','swiftchart-agent'])
        invoke(['docker','rm','swiftchart-agent'])
    rows=json.loads(invoke(['pm2','jlist']))
    api=next((row for row in rows if row.get('name')=='swiftchart-api'),None)
    if api and api['pm2_env'].get('pm_cwd') not in {state['root'],state['previous_root']}:
        raise ValueError('Another API release is active; refusing rollback overwrite')
    switch_api(args.state/'previous-api.json',Path(state['previous_root']))
    shutil.copy2(args.state/'nginx.original',Path(state['nginx']))
    invoke(['nginx','-t']);invoke(['systemctl','reload','nginx']);invoke(['pm2','save'])
    if state['had_token_file']:shutil.copy2(args.state/'api-token.original',TOKEN_FILE)
    else:TOKEN_FILE.unlink(missing_ok=True)
    print(json.dumps({'api_rolled_back':True,'new_agent_stopped':True,'database_restored':False,
                      'resume_legacy_ecs_separately_after_confirming_new_poller_stopped':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','activate-api','start-agent','verify','rollback'])
    parser.add_argument('--approved',action='store_true')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--previous-root',type=Path)
    parser.add_argument('--runtime-env',type=Path,default=Path('/etc/swiftchart-agent/runtime.env'))
    parser.add_argument('--nginx',type=Path)
    parser.add_argument('--commit')
    parser.add_argument('--image')
    args=parser.parse_args()
    if not args.approved:parser.error('Explicit deployment approval is required')
    os.umask(0o077)
    try:
        if args.action=='prepare':prepare(args)
        else:
            state=json.loads((args.state/'state.json').read_text())
            {'activate-api':activate_api,'start-agent':start_agent,'verify':verify,'rollback':rollback}[args.action](args,state)
    except Exception as exc:
        # Command failures can contain environment values: report type only.
        print(json.dumps({'release_action_failed':args.action,'error_type':type(exc).__name__, 'reason':str(exc) if isinstance(exc,ValueError) else 'Private command diagnostics withheld'}))
        raise SystemExit(1)
