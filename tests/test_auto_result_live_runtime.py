from pathlib import Path

import pytest

from app.services import auto_result_finalization_service as service


ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, row, update_rowcount=1):
        self.row = row
        self.update_rowcount = update_rowcount
        self.rowcount = -1
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if "UPDATE matches" in sql:
            self.rowcount = self.update_rowcount

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def install_db(monkeypatch, row, update_rowcount=1):
    cursor = FakeCursor(row, update_rowcount=update_rowcount)
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_db", lambda: connection)
    monkeypatch.setattr(service, "close_db", lambda conn, cur: None)
    return connection, cursor


def test_finalize_auto_result_saves_only_untouched_match(monkeypatch):
    connection, cursor = install_db(monkeypatch, ("SCHEDULED", None, None, 5, "rpl"))
    recalculated = []
    monkeypatch.setattr(
        service,
        "recalc_match_points",
        lambda match_id, tournament_id, conn, cur: recalculated.append((match_id, tournament_id)),
    )

    outcome = service.finalize_auto_result(401, 2, 1, tournament_id=5, league="rpl")

    assert outcome == "saved"
    assert connection.commits == 1
    assert recalculated == [(401, 5)]
    update_sql, update_params = next(item for item in cursor.executed if "UPDATE matches" in item[0])
    assert "status = 'FINISHED'" in update_sql
    assert "result_origin = %s" in update_sql
    assert "home_score IS NULL" in update_sql
    assert update_params[:3] == (2, 1, "auto_result_worker")


def test_finalize_auto_result_never_overwrites_manual_score(monkeypatch):
    connection, cursor = install_db(monkeypatch, ("FINISHED", 1, 0, 5, "rpl"))
    monkeypatch.setattr(service, "recalc_match_points", lambda *args, **kwargs: pytest.fail("must not recalc"))

    outcome = service.finalize_auto_result(401, 2, 1, tournament_id=5, league="rpl")

    assert outcome == "already_done"
    assert connection.commits == 0
    assert not any("UPDATE matches" in sql for sql, _ in cursor.executed)


def test_finalize_auto_result_rejects_changed_scope(monkeypatch):
    install_db(monkeypatch, ("SCHEDULED", None, None, 6, "rcup"))
    with pytest.raises(service.AutoResultFinalizeError, match="match_scope_changed"):
        service.finalize_auto_result(401, 2, 1, tournament_id=5, league="rpl")


def test_auto_result_origin_migration_clears_marker_on_later_manual_change():
    sql = (ROOT / "migrations" / "0004_add_auto_result_origin.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS result_origin" in sql
    assert "OLD.home_score IS DISTINCT FROM NEW.home_score" in sql
    assert "NEW.result_origin := NULL" in sql
    assert "BEFORE UPDATE ON matches" in sql


def test_runtime_wrapper_does_not_force_dry_run():
    shell = (ROOT / "scripts" / "run_auto_results.sh").read_text(encoding="utf-8")
    assert "scripts/auto_result_runtime.py" in shell
    assert "-e AUTO_RESULTS_DRY_RUN=true" not in shell
