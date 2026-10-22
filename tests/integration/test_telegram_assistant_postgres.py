"""Execute the actual SELECT templates and safe views on CI's disposable PostgreSQL."""
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from app.models.scoring import calculate_points
from app.services import telegram_sql_assistant as a
from app.services import telegram_bot_ui_service as ui
from app.services.ranking_service import _fetch_ranking

ROOT = Path(__file__).resolve().parents[2]


class SafeCursor:
    def __init__(self, cur, schema):
        self.cur = cur
        self.schema = schema

    def execute(self, statement, params=()):
        self.cur.execute(statement.replace('gpt_safe.', f'"{self.schema}".'), params)

    def fetchall(self):
        return self.cur.fetchall()


@pytest.fixture
def analytics(monkeypatch):
    dsn = os.getenv('AUTO_RESULTS_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit disposable PostgreSQL DSN required; enabled in CI')
    conn = psycopg2.connect(dsn)
    source = 'assistant_source_' + uuid.uuid4().hex
    safe = 'assistant_safe_' + uuid.uuid4().hex
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE SCHEMA "{source}"; CREATE SCHEMA "{safe}"; SET LOCAL search_path TO "{source}"')
        for path in sorted((ROOT / 'migrations').glob('*.sql')):
            cur.execute(path.read_text())
        cur.execute("INSERT INTO tournaments(id,name,is_active) VALUES(5,'Чемпионат России 🇷🇺',1),(6,'Кубок России',1)")
        cur.execute("INSERT INTO users(id,username,password,is_admin,is_deleted) VALUES (1,'Бек','secret',0,0),(2,'Макс Зенит','secret',0,0),(3,'admin','secret',1,0),(4,'deleted','secret',0,1)")
        cur.execute("""
            INSERT INTO matches(id,tournament_id,home_team,away_team,kickoff_time,deadline,status,home_score,away_score,round_number)
            VALUES
              (11,5,'Локомотив','Краснодар',CURRENT_TIMESTAMP-interval '1 day',CURRENT_TIMESTAMP-interval '26 hours','FINISHED',2,1,9),
              (12,5,'Зенит','ЦСКА',CURRENT_TIMESTAMP-interval '1 hour',CURRENT_TIMESTAMP-interval '3 hours','LIVE',3,0,9),
              (13,5,'Локомотив','Краснодар',CURRENT_TIMESTAMP+interval '1 day',CURRENT_TIMESTAMP+interval '22 hours','SCHEDULED',NULL,NULL,10),
              (14,5,'Зенит','ЦСКА',CURRENT_TIMESTAMP-interval '1 hour',NULL,'LIVE',NULL,NULL,9),
              (15,6,'Локомотив','Краснодар',CURRENT_TIMESTAMP-interval '1 day',CURRENT_TIMESTAMP-interval '26 hours','FINISHED',3,0,9)
        """)
        cur.execute("""
            INSERT INTO predictions(user_id,match_id,tournament_id,home_goals,away_goals,points) VALUES
            (1,11,5,2,1,10),(2,11,5,1,0,7),(3,11,5,2,1,10),(4,11,5,2,1,10),
            (1,12,5,3,0,999),(1,13,5,9,9,999),(1,14,5,8,8,999),(1,15,6,3,0,11)
        """)
        cur.execute("UPDATE matches SET league=CASE WHEN tournament_id=5 THEN 'rpl' ELSE 'rcup' END")
        # Use definitions from the authoritative safe-schema file, not hand-made mocks.
        definitions = re.findall(r'CREATE VIEW gpt_safe\.\w+ AS.*?;', (ROOT / 'docs/totish_gpt_safe_schema.sql').read_text(), re.S)
        assert len(definitions) == 6
        for ddl in definitions:
            cur.execute(ddl.replace('gpt_safe.', f'"{safe}".').replace('public.', f'"{source}".'))
        cursor = SafeCursor(cur, safe)

        @contextmanager
        def reader():
            yield cursor

        monkeypatch.setattr(a, 'short_gpt_read', reader)
        a.SESSIONS.clear()
        yield cursor, cur
    finally:
        a.SESSIONS.clear()
        conn.rollback()  # DDL and fixtures vanish together; no production or persistent changes.
        cur.close()
        conn.close()


