from unittest.mock import MagicMock

import pytest

from app import gpt_db


@pytest.fixture(autouse=True)
def reset_pool():
    gpt_db.reset_gpt_pool()
    yield
    gpt_db.reset_gpt_pool()


def test_gpt_db_fails_closed_without_dedicated_url(monkeypatch):
    monkeypatch.delenv("TOTISH_GPT_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary-must-not-be-used")

    with pytest.raises(gpt_db.GPTDatabaseUnavailable):
        gpt_db.get_gpt_db()


def test_gpt_db_uses_only_dedicated_url_and_enforces_read_only(monkeypatch):
    connection, cursor, pool = MagicMock(), MagicMock(), MagicMock()
    connection.closed = False
    connection.cursor.return_value.__enter__.return_value = cursor
    pool.getconn.return_value = connection
    monkeypatch.setenv("TOTISH_GPT_DATABASE_URL", "postgresql://gpt-reader")
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary-must-not-be-used")
    monkeypatch.setattr(gpt_db, "ThreadedConnectionPool", lambda **kwargs: pool)

    result = gpt_db.get_gpt_db()

    assert result is connection
    assert gpt_db._pool is pool
    connection.set_session.assert_called_once_with(readonly=True, autocommit=False)
    assert cursor.execute.call_args_list[0].args == ("SET default_transaction_read_only = on",)
    assert cursor.execute.call_args_list[1].args == ("SET statement_timeout = %s", (10_000,))
    assert pool.getconn.called
    assert "primary-must-not-be-used" not in str(pool.mock_calls)


def test_gpt_db_close_rolls_back_without_committing(monkeypatch):
    connection, pool = MagicMock(), MagicMock()
    connection.closed = False
    gpt_db._pool = pool

    gpt_db.close_gpt_db(connection)

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    pool.putconn.assert_called_once_with(connection, close=False)


def test_gpt_setup_failure_discards_checked_out_connection(monkeypatch):
    connection, pool = MagicMock(), MagicMock()
    connection.closed = False
    connection.set_session.side_effect = gpt_db.OperationalError("setup failed")
    pool.getconn.return_value = connection
    monkeypatch.setenv("TOTISH_GPT_DATABASE_URL", "postgresql://gpt-reader")
    monkeypatch.setattr(gpt_db, "ThreadedConnectionPool", lambda **kwargs: pool)

    with pytest.raises(gpt_db.GPTDatabaseUnavailable):
        gpt_db.get_gpt_db()

    pool.putconn.assert_called_once_with(connection, close=True)
