from datetime import datetime, timezone
import json

from scripts.auto_result_sources import (
    STATUS_FINISHED,
    STATUS_NOT_FINISHED,
    Observation,
    find_livesport_result,
    find_sports_rpl_result,
    parse_rfs_detail,
    parse_sportbox_game_json,
)
from scripts.auto_result_worker import _update_enabled_state, decide, window_state


def test_livesport_requires_ok_and_ignores_penalty_score():
    html = """<html><body>
    2 сентября, среда, 2026 Ок пен Динамо 1:1 5:4 Ахмат
    3 сентября, четверг, 2026 19:30 Динамо –:– Ахмат
    </body></html>"""
    finished = find_livesport_result(
        html, home="Динамо", away="Ахмат", match_date="2026-09-02"
    )
    assert finished.status == STATUS_FINISHED
    assert (finished.home_score, finished.away_score) == (1, 1)
    waiting = find_livesport_result(
        html, home="Динамо", away="Ахмат", match_date="2026-09-03"
    )
    assert waiting.status == STATUS_NOT_FINISHED


def test_sports_rpl_requires_match_finished_text():
    finished_html = """
    <article class="calendar-card" data-match-status="FINISHED">
      <a title="Матч Акрон - ЦСКА"></a>
      <time datetime="2026-08-28T15:00:00Z"></time>
      <div>Матч окончен</div>
      <span class="calendar-score__score">2</span>
      <span class="calendar-score__score">2</span>
    </article>
    """
    result = find_sports_rpl_result(
        finished_html, home="Акрон", away="ЦСКА", match_date="2026-08-28"
    )
    assert result.status == STATUS_FINISHED
    assert (result.home_score, result.away_score) == (2, 2)

    future_html = (
        finished_html.replace("FINISHED", "NOT_STARTED")
        .replace("Матч окончен", "16:30")
        .replace(">2<", ">-<")
    )
    result = find_sports_rpl_result(
        future_html, home="Акрон", away="ЦСКА", match_date="2026-08-28"
    )
    assert result.status == STATUS_NOT_FINISHED


def test_sportbox_requires_timeline_end_not_merely_score():
    final = json.dumps(
        {
            "live": 0,
            "score": "3 : 0",
            "football_timeline_html": '<div class="b-timeline b-timeline-end"></div>',
        }
    )
    result = parse_sportbox_game_json(final, match_date="2026-06-09")
    assert result.status == STATUS_FINISHED
    assert (result.home_score, result.away_score) == (3, 0)

    not_final = json.dumps(
        {
            "live": 0,
            "score": "3 : 0",
            "football_timeline_html": '<div class="b-timeline"></div>',
        }
    )
    result = parse_sportbox_game_json(not_final, match_date="2026-06-09")
    assert result.status == STATUS_NOT_FINISHED


def test_rfs_detail_knows_target_date_before_match_is_finished():
    html = """
      <h1>3 сентября 2026</h1>
      <div>Динамо</div><div>Ахмат</div><div>Матч не начался</div>
    """
    result = parse_rfs_detail(html, score=None)
    assert result.status == STATUS_NOT_FINISHED
    assert result.match_date == "2026-09-03"


def test_decision_requires_two_matching_finished_observations():
    a = Observation("a", STATUS_FINISHED, home_score=2, away_score=1)
    b = Observation("b", STATUS_FINISHED, home_score=2, away_score=1)
    assert decide(a, b) == {"decision": "would_write", "score": (2, 1)}

    b2 = Observation("b", STATUS_FINISHED, home_score=1, away_score=1)
    assert decide(a, b2)["decision"] == "score_conflict"

    waiting = Observation("b", STATUS_NOT_FINISHED)
    decision = decide(a, waiting)
    assert decision["decision"] == "one_source_confirmed"
    assert decision["confirmed_source"] == "a"


def test_kill_switch_notifies_only_on_real_transitions(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setattr(
        "scripts.auto_result_worker._queue_message",
        lambda outbox, message: messages.append(message),
    )
    state = {}
    _update_enabled_state(state, False, tmp_path)
    assert messages == []
    _update_enabled_state(state, False, tmp_path)
    assert messages == []
    _update_enabled_state(state, True, tmp_path)
    assert len(messages) == 1 and "снова включена" in messages[-1]
    _update_enabled_state(state, True, tmp_path)
    assert len(messages) == 1
    _update_enabled_state(state, False, tmp_path)
    assert len(messages) == 2 and "отключена" in messages[-1]


def test_window_starts_at_120_and_hard_ends_after_360_with_one_cron_grace():
    kickoff = datetime(2026, 9, 3, 16, 30, tzinfo=timezone.utc)
    assert window_state(
        kickoff, datetime(2026, 9, 3, 18, 29, 59, tzinfo=timezone.utc)
    ) == "too_early"
    assert window_state(
        kickoff, datetime(2026, 9, 3, 18, 30, tzinfo=timezone.utc)
    ) == "active"
    assert window_state(
        kickoff, datetime(2026, 9, 3, 19, 30, tzinfo=timezone.utc)
    ) == "active"
    assert window_state(
        kickoff, datetime(2026, 9, 3, 22, 31, tzinfo=timezone.utc)
    ) == "expired_grace"
    assert window_state(
        kickoff, datetime(2026, 9, 3, 22, 36, tzinfo=timezone.utc)
    ) == "expired"