def test_ranking_matches_canonical_and_excludes_unfinished_and_other_tournament(analytics):
    safe, source = analytics
    canonical = _fetch_ranking(source, 5, True)
    rows = a.read(safe, 'SELECT username, points FROM gpt_safe.predictions WHERE tournament_id = %s AND status = %s', (5, 'FINISHED'))
    assert rows == [('Бек', 10), ('Макс Зенит', 7)]
    lines, _ = a.execute_question(safe, a.Question(intent='ranking'), 5, datetime.now(timezone.utc))
    assert [row['username'] for row in canonical] == ['Бек', 'Макс Зенит']
    assert lines == ['1. Бек: 10 очк.', '2. Макс Зенит: 7 очк.']
    assert calculate_points(2, 1, 2, 1) == canonical[0]['points']
    assert calculate_points(2, 1, 1, 0) == canonical[1]['points']


@pytest.fixture
def guided_ui(analytics, monkeypatch):
    safe, _ = analytics

    @contextmanager
    def reader():
        yield safe

    monkeypatch.setattr(ui, 'short_gpt_read', reader)
    return analytics


def test_guided_player_card_uses_canonical_order_and_tournament_scope(guided_ui):
    rpl = ui.render('ui:v:rpl:card:all:2:0')['text']
    assert 'Макс Зенит' in rpl and 'Место: 2 из 2' in rpl
    assert 'Очки: 7' in rpl and 'До лидера: 3 очк.' in rpl
    cup = ui.render('ui:v:cup:card:all:1:0')['text']
    assert 'Очки: 11' in cup and 'Место: 1 из 2' in cup
    assert '999' not in rpl + cup


def test_guided_recent_matches_hide_live_future_and_other_tournament(guided_ui):
    result = ui.render('ui:v:rpl:recent:all:1:0')['text']
    assert 'Локомотив — Краснодар' in result and '10 очк.' in result
    assert 'Зенит — ЦСКА' not in result and '999' not in result and '11 очк.' not in result


def test_guided_personal_predictions_only_show_selected_player_after_deadline(guided_ui):
    result = ui.render('ui:v:rpl:predictions:yesterday:1:0')['text']
    assert 'Бек: 2:1' in result and 'Макс Зенит:' not in result
    tomorrow = (datetime.now(a.MSK).date() + a.timedelta(days=1)).strftime('%Y%m%d')
    result = ui.render(f'ui:v:rpl:predictions:d{tomorrow}-{tomorrow}:1:0')['text']
    assert 'закрыты' in result and '9:9' not in result


def test_guided_round_picker_and_totals_use_derived_calendar(guided_ui):
    result = ui.render('ui:r:rpl:points:all:0:0')
    choices = [b['callback_data'] for row in result['rows'] for b in row if b['callback_data'].startswith('ui:v:')]
    assert choices == ['ui:v:rpl:points:r1:0:0']
    result = ui.render(choices[0])['text']
    assert 'Тур 1' in result and 'Бек: 10' in result and 'Макс Зенит: 7' in result


def test_end_to_end_clarifications_and_date_aggregate(analytics):
    assert 'Какой турнир' in a.answer('Сколько очков набрал Бек?', 'one')['text']
    assert 'Бек: 10' in a.answer('РПЛ', 'one')['text']
    assert 'Бек: 11' in a.answer('Кубок России', 'one')['text']
    assert 'Макс Зенит: 7' in a.answer('Очки игроков в первом туре РПЛ', 'two')['text']
    yesterday = analytics[1]
    yesterday.execute("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date - 1")
    day = yesterday.fetchone()[0].isoformat()
    result = a.answer(f'Кто сколько набрал за {day} в РПЛ?', 'three')['text']
    assert 'Бек: 10' in result and 'Макс Зенит: 7' in result


