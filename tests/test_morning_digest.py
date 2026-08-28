from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import morning_digest_service as digest
from app.services import telegram_error_notifier as notifier
from scripts import send_morning_digest as cli


def match(match_id, tournament, kickoff):
    return {
        "match_id": match_id,
        "tournament_name": tournament,
        "home_team": "Акрон",
        "away_team": "ЦСКА",
        "kickoff_time": kickoff,
        "status": "SCHEDULED",
    }


def digest_data(**overrides):
    data = {
        "date": date(2026, 8, 28),
        "issues": [],
        "today_matches": [],
        "tomorrow_matches": [],
    }
    data.update(overrides)
    return data


class Cursor:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


def test_render_normal_digest_with_today_match():
    message = digest.render_digest(
        digest_data(
            today_matches=[
                match(1, "Чемпионат России 🇷🇺", datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc))
            ]
        )
    )

    assert "☀️ ТОТИШ — утренний отчёт\n28 августа" in message
    assert "✅ Всё работает штатно." in message
    assert "⚽ Сегодня" in message
    assert "РПЛ\n18:00 — Акрон — ЦСКА" in message


def test_render_one_and_multiple_problems():
    one = digest.render_digest(digest_data(issues=["🔴 Одна проблема."]))
    multiple = digest.render_digest(digest_data(issues=["🔴 Первая.", "🟠 Вторая."]))

    assert "🚨 Требует внимания\n\n🔴 Одна проблема." in one
    assert "✅ Всё работает штатно." not in one
    assert "🔴 Первая.\n🟠 Вторая." in multiple


def test_render_today_absent_and_tomorrow_present():
    message = digest.render_digest(
        digest_data(
            tomorrow_matches=[
                match(2, "Кубок России", datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc))
            ]
        )
    )

    assert "Сегодня матчей нет." in message
    assert "⚽ Завтра" in message
    assert "Кубок России\n19:00 — Акрон — ЦСКА" in message


def test_render_today_hides_tomorrow_and_later_matches():
    message = digest.render_digest(
        digest_data(
            today_matches=[match(1, "Кубок России", datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc))],
            tomorrow_matches=[match(2, "Кубок России", datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc))],
        )
    )
    later_only = digest.render_digest(digest_data())

    assert "⚽ Завтра" not in message
    assert "Сегодня матчей нет." not in message
    assert "Сегодня матчей нет." in later_only
    assert "⚽ Завтра" not in later_only


def test_render_groups_multiple_tournaments_with_friendly_names():
    message = digest.render_digest(
        digest_data(
            today_matches=[
                match(1, "Чемпионат России 🇷🇺", datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)),
                match(2, "Кубок России", datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)),
            ]
        )
    )

    assert "Кубок России\n17:00 — Акрон — ЦСКА" in message
    assert "РПЛ\n18:00 — Акрон — ЦСКА" in message


def test_schedule_uses_moscow_day_boundary_and_groups_tournaments():
    cursor = Cursor(
        rows=[
            (1, "Чемпионат России 🇷🇺", "Акрон", "ЦСКА", datetime(2026, 8, 28, 21, 30, tzinfo=timezone.utc), "SCHEDULED"),
            (2, "Кубок России", "Зенит", "Ростов", datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc), "SCHEDULED"),
        ]
    )

    today, tomorrow = digest.fetch_schedule_matches(cursor, date(2026, 8, 29))
    message = digest.render_digest(digest_data(date=date(2026, 8, 29), today_matches=today, tomorrow_matches=tomorrow))

    assert len(today) == 1
    assert today[0]["match_id"] == 1
    assert "AT TIME ZONE 'Europe/Moscow'" in cursor.executed[0][0]
    assert "РПЛ\n00:30 — Акрон — ЦСКА" in message


def test_finished_result_validation_uses_canonical_helper():
    invalid = Cursor(rows=[(1, "ЦСКА", "Балтика", "FINISHED", None, 1)])
    valid = Cursor(rows=[(2, "ЦСКА", "Балтика", "FINISHED", 2, 1)])

    assert digest.find_finished_matches_without_result(invalid)[0]["match_id"] == 1
    assert digest.find_finished_matches_without_result(valid) == []


def test_points_mismatch_uses_calculate_points_and_accepts_zero():
    matching = Cursor(rows=[(1, "FINISHED", 2, 1, 2, 1, 10), (2, "FINISHED", 2, 1, 0, 3, 0)])
    mismatch = Cursor(rows=[(1, "FINISHED", 2, 1, 2, 1, 0)])

    assert digest.count_points_mismatches(matching) == 0
    assert digest.count_points_mismatches(mismatch) == 1


