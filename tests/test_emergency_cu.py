"""Behavioral regressions for the temporary production CU policy."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import db_activity_gate as gate
from scripts import monitor_production as monitor

ROOT = Path(__file__).resolve().parents[1]


class EmergencyGateTests(unittest.TestCase):
    def test_idle_checks_do_not_import_or_query_database(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'gate.json'
            recovery = Path(directory) / 'recovery.json'
            state.write_text(json.dumps(dict(generated_at=1000, active=False, next_due=0)))
            # Exercise every five-minute refresh/worker tick and hourly monitor
            # before the bounded watchdog, including the former hourly wake.
            for age in range(0, gate.MAX_IDLE_REFRESH_SECONDS, 300):
                self.assertFalse(gate.refresh_due(state, 1000 + age)[0])
                self.assertFalse(gate.should_run(state, 1000 + age)[0])
                self.assertFalse(gate.monitor_due(state, recovery, 1000 + age)[0])
            self.assertGreater(gate.MAX_IDLE_REFRESH_SECONDS, 3600)
            self.assertEqual(gate.refresh_due(state, 1000 + gate.MAX_IDLE_REFRESH_SECONDS),
                             (True, 'idle_watchdog'))

    def test_hourly_cron_cannot_miss_actionable_window_without_worker(self):
        cron = (ROOT / 'deploy/production.cron').read_text()
        line = next(line for line in cron.splitlines() if 'monitor-due' in line)
        self.assertEqual(line.split()[:5], ['0', '*', '*', '*', '*'])
        from scripts.auto_result_policy import FIRST_CHECK_MINUTES, HARD_DEADLINE_MINUTES
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'gate.json'
            recovery = Path(directory) / 'recovery.json'
            # Every possible minute offset relative to hourly cron, with neither
            # worker nor refresher updating the saved idle plan at next_due.
            for offset in range(60):
                kickoff = 100000 + offset * 60
                start = kickoff + FIRST_CHECK_MINUTES * 60
                end = kickoff + HARD_DEADLINE_MINUTES * 60
                state.write_text(json.dumps(dict(generated_at=kickoff, active=False, next_due=start)))
                probes = [t for t in range(start, end + 1) if t % 3600 == 0
                          and gate.monitor_due(state, recovery, t)[0]]
                self.assertTrue(probes, offset)
                self.assertLess(probes[0] - start, 3600)
            # Even a match added immediately after an idle refresh is discovered
            # before its window ends, with enough time for an hourly probe.
            self.assertLess(gate.MAX_IDLE_REFRESH_SECONDS + 300 + 3600,
                            (HARD_DEADLINE_MINUTES - FIRST_CHECK_MINUTES) * 60)

    def test_stale_missing_and_incident_plans_allow_independent_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'gate.json'
            recovery = Path(directory) / 'recovery.json'
            self.assertTrue(gate.monitor_due(state, recovery, 1000)[0])
            state.write_text(json.dumps(dict(generated_at=1000, active=False, next_due=0)))
            late = 1000 + gate.MAX_IDLE_REFRESH_SECONDS + gate.FAIL_OPEN_GRACE_SECONDS + 1
            self.assertEqual(gate.monitor_due(state, recovery, late), (True, 'stale_plan_fail_open'))
            for family in ('health_db', 'auto_results'):
                recovery.write_text(json.dumps({family: {'key': 'failure'}}))
                self.assertEqual(gate.monitor_due(state, recovery, 1100), (True, 'recovery_probe'))
            recovery.write_text('{}')
            state.write_text(json.dumps(dict(generated_at=1000, active=True, active_until=2000)))
            self.assertEqual(gate.monitor_due(state, recovery, 1100), (True, 'active_window'))

    def test_deploy_removes_legacy_gate_cron_and_preserves_unrelated_jobs(self):
        workflow = (ROOT / '.github/workflows/deploy.yml').read_text()
        # Execute the real deployment awk filter against unmanaged legacy jobs.
        program = workflow.split("awk '", 1)[1].split("' >", 1)[0]
        names = ['host_telegram_notifier.py', 'monitor_production.py',
                 'monitor_production_light.py', 'db_activity_gate.py',
                 'refresh_db_activity_gate.sh', 'run_auto_results.sh',
                 'run_match_result_pushes.sh', 'run_deadline_pushes.sh',
                 'run_morning_digest.sh', 'run_database_backup.sh',
                 'verify_database_backup_restore.sh']
        unrelated = '* * * * * /opt/other/job.sh\n'
        legacy = ''.join('* * * * * cd /opt/football-totalizator && scripts/' + name + '\n'
                         for name in names)
        previous = '# BEGIN TOTISH MANAGED\nold job\n# END TOTISH MANAGED\n'
        result = subprocess.run(['awk', program], input=legacy + previous + unrelated,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, unrelated)

    def test_every_cron_script_and_gate_policy_is_managed(self):
        import re
        manifest = set((ROOT / 'deploy/production-managed-files.txt').read_text().splitlines())
        cron = (ROOT / 'deploy/production.cron').read_text()
        self.assertTrue(set(re.findall(r'scripts/[\w.-]+', cron)) <= manifest)
        self.assertIn('scripts/auto_result_policy.py', manifest)


class EmergencyBackupTests(unittest.TestCase):
    def test_freshness_boundary_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backups = root / 'backups'
            backups.mkdir()
            dump = backups / 'totish-daily-2026-09-17.dump'
            dump.write_text('dump')
            dump.with_suffix('.dump.sha256').write_text('checksum')
            now = 1000000
            with patch.object(monitor, 'STATE_DIR', root), patch.object(monitor.time, 'time', return_value=now), \
                 patch.object(monitor, 'alert') as alert, patch.object(monitor, 'recover') as recover:
                for age in (36 * 3600 + 1, 96 * 3600, 108 * 3600):
                    os.utime(dump, (now - age, now - age))
                    self.assertTrue(monitor.check_database_backup())
                alert.assert_not_called()
                os.utime(dump, (now - 108 * 3600 - 1, now - 108 * 3600 - 1))
                self.assertFalse(monitor.check_database_backup())
                self.assertEqual(alert.call_args.args[0], 'backup:stale')
                self.assertIn('108', alert.call_args.args[1])
                self.assertNotIn('ежеднев', alert.call_args.args[1])
                os.utime(dump, (now, now))
                self.assertTrue(monitor.check_database_backup())
                recover.assert_called_with('backup')

    def test_weekly_snapshot_reuses_dump_and_retains_four_weeks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts/run_database_backup.sh'
            shutil.copyfile(ROOT / 'scripts/run_database_backup.sh', script)
            (root / '.env').write_text('DATABASE_URL=unused-test-value\n')
            binaries = root / 'bin'
            binaries.mkdir()
            docker = binaries / 'docker'
            docker.write_text('''#!/bin/bash
set -eu
if [[ "$*" == *"pg_restore --list"* ]]; then
  cat >/dev/null
  for table in users matches predictions tournaments schema_migrations; do
    echo "TABLE DATA public $table owner"
  done
else
  echo dump >> "$TEST_DUMP_CALLS"
  printf '%s' "$TEST_DUMP_CONTENT"
fi
''')
            date = binaries / 'date'
            date.write_text('''#!/bin/bash
case "$1" in
  +%F) echo "$TEST_DATE" ;;
  +%G-W%V) echo "$TEST_WEEK" ;;
  *) exit 9 ;;
esac
''')
            for binary in (docker, date):
                binary.chmod(0o755)
            env = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'],
                       TOTISH_STATE_DIR=str(root / 'state'), TEST_DUMP_CALLS=str(root / 'calls'))
            backups = root / 'state/backups'
            runs = [('2026-09-17', '2026-W38'), ('2026-09-21', '2026-W39'),
                    ('2026-09-25', '2026-W39'), ('2026-09-29', '2026-W40'),
                    ('2026-10-05', '2026-W41'), ('2026-10-09', '2026-W41'),
                    ('2026-10-13', '2026-W42'), ('2026-10-17', '2026-W42')]
            first = {}
            for count, (day, week) in enumerate(runs, 1):
                env.update(TEST_DATE=day, TEST_WEEK=week, TEST_DUMP_CONTENT='dump-' + day)
                result = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                weekly = backups / f'totish-weekly-{week}.dump'
                first.setdefault(week, 'dump-' + day)
                self.assertEqual(weekly.read_text(), first[week])
                self.assertEqual(len((root / 'calls').read_text().splitlines()), count)
                checksum = subprocess.run(['sha256sum', '-c', str(weekly) + '.sha256'], capture_output=True)
                self.assertEqual(checksum.returncode, 0)
            self.assertEqual(len(list(backups.glob('totish-weekly-*.dump'))), 4)
            self.assertEqual(len(list(backups.glob('totish-weekly-*.dump.sha256'))), 4)
            self.assertEqual(len(list(backups.glob('totish-daily-*.dump'))), 7)
            self.assertFalse((backups / 'totish-weekly-2026-W38.dump').exists())


if __name__ == '__main__':
    unittest.main()