def test_deadline_views_and_no_cross_tournament_missing_predictions(analytics):
    safe, _ = analytics
    rows = a.read(safe, 'SELECT match_id FROM gpt_safe.predictions WHERE match_id = ANY(%s)', ([13, 14],))
    assert rows == []  # Future deadline and absent deadline both fail closed in DB.
    q = a.Question(intent='predictions', match_id=13)
    lines, _ = a.execute_question(safe, q, 5, datetime.now(timezone.utc))
    assert 'закрыты' in '\n'.join(lines) and '9:9' not in '\n'.join(lines)
    q = a.Question(intent='missing', match_id=15)
    lines, _ = a.execute_question(safe, q, 6, datetime.now(timezone.utc))
    assert 'Макс Зенит: нет прогноза' in lines
    assert 'Бек: нет прогноза' not in lines


def test_last_exact_and_match_selection(analytics):
    result = a.answer('Когда Бек последний раз получал 10 очков в РПЛ?', 'one')['text']
    assert 'Локомотив — Краснодар: 10 очк.' in result
    exact = a.answer('Кто угадал точный счёт в РПЛ?', 'two')['text']
    assert 'Бек' in exact and 'Макс Зенит' not in exact
    choices = a.answer('Кто поставил на Локомотив — Краснодар в РПЛ?', 'three')['text']
    assert 'Выберите номер матча' in choices
    selected = a.answer('1', 'three')['text']
    assert 'Бек: 2:1' in selected and 'Макс Зенит: 1:0' in selected


def test_real_transaction_readonly_rejects_mutation(analytics):
    _, cur = analytics
    # Start a fresh read-only transaction after the fixture transaction is rolled back.
    conn = psycopg2.connect(os.environ['AUTO_RESULTS_TEST_DATABASE_URL'])
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as c:
            c.execute('SELECT 1')
            assert c.fetchone() == (1,)
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                c.execute('CREATE TABLE must_not_exist (id int)')
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def round_analytics(analytics):
    safe, source = analytics
    source.execute("""
        INSERT INTO matches(id,tournament_id,home_team,away_team,kickoff_time,deadline,status,home_score,away_score,round_number) VALUES
        (21,5,'Локомотив','Краснодар',CURRENT_TIMESTAMP-interval '12 days',CURRENT_TIMESTAMP-interval '13 days','FINISHED',2,1,7),
        (22,5,'Зенит','ЦСКА',CURRENT_TIMESTAMP-interval '11 days',CURRENT_TIMESTAMP-interval '12 days','FINISHED',0,0,7),
        (23,5,'Зенит','Краснодар',CURRENT_TIMESTAMP-interval '8 days',CURRENT_TIMESTAMP-interval '9 days','FINISHED',3,0,8),
        (24,5,'Локомотив','ЦСКА',CURRENT_TIMESTAMP-interval '5 days',CURRENT_TIMESTAMP-interval '6 days','FINISHED',1,0,NULL)
    """)
    source.execute("""
        INSERT INTO predictions(user_id,match_id,tournament_id,home_goals,away_goals,points) VALUES
        (1,21,5,2,1,10),(2,21,5,2,1,10),(1,22,5,2,1,0),
        (1,23,5,0,1,0),(2,23,5,3,0,11),(1,24,5,1,0,10)
    """)
    source.execute("UPDATE matches SET league='rpl' WHERE id IN (21,22,23,24)")
    source.execute("UPDATE matches SET match_category='supercup' WHERE id=24")
    return safe, source


def test_round_wins_shared_first_full_round_only_and_tournament_isolation(round_analytics):
    safe, _ = round_analytics
    now = datetime.now(timezone.utc)
    lines, _ = a.execute_question(safe, a.Question(intent='round_wins'), 5, now)
    assert 'Учтено туров с прогнозами: 2.' in lines
    assert '1. Макс Зенит: 2 побед' in lines
    assert '2. Бек: 1 побед' in lines
    cup, _ = a.execute_question(safe, a.Question(intent='round_wins'), 6, now)
    assert '1. Бек: 1 побед' in cup
    assert '2. Макс Зенит: 0 побед' in cup


def test_round_date_filter_never_completes_partial_round(round_analytics):
    safe, source = round_analytics
    source.execute("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date - 1")
    day = source.fetchone()[0].isoformat()
    lines, _ = a.execute_question(safe, a.Question(intent='round_wins', dates=[day]), 5, datetime.now(timezone.utc))
    assert 'Завершённых туров с начисленными прогнозами нет.' in lines


