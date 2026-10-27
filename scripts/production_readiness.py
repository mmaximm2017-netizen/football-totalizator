#!/usr/bin/env python3
"""Read-only readiness receipt. Never prints environment values or nginx config."""
import argparse
import json
from pathlib import Path
import shutil
import stat
import subprocess
import time
from urllib.request import Request, urlopen


def command_ok(command, *, source=None):
    try:
        result = subprocess.run(command, input=source, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def inspect(root, state, probe_db=False):
    checks = {}
    env = root / '.env'
    checks['environment_file_private'] = env.is_file() and not bool(stat.S_IMODE(env.stat().st_mode) & 0o077)
    checks['nginx_configuration_test'] = command_ok(['nginx', '-t'])
    checks['age_installed'] = bool(shutil.which('age'))
    checks['rclone_installed'] = bool(shutil.which('rclone'))
    try:
        settings = json.loads((state / 'backup-settings.json').read_text())
        checks['offsite_configuration_present'] = bool(settings.get('remote') and settings.get('age_recipient', '').startswith('age1'))
    except (OSError, ValueError, TypeError, AttributeError):
        checks['offsite_configuration_present'] = False
    try:
        stamp = json.loads((state / 'offsite-backup.json').read_text())
        checks['recent_offsite_copy_receipt'] = 0 <= time.time() - float(stamp['completed_at']) <= 36 * 3600
    except (OSError, ValueError, TypeError, KeyError):
        checks['recent_offsite_copy_receipt'] = False
    try:
        age = time.time() - float((state / 'restore-verified.timestamp').read_text())
        checks['recent_local_restore_receipt'] = 0 <= age <= 8 * 86400
    except (OSError, ValueError):
        checks['recent_local_restore_receipt'] = False
    try:
        with urlopen(Request('https://totish.ru/login', method='HEAD'), timeout=10) as response:
            checks['public_https_login'] = response.status == 200 and response.url.startswith('https://totish.ru/')
            for name in ('Strict-Transport-Security', 'Content-Security-Policy', 'X-Content-Type-Options', 'X-Robots-Tag'):
                checks['header_' + name.lower()] = bool(response.headers.get(name))
    except (OSError, ValueError):
        checks['public_https_login'] = False
    checks['actual_application_reader_role'] = 'not_probed'
    if probe_db:
        source = Path(__file__).with_name('check_reader_permissions.py').read_text()
        checks['actual_application_reader_role'] = command_ok(['docker', 'exec', '-i', 'football-totalizator-app-1', 'python', '-'], source=source)
    return {
        'checks': checks,
        'limitations': [
            'Run nginx -t with an identity allowed to read its configuration; failure is never treated as pass.',
            'A copy receipt alone does not prove restore of the externally downloaded archive.',
            'Telegram delivery and Android TalkBack require separate actual checks.',
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/football-totalizator'))
    parser.add_argument('--state', type=Path, default=Path.home() / '.local/state/totish')
    parser.add_argument('--probe-db', action='store_true', help='One explicit read-only query using the running app connection')
    args = parser.parse_args()
    try:
        receipt = inspect(args.root, args.state, args.probe_db)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        raise SystemExit(0 if all(value is True for value in receipt['checks'].values()) else 1)
    except Exception as exc:
        print(json.dumps({'readiness': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
