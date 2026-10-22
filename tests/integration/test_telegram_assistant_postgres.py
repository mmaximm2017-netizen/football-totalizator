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
