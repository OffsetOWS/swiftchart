from pathlib import Path
import subprocess
import sys

import pytest

from scripts.agent_vps_release import load_env, nginx_guard


def test_nginx_guard_changes_only_api_server_blocks_and_is_idempotent():
    before='''server {
    server_name static.example.invalid;
    location / { return 200; }
}
server {
    # a comment with { braces }
    server_name api.example.invalid;
    location /api/ { proxy_pass http://127.0.0.1:8000; }
}
'''
    after,count=nginx_guard(before)
    assert count==1
    assert after.split('server {')[1]==before.split('server {')[1]
    assert 'location ^~ /internal/ { return 404; }' in after
    assert nginx_guard(after)==(after,0)


def test_nginx_guard_rejects_unbalanced_configuration():
    with pytest.raises(ValueError,match='Unbalanced'):
        nginx_guard('server {\nlocation / { proxy_pass http://127.0.0.1:8000; }')


def test_release_cli_refuses_mutation_without_approval(tmp_path):
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(root/'scripts/agent_vps_release.py'),'rollback','--state',str(tmp_path/'missing')],capture_output=True,text=True)
    assert result.returncode==2 and 'Explicit deployment approval is required' in result.stderr
    assert not (tmp_path/'missing').exists()


def test_runtime_env_must_be_private_non_placeholder_and_have_workload_auth(tmp_path):
    path=tmp_path/'runtime.env'
    path.write_text('TRADING_AGENT_TELEGRAM_BOT_TOKEN=test-token\nTRADING_AGENT_ALLOWED_USER_IDS=42\nTRADING_AGENT_AWS_REGION=selected-region\nTRADING_AGENT_BEDROCK_MODEL_ID=configured-model\n')
    path.chmod(0o644)
    with pytest.raises(ValueError,match='private'):
        load_env(path)
    path.chmod(0o600)
    with pytest.raises(ValueError,match='dedicated VPS Bedrock credential'):
        load_env(path)
    path.write_text(path.read_text()+'AWS_BEARER_TOKEN_BEDROCK=test-bedrock-credential\n')
    assert load_env(path)['TRADING_AGENT_ALLOWED_USER_IDS']=='42'
    path.write_text(path.read_text().replace('configured-model','YOUR_MODEL'))
    with pytest.raises(ValueError,match='Invalid runtime'):
        load_env(path)


def workload_env(tmp_path,monkeypatch):
    from scripts import agent_vps_release as release
    directory=tmp_path/'aws';directory.mkdir(mode=0o700)
    monkeypatch.setattr(release,'WORKLOAD_AUTH_DIR',directory)
    for name in ('workload.key','workload.crt','aws_signing_helper'):
        file=directory/name;file.write_text('fixture');file.chmod(0o600)
    command='/run/bedrock/aws_signing_helper credential-process --certificate /run/bedrock/workload.crt --private-key /run/bedrock/workload.key --trust-anchor-arn test-anchor --profile-arn test-profile --role-arn test-role --region selected-region --session-duration 3600'
    config=directory/'config';config.write_text('[profile swiftchart-agent-vps]\nregion=selected-region\ncredential_process='+command+'\n');config.chmod(0o600)
    return release, directory, {'AWS_PROFILE':'swiftchart-agent-vps','AWS_CONFIG_FILE':'/run/bedrock/config','TRADING_AGENT_AWS_REGION':'selected-region'}


def test_renewable_workload_profile_is_supported_and_operator_profiles_are_rejected(tmp_path,monkeypatch):
    release,directory,data=workload_env(tmp_path,monkeypatch)
    assert release.role_credentials(data)
    with pytest.raises(ValueError,match='dedicated VPS'):
        release.role_credentials(data|{'AWS_PROFILE':'operator'})
    with pytest.raises(ValueError,match='mix'):
        release.role_credentials(data|{'AWS_ACCESS_KEY_ID':'operator-placeholder'})


def test_workload_private_key_permissions_and_provider_are_checked(tmp_path,monkeypatch):
    release,directory,data=workload_env(tmp_path,monkeypatch)
    (directory/'workload.key').chmod(0o644)
    with pytest.raises(ValueError,match='private'):
        release.role_credentials(data)
    (directory/'workload.key').chmod(0o600)
    config=directory/'config';config.write_text(config.read_text().replace('/run/bedrock/aws_signing_helper','/bin/sh'))
    with pytest.raises(ValueError,match='provider'):
        release.role_credentials(data)
