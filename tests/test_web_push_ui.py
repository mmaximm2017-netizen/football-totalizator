from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app import create_app
from app.services import deadline_push_service, match_result_push_service, web_push_service
from scripts import bootstrap_match_result_pushes, send_test_match_result_push


ROOT = Path(__file__).resolve().parents[1]


def test_web_push_is_absent_from_profile_and_production_routes():
    profile = (ROOT / "templates/profile.html").read_text()
    assert "data-web-push" not in profile
    assert "web-push.js" not in profile
    assert not any(rule.rule.startswith("/api/push/") for rule in create_app().url_map.iter_rules())


def test_old_workers_only_remove_existing_subscriptions():
    for path in ("static/push-worker.js", "static/service-worker.js"):
        script = (ROOT / path).read_text()
        assert "subscription.unsubscribe()" in script
        assert "showNotification" not in script
        assert "addEventListener('push'" not in script
    assert "requestPermission" not in (ROOT / "static/js/web-push.js").read_text()


def test_disabled_push_workers_exit_before_database_access():
    for worker in (deadline_push_service, match_result_push_service):
        with patch.object(worker, "get_db", side_effect=AssertionError):
            for dry_run in (False, True):
                assert worker.run_once(dry_run=dry_run)["sent"] == 0
    with patch.object(bootstrap_match_result_pushes, "get_db", side_effect=AssertionError):
        assert bootstrap_match_result_pushes.bootstrap(None)["inserted"] == 0
    with patch.object(send_test_match_result_push, "run_test", side_effect=AssertionError):
        assert send_test_match_result_push.main(["--user-id", "1"]) == 0
    with pytest.raises(RuntimeError, match="push_disabled"):
        web_push_service.send_push({}, {})


def test_legacy_cron_wrappers_exit_without_invoking_docker(tmp_path):
    for name in ("run_deadline_pushes.sh", "run_match_result_pushes.sh"):
        subprocess.run(["/bin/bash", str(ROOT / "scripts" / name)], check=True,
                       cwd=tmp_path, env={"PATH": str(tmp_path)})
