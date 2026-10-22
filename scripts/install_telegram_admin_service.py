#!/usr/bin/env python3
"""One-time root bootstrap for the existing system bot and narrow deploy access."""
import argparse
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('deploy_user')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required_for_service_bootstrap')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*\$?', args.deploy_user):
        raise ValueError('invalid_deploy_username')
    pwd.getpwnam(args.deploy_user)
    root = Path(__file__).resolve().parents[1]
    rule = f'{args.deploy_user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart totish-telegram-admin.service\n'
    with tempfile.NamedTemporaryFile('w', delete=False) as handle:
        handle.write(rule)
        temporary = Path(handle.name)
    try:
        subprocess.run(['/usr/sbin/visudo', '-cf', str(temporary)], check=True)
        destination = Path('/etc/sudoers.d/totish-telegram-admin')
        shutil.copyfile(temporary, destination)
        destination.chmod(0o440)
    finally:
        temporary.unlink(missing_ok=True)
    shutil.copyfile(root / 'deploy/systemd/totish-telegram-admin.service', '/etc/systemd/system/totish-telegram-admin.service')
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)
    # Activation belongs to the subsequent authorized deployment.
    print('TELEGRAM_SERVICE_BOOTSTRAP_OK')


if __name__ == '__main__':
    main()