def test_round_wins_reject_invalid_points_and_future_deadline(round_analytics):
    safe, source = round_analytics
    source.execute('UPDATE predictions SET points=999 WHERE match_id=21 AND user_id=1')
    source.execute("UPDATE matches SET deadline=CURRENT_TIMESTAMP+interval '1 day' WHERE id=23")
    lines, _ = a.execute_question(safe, a.Question(intent='round_wins'), 5, datetime.now(timezone.utc))
    assert 'Завершённых туров с начисленными прогнозами нет.' in lines


def test_streak_counts_missing_prediction_and_stops_at_exact(round_analytics):
    safe, _ = round_analytics
    lines, _ = a.execute_question(safe, a.Question(intent='streak', round_number=1), 5, datetime.now(timezone.utc))
    assert 'Бек: 1 матчей' in lines and 'Макс Зенит: 1 матчей' in lines


def test_average_record_hits_and_compare_use_stored_finished_points(round_analytics):
    safe, _ = round_analytics
    now = datetime.now(timezone.utc)
    average, _ = a.execute_question(safe, a.Question(intent='average', round_number=1), 5, now)
    assert 'Бек: 5.00 очк. (2 прогнозов)' in average
    assert 'Макс Зенит: 10.00 очк. (1 прогнозов)' in average
    record, _ = a.execute_question(safe, a.Question(intent='record', round_number=1), 5, now)
    assert 'Бек: 10 очк.' in record
    hits, _ = a.execute_question(safe, a.Question(intent='hits', round_number=2, points=-1), 5, now)
    assert 'Макс Зенит: 1 раз' in hits
    tens, _ = a.execute_question(safe, a.Question(intent='hits', round_number=2, points=10), 5, now)
    assert 'Макс Зенит: 0 раз' in tens
    compare, _ = a.execute_question(safe, a.Question(intent='compare', round_number=1, players=['Бек', 'Макс Зенит']), 5, now)
    assert any('Бек\n10 очк. · среднее 5.00' in line for line in compare)
    assert any('Макс Зенит\n10 очк. · среднее 10.00' in line for line in compare)


def test_new_question_after_unsupported_screenshot_is_now_answered(round_analytics):
    result = a.answer('Кто сколько раз выигрывал туры РПЛ', 'screenshot')['text']
    assert 'Макс Зенит: 2 побед' in result
    comparison = a.answer('Сравни Бек и Макс Зенит в РПЛ', 'compare')['text']
    assert 'Сравнение' in comparison
    assert a.SESSIONS['compare'][1].teams == []


def test_calendar_rounds_ignore_stored_numbers_national_team_and_supercup(analytics):
    safe, cur = analytics
    cur.execute("INSERT INTO tournaments(id,name) VALUES(7,'RPL calendar test'),(8,'Cup calendar test')")
    entries = [
        (1001,7,'rpl','rpl','A','B','2026-07-31 22:30:00+00',None),  # 1 Aug Moscow
        (1002,7,'rpl','rpl','C','D','2026-08-02 12:00:00+00',None),
        (1003,7,'rpl','rpl','Россия 🇷🇺','Иран','2026-08-03 12:00:00+00',None),
        (1004,7,'rpl','rpl','E','F','2026-08-04 12:00:00+00',None),
        (1005,7,'rpl','supercup','G','H','2026-08-03 12:00:00+00',None),
        (1006,7,'rpl','rpl','I','J','2026-08-03 12:00:00+00','Суперкубок'),
        (1007,7,'rpl','national_team','K','L','2026-08-03 12:00:00+00',None),
        (1008,7,'rpl','rpl','M','N',None,None),
        (1011,8,'rcup','rpl','A','B','2026-08-01 12:00:00+00',None),
        (1012,8,'rcup','rpl','C','D','2026-08-03 12:00:00+00',None),
        (1013,8,'rcup','national_team','K','L','2026-08-02 12:00:00+00',None),
    ]
    for mid, tid, league, category, home, away, kickoff, stage in entries:
        cur.execute('INSERT INTO matches(id,tournament_id,league,match_category,home_team,away_team,kickoff_time,playoff_stage,round_number) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,99)', (mid,tid,league,category,home,away,kickoff,stage))
    rows = a.read(safe, 'SELECT match_id, tournament_id, round_number FROM gpt_safe.calendar_rounds WHERE tournament_id IN (7,8) ORDER BY match_id')
    assert rows == [(1001,7,1),(1002,7,1),(1004,7,2),(1011,8,1),(1012,8,2)]
    cur.execute('SELECT DISTINCT round_number FROM matches WHERE tournament_id IN (7,8)')
    assert cur.fetchall() == [(99,)]  # View never rewrites stored metadata.


