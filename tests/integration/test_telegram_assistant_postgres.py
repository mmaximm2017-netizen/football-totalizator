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
        # Use definitions from the authoritative safe-schema file, not hand-made mocks.
        definitions = re.findall(r'CREATE VIEW gpt_safe\.\w+ AS.*?;', (ROOT / 'docs/totish_gpt_safe_schema.sql').read_text(), re.S)
        assert len(definitions) == 4
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
    assert lines == ['1. Бек: 10 очк. (1 прогнозов)', '2. Макс Зенит: 7 очк. (1 прогнозов)']
    assert calculate_points(2, 1, 2, 1) == canonical[0]['points']
    assert calculate_points(2, 1, 1, 0) == canonical[1]['points']


def test_end_to_end_clarifications_and_date_aggregate(analytics):
    assert 'Какой турнир' in a.answer('Сколько очков набрал Бек?', 'one')['text']
    assert 'Бек: 10' in a.answer('РПЛ', 'one')['text']
    assert 'Бек: 11' in a.answer('Кубок России', 'one')['text']
    assert 'Макс Зенит: 7' in a.answer('Очки игроков в девятом туре РПЛ', 'two')['text']
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
        (21,5,'Локомотив','Краснодар',CURRENT_TIMESTAMP-interval '5 days',CURRENT_TIMESTAMP-interval '6 days','FINISHED',2,1,7),
        (22,5,'Зенит','ЦСКА',CURRENT_TIMESTAMP-interval '4 days',CURRENT_TIMESTAMP-interval '5 days','FINISHED',0,0,7),
        (23,5,'Зенит','Краснодар',CURRENT_TIMESTAMP-interval '3 days',CURRENT_TIMESTAMP-interval '4 days','FINISHED',3,0,8),
        (24,5,'Локомотив','ЦСКА',CURRENT_TIMESTAMP-interval '2 days',CURRENT_TIMESTAMP-interval '3 days','FINISHED',1,0,NULL)
    """)
    source.execute("""
        INSERT INTO predictions(user_id,match_id,tournament_id,home_goals,away_goals,points) VALUES
        (1,21,5,2,1,10),(2,21,5,2,1,10),(1,22,5,2,1,0),
        (1,23,5,0,1,0),(2,23,5,3,0,11),(1,24,5,1,0,10)
    """)
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
    lines, _ = a.execute_question(safe, a.Question(intent='streak', round_number=7), 5, datetime.now(timezone.utc))
    assert 'Бек: 1 матчей' in lines and 'Макс Зенит: 1 матчей' in lines


def test_average_record_hits_and_compare_use_stored_finished_points(round_analytics):
    safe, _ = round_analytics
    now = datetime.now(timezone.utc)
    average, _ = a.execute_question(safe, a.Question(intent='average', round_number=7), 5, now)
    assert 'Бек: 5.00 очк. (2 прогнозов)' in average
    assert 'Макс Зенит: 10.00 очк. (1 прогнозов)' in average
    record, _ = a.execute_question(safe, a.Question(intent='record', round_number=7), 5, now)
    assert 'Бек: 10 очк.' in record
    hits, _ = a.execute_question(safe, a.Question(intent='hits', round_number=8, points=-1), 5, now)
    assert 'Макс Зенит: 1 раз' in hits
    tens, _ = a.execute_question(safe, a.Question(intent='hits', round_number=8, points=10), 5, now)
    assert 'Макс Зенит: 0 раз' in tens
    compare, _ = a.execute_question(safe, a.Question(intent='compare', round_number=7, players=['Бек', 'Макс Зенит']), 5, now)
    assert any('Бек\n10 очк. · среднее 5.00' in line for line in compare)
    assert any('Макс Зенит\n10 очк. · среднее 10.00' in line for line in compare)


def test_new_question_after_unsupported_screenshot_is_now_answered(round_analytics):
    result = a.answer('Кто сколько раз выигрывал туры РПЛ', 'screenshot')['text']
    assert 'Макс Зенит: 2 побед' in result
    comparison = a.answer('Сравни Бек и Макс Зенит в РПЛ', 'compare')['text']
    assert 'Сравнение' in comparison
    assert a.SESSIONS['compare'][1].teams == []
