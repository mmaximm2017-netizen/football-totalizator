#!/usr/bin/env python3
"""Activate and verify the system Telegram service; no credentials or DB access."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time

SERVICE = 'totish-telegram-admin.service'
MODULES = ('telegram_admin_bot.py', 'telegram_bot_ui.py', 'telegram_http.py',
           'host_telegram_notifier.py', 'telegram_update_queue.py')


def changed(source, installed):
    return any(not (installed / 'scripts' / name).is_file()
               or (source / 'scripts' / name).read_bytes() != (installed / 'scripts' / name).read_bytes()
               for name in MODULES)


def restart_command():
    command = ['/usr/bin/systemctl', 'restart', SERVICE]
    return command if os.geteuid() == 0 else ['/usr/bin/sudo', '-n', *command]


def check_access():
    if os.geteuid() != 0:
        result = subprocess.run(['/usr/bin/sudo', '-n', '-l'], check=True, timeout=5,
                                text=True, capture_output=True)
        allowed = re.search(r'NOPASSWD:\s*/usr/bin/systemctl\s+restart\s+' + re.escape(SERVICE) + r'(?=[,\s]|$)', result.stdout)
        if not allowed:
            raise RuntimeError('telegram_service_restart_permission_missing')


def verified(root):
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'show', SERVICE, '--property=MainPID,ActiveState'],
                                text=True, capture_output=True, timeout=5, check=True)
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        marker = json.loads((root / 'runtime' / 'telegram-admin-process.json').read_text())
        expected = {name: hashlib.sha256((root / 'scripts' / name).read_bytes()).hexdigest() for name in MODULES}
        return properties.get('ActiveState') == 'active' and int(properties.get('MainPID', 0)) > 0 and marker['pid'] == int(properties['MainPID']) and marker['hashes'] == expected
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--installed', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    if args.preflight:
        if args.source is None:
            parser.error('--source required for preflight')
        if changed(args.source, args.installed) or not verified(args.installed):
            check_access()
        print('TELEGRAM_ACTIVATION_PREFLIGHT_OK')
        return 0
    if not verified(args.installed):
        check_access()
        subprocess.run(restart_command(), check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        if verified(args.installed):
            print('TELEGRAM_PROCESS_VERSION_OK')
            return 0
        time.sleep(.5)
    raise RuntimeError('telegram_process_version_unverified')


if __name__ == '__main__':
    raise SystemExit(main())
