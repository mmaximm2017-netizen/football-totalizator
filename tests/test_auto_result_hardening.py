from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import auto_result_finalization_service as service
from scripts import auto_result_worker as worker
from scripts.auto_result_sources import (
    SourceError,
    find_livesport_result,
    find_sportbox_candidate,
)


def test_livesport_unparseable_page_is_source_error():
    with pytest.raises(SourceError, match="livesport_calendar_dates_missing"):
        find_livesport_result("<html>captcha</html>", home="Зенит", away="ЦСКА", match_date="2026-09-05")


def test_observation_parser_error_marks_source_unhealthy():
    cache = worker.PageCache()
    cache._pages["sports_rpl"] = "<html>changed markup</html>"
    match = {"scope": "rpl", "home_team": "Зенит", "away_team": "ЦСКА", "match_date": "2026-09-05"}
    cache._pages["livesport_rpl"] = "5 сентября, суббота, 2026 Зенит ЦСКА"
    with pytest.raises(worker.SourceError):
        worker.observe_match(cache, match)
    assert cache.source_status()["sports_rpl"]["ok"] is False


def test_final_notice_window_survives_one_missed_cron():
    kickoff = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
    assert worker.window_state(kickoff, kickoff + timedelta(minutes=190)) == "expired"
    assert worker.FINAL_NOTICE_LOOKBACK_MINUTES == 195


def test_sportbox_ambiguous_same_day_match_fails_closed():
    candidates = [
        {"home": "Россия", "away": "Иран", "day": 10, "month": 10, "game_id": "1"},
        {"home": "Россия", "away": "Иран", "day": 10, "month": 10, "game_id": "2"},
    ]
    with pytest.raises(SourceError, match="sportbox_candidate_ambiguous"):
        find_sportbox_candidate(candidates, home="Россия", away="Иран", match_date="2026-10-10")


def test_admin_templates_show_auto_marker_and_list_loads_origin():
    root = Path(__file__).resolve().parents[1]
    service_text = (root / "app/services/admin_view_service.py").read_text(encoding="utf-8")
    assert "m.result_origin" in service_text
    assert '"is_auto_result": (row[9] if len(row) > 9 else None) == "auto_result_worker"' in service_text
    for name in ("admin_russia_2027.html", "admin_russian_cup.html"):
        html = (root / "templates" / name).read_text(encoding="utf-8")
        assert "admin-auto-result-badge" in html


def test_live_finalizer_has_identity_guard():
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "match_identity_changed" in source
    assert "actual_home_team != expected_home_team" in source
    assert "actual_kickoff_time != expected_kickoff_time" in source
