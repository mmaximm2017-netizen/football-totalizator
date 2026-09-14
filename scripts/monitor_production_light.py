#!/usr/bin/env python3
"""Minute-level production checks that do not intentionally touch PostgreSQL.

The full monitor still runs every 15 minutes for DB health and auto-result monitor
checks. On quarter-hour boundaries this script exits immediately so the two monitor
passes do not race or duplicate recovery notifications.
"""

from datetime import datetime

import monitor_production as monitor


def main() -> int:
    if datetime.now().minute % 15 == 0:
        return 0

    ok = True
    if not monitor.check_container():
        return 1
    if not monitor.check_local_health():
        ok = False
    if not monitor.check_control_plane_release():
        ok = False
    if not monitor.check_database_backup():
        ok = False
    if not monitor.check_public_health():
        ok = False

    if ok:
        print("MONITOR LIGHT: OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
