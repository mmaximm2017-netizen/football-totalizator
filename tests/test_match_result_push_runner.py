import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_match_result_pushes.sh"
SOURCE = WRAPPER.read_text(encoding="utf-8")


class MatchResultPushRunnerContractTests(unittest.TestCase):
    def test_wrapper_is_executable(self):
        self.assertTrue(WRAPPER.stat().st_mode & stat.S_IXUSR)

    def test_wrapper_uses_strict_mode(self):
        self.assertIn("#!/usr/bin/env bash", SOURCE)
        self.assertIn("set -Eeuo pipefail", SOURCE)

    def test_only_dry_run_argument_is_allowed(self):
        self.assertIn('"$1" != "--dry-run"', SOURCE)
        self.assertIn("exit 2", SOURCE)

    def test_noninteractive_compose_flags_are_required(self):
        self.assertIn('compose run --rm -T --interactive=false', SOURCE)

    def test_separate_lock_timeout_and_log_are_configured(self):
        self.assertIn("/tmp/totish-match-result-push.lock", SOURCE)
        self.assertIn("/var/log/totish-match-result-push.log", SOURCE)
        self.assertIn('"$FLOCK_BIN" -n 9', SOURCE)
        self.assertIn('--signal=TERM --kill-after=10s', SOURCE)

    def test_worker_and_read_only_mount_are_configured(self):
        self.assertIn('"$PROJECT_ROOT/scripts:/app/scripts:ro"', SOURCE)
        self.assertIn("app python scripts/send_match_result_pushes.py", SOURCE)

    def test_start_finish_timeout_and_bounded_log_are_configured(self):
        self.assertIn("START match-result worker", SOURCE)
        self.assertIn("FINISH match-result worker exit_code=", SOURCE)
        self.assertIn("TIMEOUT match-result worker", SOURCE)
        self.assertIn("MAX_LOG_BYTES=10485760", SOURCE)
        self.assertIn("tail -c 5242880", SOURCE)

    def test_recovery_notice_is_paired_with_successful_failure_alert(self):
        self.assertIn('RECOVERY_MARKER="$STATE_DIR/match-result-worker.failed"', SOURCE)
        self.assertIn(': >"$RECOVERY_MARKER"', SOURCE)
        self.assertIn('elif [[ -f "$RECOVERY_MARKER" ]]; then', SOURCE)
        self.assertIn("✅ ТОТИШ: фоновая задача восстановилась", SOURCE)
        self.assertIn('rm -f "$RECOVERY_MARKER"', SOURCE)

    def test_wrapper_has_no_secrets_or_endpoints(self):
        for secret_name in ("WEB_PUSH_VAPID_PRIVATE_KEY", "p256dh", "auth", "--token", "endpoint"):
            self.assertNotIn(secret_name, SOURCE)


if __name__ == "__main__":
    unittest.main()