def test_latest_sync_uses_only_the_latest_record():
    success = Cursor(row=("success", 0, {"errors": [], "sync": {"errors": []}}))
    partial = Cursor(row=("partial_success", 0, {"errors": []}))
    nested_error = Cursor(row=("success", 0, {"sync": {"errors": ["upstream"]}}))

    assert digest.latest_sync_has_problem(success) is False
    assert digest.latest_sync_has_problem(partial) is True
    assert digest.latest_sync_has_problem(nested_error) is True


def test_health_ignores_single_active_warning():
    local_health = {"status": "ok"}
    db_health = {"db": "ok", "active_tournament": "ok", "ranking": "ok", "single_active": "warn:2"}

    assert digest.collect_health_issues(
        "running|true|false",
        local_health,
        db_health,
        lambda url: {"status": "ok"},
    ) == []


def test_outbox_only_flags_conservatively_stale_messages(tmp_path):
    fresh = tmp_path / "fresh.msg"
    fresh.write_text("fresh", encoding="utf-8")
    now = datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)
    fresh_time = now.timestamp() - 60
    stale_time = now.timestamp() - digest.OUTBOX_STALE_SECONDS - 1
    import os
    os.utime(fresh, (fresh_time, fresh_time))
    assert digest.collect_outbox_issues(now=now, outbox_dir=tmp_path) == []

    stale = tmp_path / "stale.msg"
    stale.write_text("stale", encoding="utf-8")
    os.utime(stale, (stale_time, stale_time))
    assert "1 сообщений" in digest.collect_outbox_issues(now=now, outbox_dir=tmp_path)[0]


def test_deadline_worker_fresh_at_five_minutes_has_no_issue():
    assert digest.collect_worker_issues(10_000, 9_700, 9_700) == []


def test_deadline_worker_fresh_at_fourteen_minutes_fifty_nine_seconds_has_no_issue():
    assert digest.collect_worker_issues(10_000, 9_101, 9_700) == []


def test_deadline_worker_stale_at_exactly_fifteen_minutes():
    issues = digest.collect_worker_issues(10_000, 9_100, 9_700)

    assert issues == ["🟠 Worker дедлайнов не запускался 15 минут."]


def test_deadline_worker_stale_age_uses_floor_minutes():
    issues = digest.collect_worker_issues(10_000, 7_780, 9_700)

    assert issues == ["🟠 Worker дедлайнов не запускался 37 минут."]


def test_result_worker_stale_message():
    issues = digest.collect_worker_issues(10_000, 9_700, 9_100)

    assert issues == ["🟠 Worker обработки результатов не запускался 15 минут."]


def test_both_workers_stale_produce_two_messages():
    issues = digest.collect_worker_issues(10_000, 9_100, 9_100)

    assert len(issues) == 2
    assert "Worker дедлайнов" in issues[0]
    assert "Worker обработки результатов" in issues[1]


def test_missing_worker_timestamps_are_critical_issues():
    deadline_missing = digest.collect_worker_issues(10_000, None, 9_700)
    result_missing = digest.collect_worker_issues(10_000, 9_700, "")

    assert deadline_missing == ["🔴 Нет данных о запуске worker дедлайнов."]
    assert result_missing == ["🔴 Нет данных о запуске worker обработки результатов."]


def test_malformed_worker_timestamp_is_unavailable_without_crash():
    issues = digest.collect_worker_issues(10_000, "not-a-timestamp", 9_700)

    assert issues == ["🔴 Нет данных о запуске worker дедлайнов."]


def test_future_worker_timestamp_has_zero_age_and_no_alert():
    assert digest.collect_worker_issues(10_000, 10_060, 10_060) == []


def test_healthy_workers_keep_normal_digest_status():
    issues = digest.collect_worker_issues(10_000, 9_700, 9_700)
    message = digest.render_digest(digest_data(issues=issues))

    assert "✅ Всё работает штатно." in message


