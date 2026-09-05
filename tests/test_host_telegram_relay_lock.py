"""Real host flock concurrency, using subprocesses and no Telegram network."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
from pathlib import Path
import sys, time
from scripts import host_telegram_notifier as relay
root = Path(sys.argv[1])
relay.STATE_DIR = root / 'state'
relay.DEDUPE_FILE = relay.STATE_DIR / 'dedupe.json'
relay.OUTBOX_DIR = root / 'out'
def send(message):
    (root / 'entered').touch()
    deadline = time.monotonic() + 10
    while not (root / 'release').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test release timeout')
        time.sleep(.01)
    with (root / 'sent').open('a') as handle:
        handle.write(message + '\\n')
relay.send_message = send
print(relay.drain_outbox(), flush=True)
'''


def start(tmp_path):
    return subprocess.Popen([sys.executable, '-c', CHILD, str(tmp_path)], cwd=ROOT,
                            env={**os.environ, 'PYTHONPATH': str(ROOT)},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_entered(tmp_path, process):
    deadline = time.monotonic() + 5
    while not (tmp_path / 'entered').exists():
        assert process.poll() is None
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_two_relay_processes_send_one_file_only_once(tmp_path):
    (tmp_path / 'out').mkdir()
    message = tmp_path / 'out' / 'one.msg'
    message.write_text('one message')
    first = start(tmp_path)
    try:
        wait_entered(tmp_path, first)
        second = start(tmp_path)
        output, error = second.communicate(timeout=5)
        assert second.returncode == 0, error
        assert output.strip() == '0'
        assert message.exists()  # skipping relay did not consume pending work
        (tmp_path / 'release').touch()
        output, error = first.communicate(timeout=5)
        assert first.returncode == 0, error
        assert output.strip() == '1'
        assert (tmp_path / 'sent').read_text().splitlines() == ['one message']
        assert not message.exists()
    finally:
        if first.poll() is None:
            first.kill()
        first.wait(timeout=5)


def test_dead_relay_releases_lock_and_preserves_pending_file(tmp_path):
    (tmp_path / 'out').mkdir()
    message = tmp_path / 'out' / 'one.msg'
    message.write_text('pending')
    first = start(tmp_path)
    try:
        wait_entered(tmp_path, first)
        first.kill()
        first.wait(timeout=5)
        assert message.exists()
        (tmp_path / 'release').touch()
        retry = start(tmp_path)
        output, error = retry.communicate(timeout=5)
        assert retry.returncode == 0, error
        assert output.strip() == '1'
        assert (tmp_path / 'sent').read_text().splitlines() == ['pending']
        assert not message.exists()
    finally:
        if first.poll() is None:
            first.kill()
        first.wait(timeout=5)
