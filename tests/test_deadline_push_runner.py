import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_deadline_pushes.sh"
SOURCE = WRAPPER.read_text(encoding="utf-8")


class DeadlinePushRunnerContractTests(unittest.TestCase):
    def test_wrapper_is_bash_strict_mode(self):
        self.assertIn("#!/usr/bin/env bash", SOURCE)
        self.assertIn("set -Eeuo pipefail", SOURCE)

    def test_only_dry_run_argument_is_allowed(self):
        self.assertIn('"$1" != "--dry-run"', SOURCE)
        self.assertIn("exit 2", SOURCE)

    def test_nonblocking_flock_is_configured(self):
        self.assertIn('"$FLOCK_BIN" -n 9', SOURCE)
        self.assertIn("SKIP overlapping run", SOURCE)

    def test_hard_timeout_is_configured(self):
        self.assertIn('TIMEOUT_SECONDS="${TOTISH_DEADLINE_PUSH_TIMEOUT:-180}"', SOURCE)
        self.assertIn('"$TIMEOUT_BIN" --signal=TERM --kill-after=10s', SOURCE)

    def test_worker_uses_expected_project_and_read_only_scripts_mount(self):
        self.assertIn('PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-', SOURCE)
        self.assertIn('"$PROJECT_ROOT/scripts:/app/scripts:ro"', SOURCE)
        self.assertIn("app python scripts/send_deadline_pushes.py", SOURCE)

    def test_worker_uses_noninteractive_compose_mode(self):
        self.assertIn(
            '"$DOCKER_BIN" compose run --rm -T --interactive=false \\\n'
            '        -v "$PROJECT_ROOT/scripts:/app/scripts:ro"',
            SOURCE,
        )

    def test_default_mode_does_not_add_dry_run(self):
        self.assertIn("WORKER_ARGS=()", SOURCE)
        self.assertIn('WORKER_ARGS+=("--dry-run")', SOURCE)
        self.assertIn('"${WORKER_ARGS[@]}"', SOURCE)

    def test_logging_and_exit_code_are_recorded(self):
        self.assertIn("/var/log/totish-deadline-push.log", SOURCE)
        self.assertIn("MAX_LOG_BYTES=10485760", SOURCE)
        self.assertIn("tail -c 5242880", SOURCE)
        self.assertIn("START deadline worker", SOURCE)
        self.assertIn("FINISH deadline worker exit_code=", SOURCE)

    def test_cron_cadence_is_documented(self):
        self.assertIn("*/5 * * * * /opt/football-totalizator/scripts/run_deadline_pushes.sh", SOURCE)
        self.assertIn("SHELL=/bin/bash", SOURCE)

    def test_recovery_notice_is_paired_with_successful_failure_alert(self):
        self.assertIn('RECOVERY_MARKER="$STATE_DIR/deadline-worker.failed"', SOURCE)
        self.assertIn(': >"$RECOVERY_MARKER"', SOURCE)
        self.assertIn('elif [[ -f "$RECOVERY_MARKER" ]]; then', SOURCE)
        self.assertIn("✅ ТОТИШ: фоновая задача восстановилась", SOURCE)
        self.assertIn('rm -f "$RECOVERY_MARKER"', SOURCE)

    def test_wrapper_does_not_contain_credentials_or_secret_arguments(self):
        for secret_name in ("WEB_PUSH_VAPID_PRIVATE_KEY", "p256dh", "auth", "--token"):
            self.assertNotIn(secret_name, SOURCE)


if __name__ == "__main__":
    unittest.main()