def test_agreed_production_date_ranges_form_17_rpl_and_6_cup_rounds(analytics):
    from datetime import date, timedelta
    safe, cur = analytics
    cur.execute("INSERT INTO tournaments(id,name) VALUES(7,'RPL approved calendar'),(8,'Cup approved calendar')")
    ranges = {
        7: [('07-24','07-26'),('07-31','08-02'),('08-08','08-10'),('08-14','08-16'),('08-22','08-24'),('08-28','08-30'),('09-05','09-07'),('09-11','09-13'),('09-16','09-17'),('10-09','10-11'),('10-17','10-19'),('10-23','10-25'),('10-31','11-02'),('11-06','11-08'),('11-20','11-22'),('11-28','11-30'),('12-04','12-06')],
        8: [('08-04','08-05'),('08-18','08-20'),('09-01','09-03'),('10-13','10-15'),('10-27','10-29'),('11-24','11-26')],
    }
    mid = 2000
    for tid, blocks in ranges.items():
        for first, last in blocks:
            day, end = date.fromisoformat('2026-'+first), date.fromisoformat('2026-'+last)
            while day <= end:
                mid += 1
                cur.execute("INSERT INTO matches(id,tournament_id,league,match_category,home_team,away_team,kickoff_time,round_number) VALUES(%s,%s,%s,'rpl','A','B',%s,99)", (mid,tid,'rpl' if tid==7 else 'rcup',f'{day} 12:00:00+03'))
                day += timedelta(days=1)
    rows = a.read(safe, 'SELECT tournament_id, round_number, MIN(match_date), MAX(match_date) FROM gpt_safe.calendar_rounds WHERE tournament_id IN (7,8) GROUP BY tournament_id, round_number ORDER BY tournament_id, round_number')
    expected = [(tid, i, date.fromisoformat('2026-'+first), date.fromisoformat('2026-'+last)) for tid, blocks in ranges.items() for i, (first,last) in enumerate(blocks,1)]
    assert rows == expected


def test_archive_table_includes_deleted_participant_and_matches_canonical(guided_ui):
    safe, source = guided_ui
    source.execute('UPDATE tournaments SET is_active = 0 WHERE id = 5')
    canonical = _fetch_ranking(source, 5, False)
    lines, _ = a.execute_question(safe, a.Question(intent='ranking'), 5, datetime.now(timezone.utc))
    assert len(lines) == len(canonical) == 3
    for index, row in enumerate(canonical, 1):
        assert lines[index - 1].startswith(f"{index}. {row['username']}: {row['points']} очк.")
    card = ui.render('ui:v:rpl:card:all:4:0')
    assert 'deleted' in card['text'] and 'Очки: 10' in card['text']
    assert 'Архивный участник' in card['text']
    callbacks = [button['callback_data'] for row in card['rows'] for button in row]
    assert not any(':predictions:' in value or ':recent:' in value for value in callbacks)
    source.execute("UPDATE tournaments SET is_active = 1 WHERE id = 5")
    lines, _ = a.execute_question(safe, a.Question(intent='ranking'), 5, datetime.now(timezone.utc))
    assert len(lines) == 2 and not any('deleted' in line for line in lines)


def test_aggregate_rankings_do_not_reveal_future_or_null_deadline_predictions(analytics):
    safe, source = analytics
    source.execute("UPDATE matches SET status='FINISHED', home_score=9, away_score=9, kickoff_time=CURRENT_TIMESTAMP-interval '1 hour' WHERE id IN (13,14)")
    source.execute('UPDATE tournaments SET is_active = 0 WHERE id = 5')
    rows = a.read(safe, 'SELECT user_id, points FROM gpt_safe.tournament_rankings WHERE tournament_id = %s ORDER BY user_id', (5,))
    assert rows == [(1, 10), (2, 7), (4, 10)]
