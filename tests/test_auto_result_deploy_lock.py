"""Run the actual wrapper with real flock and a fake Docker boundary."""
import fcntl
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wrapper(tmp_path):
    bindir=tmp_path/'bin'
    bindir.mkdir()
    docker=bindir/'docker'
    docker.write_text('''#!/usr/bin/env python3
import os,sys,time
from pathlib import Path
s=' '.join(sys.argv[1:])
with open(os.environ['CALLS'],'a') as f:f.write(s+'\\n')
if s.startswith('compose run'):
    Path(os.environ['READY']).touch()
    while os.getenv('HOLD') and not Path(os.environ['DONE']).exists():time.sleep(.02)
elif s.startswith('image inspect') and 'Config.Env' in s:print('TOTISH_RELEASE='+'a'*40)
elif s.startswith('image inspect'):print('sha256:imageid')
elif 'Config.Image' in s:print('ghcr.io/mmaximm2017-netizen/football-totalizator@sha256:'+'b'*64)
elif 'State.Running' in s:print('true|healthy')
elif '.Image' in s:print('sha256:imageid')
elif '.Id' in s:print('containerid')
else:sys.exit(2)
''')
    docker.chmod(0o755)
    script=tmp_path/'wrapper.sh'
    source=(ROOT/'scripts/run_auto_results.sh').read_text()
    source=source.replace('export PATH="','export PATH="'+str(bindir)+':',1)
    script.write_text(source)
    (tmp_path/'.totish-managed-release').write_text('a'*40)
    scripts=tmp_path/'scripts'
    scripts.mkdir()
    (scripts/'host_telegram_notifier.py').write_text('pass\n')
    env={**os.environ,'TOTISH_PROJECT_ROOT':str(tmp_path),
         'TOTISH_AUTO_RESULTS_LOG':str(tmp_path/'log'),
         'TOTISH_AUTO_RESULTS_LOCK':str(tmp_path/'worker.lock'),
         'TOTISH_DEPLOY_LOCK_FILE':str(tmp_path/'deploy.lock'),
         'CALLS':str(tmp_path/'calls'),'READY':str(tmp_path/'ready'),'DONE':str(tmp_path/'done')}
    return script,env,tmp_path


def test_deploy_in_progress_prevents_any_docker_start(wrapper):
    script,env,root=wrapper
    with (root/'deploy.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True,timeout=5,check=False)
    assert result.returncode == 0 and 'deploy in progress' in result.stdout
    assert not (root/'calls').exists()


def test_worker_holds_shared_deploy_lock_and_uses_only_image_scripts(wrapper):
    script,env,root=wrapper
    process=subprocess.Popen(['bash',str(script)],env={**env,'HOLD':'1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        limit=time.monotonic()+5
        while not (root/'ready').exists() and time.monotonic()<limit:
            time.sleep(.02)
        assert (root/'ready').exists()
        with (root/'deploy.lock').open('w') as lock, pytest.raises(BlockingIOError):
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        calls=(root/'calls').read_text()
        assert 'compose run' in calls and '/app/scripts:ro' not in calls
    finally:
        (root/'done').touch()
        process.communicate(timeout=5)
    assert process.returncode == 0
    with (root/'deploy.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_release_mismatch_fails_closed(wrapper):
    script,env,root=wrapper
    (root/'.totish-managed-release').write_text('c'*40)
    result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True,timeout=5,check=False)
    assert result.returncode != 0 and 'release mismatch' in result.stderr
    assert 'compose run' not in (root/'calls').read_text()


def test_nested_deploy_reuses_inherited_lock_without_deadlock(tmp_path):
    source=(ROOT/'scripts/deploy_production.sh').read_text()
    start=source.index('if [[ "$(readlink /proc/$$/fd/9')
    end=source.index('\n\nCURRENT_CONTAINER_ID',start)
    nested=tmp_path/'nested.sh'
    nested.write_text('set -e\n'+source[start:end])
    lock=tmp_path/'deploy.lock'
    result=subprocess.run(['bash','-c',
        'export LOCK_FILE="$1"; exec 9>"$LOCK_FILE"; flock -n 9; bash "$2"',
        'test',str(lock),str(nested)],capture_output=True,text=True,timeout=5,check=False)
    assert result.returncode == 0, result.stderr


def test_workflow_lock_covers_managed_marker_and_rollback():
    source=(ROOT/'.github/workflows/deploy.yml').read_text()
    assert source.index('exec 9>/tmp/totish-production-deploy.lock') < source.index('previous_image=')
    assert source.index('exec 9>/tmp/totish-production-deploy.lock') < source.index('CONTROL PLANE SYNC OK')
    assert 'run_auto_results\\.sh|run_database_backup' in source


def test_first_rollout_drains_existing_worker_before_touching_image():
    source=(ROOT/'.github/workflows/deploy.yml').read_text()
    assert source.index('flock -w 150 9') < source.index('exec 8>"$HOME/.local/state/totish/auto-results.lock"')
    assert source.index('flock -w 150 8') < source.index('previous_image=')