def test_collect_digest_adds_worker_issues_to_attention_block():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with (
        patch.object(digest, "collect_health_issues", return_value=[]),
        patch.object(digest, "fetch_schedule_matches", return_value=([], [])),
        patch.object(digest, "find_finished_matches_without_result", return_value=[]),
        patch.object(digest, "count_points_mismatches", return_value=0),
        patch.object(digest, "latest_sync_has_problem", return_value=False),
        patch.object(digest, "collect_outbox_issues", return_value=[]),
        patch.object(digest, "get_db", return_value=conn),
        patch.object(digest, "close_db"),
    ):
        data = digest.collect_digest(
            now=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc),
            host_now_epoch=10_000,
            deadline_worker_mtime=9_100,
            result_worker_mtime=9_700,
        )

    assert data["issues"] == ["🟠 Worker дедлайнов не запускался 15 минут."]
    assert "🚨 Требует внимания" in digest.render_digest(data)


def test_dry_run_never_enqueues(monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_digest", lambda **_: digest_data())
    monkeypatch.setattr(cli, "render_digest", lambda _: "preview")
    enqueue = []
    monkeypatch.setattr(cli, "enqueue_telegram_message", lambda message: enqueue.append(message) or True)
    monkeypatch.setattr("sys.argv", ["send_morning_digest.py", "--dry-run"])

    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == "preview"
    assert enqueue == []


def test_cli_reads_host_checked_health_json(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(cli, "render_digest", lambda _: "preview")
    monkeypatch.setattr(
        cli,
        "collect_digest",
        lambda **kwargs: captured.update(kwargs) or digest_data(),
    )
    monkeypatch.setenv("MORNING_DIGEST_LOCAL_HEALTH_JSON", '{"status":"ok"}')
    monkeypatch.setenv("MORNING_DIGEST_DB_HEALTH_JSON", '{"db":"ok"}')
    monkeypatch.setenv("TOTISH_DIGEST_HOST_NOW_EPOCH", "10000")
    monkeypatch.setenv("TOTISH_DEADLINE_WORKER_MTIME", "9700")
    monkeypatch.setenv("TOTISH_RESULT_WORKER_MTIME", "9700")
    monkeypatch.setattr("sys.argv", ["send_morning_digest.py", "--dry-run", "--container-state", "running|true|false"])

    assert cli.main() == 0
    assert captured["local_health"] == {"status": "ok"}
    assert captured["db_health"] == {"db": "ok"}
    assert captured["host_now_epoch"] == "10000"
    assert captured["deadline_worker_mtime"] == "9700"
    assert captured["result_worker_mtime"] == "9700"
    assert capsys.readouterr().out.strip() == "preview"


def test_public_enqueue_helper_keeps_existing_outbox_behavior():
    with patch.object(notifier, "_enqueue_message", return_value=True) as enqueue:
        assert notifier.enqueue_telegram_message("digest") is True
    enqueue.assert_called_once_with("digest")


def test_runner_has_moscow_date_idempotency_and_flock_contract():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_morning_digest.sh").read_text(encoding="utf-8")

    assert "TZ=Europe/Moscow date +%F" in source
    assert "SKIP already sent" in source
    assert "flock" in source
    assert "! DRY_RUN" in source
    assert "$HOME/.local/state/totish" in source
    assert "morning-digest.log" in source
    assert "morning-digest-state" in source
    assert "morning-digest.lock" in source


def test_runner_checks_health_inside_named_production_container():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_morning_digest.sh").read_text(encoding="utf-8")

    assert "docker exec" not in source  # The wrapper uses the resolved Docker binary.
    assert '"$DOCKER_BIN" exec football-totalizator-app-1' in source
    assert "http://127.0.0.1:8000/health" in source
    assert "http://127.0.0.1:8000/health/db" in source
    assert "http://app:8000" not in source
    assert "MORNING_DIGEST_LOCAL_HEALTH_JSON" in source
    assert "MORNING_DIGEST_DB_HEALTH_JSON" in source
    assert "stat -c %Y /var/log/totish-deadline-push.log" in source
    assert "stat -c %Y /var/log/totish-match-result-push.log" in source
    assert "TOTISH_DIGEST_HOST_NOW_EPOCH" in source
    assert "TOTISH_DEADLINE_WORKER_MTIME" in source
    assert "TOTISH_RESULT_WORKER_MTIME" in source


def test_runner_uses_production_image_fallback_when_primary_container_is_missing():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_morning_digest.sh").read_text(encoding="utf-8")

    assert "resolve_fallback_image" in source
    assert "TOTISH_DEPLOY_STATE_DIR" in source
    assert "target_image=" in source
    assert "image ls --format" in source
    assert "echo missing" in source
    assert source.index("resolve_fallback_image") < source.index("no production image fallback is available")
