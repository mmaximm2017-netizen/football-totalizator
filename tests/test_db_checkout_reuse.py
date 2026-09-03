from pathlib import Path
from unittest.mock import patch

from app.services import tournament_service


ROOT = Path(__file__).resolve().parents[1]


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_active_tournament_reuses_supplied_cursor_without_new_checkout():
    cur = Cursor([(7, "РПЛ", 1, "2026-07-01", None)])

    with patch.object(tournament_service, "get_db", side_effect=AssertionError("unexpected checkout")):
        tournament = tournament_service.get_active_tournament(cur=cur)

    assert tournament["id"] == 7
    assert len(cur.executed) == 1


def test_active_tournament_helpers_reuse_supplied_cursor():
    cur = Cursor([(7, "РПЛ", 1, "2026-07-01", None)])

    with patch.object(tournament_service, "get_db", side_effect=AssertionError("unexpected checkout")):
        tournament_id = tournament_service.get_active_tournament_id(cur=cur)

    assert tournament_id == 7


def test_single_active_check_reuses_supplied_cursor():
    cur = Cursor([(1,)])

    with patch.object(tournament_service, "get_db", side_effect=AssertionError("unexpected checkout")):
        result = tournament_service.ensure_single_active_tournament(cur=cur)

    assert result == {"ok": True, "active_count": 1}


def test_health_check_reuses_one_cursor_across_database_reads():
    source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")

    assert "get_active_tournament_id(cur=cur)" in source
    assert "get_tournament_ranking(active_tid, cur=cur)" in source
    assert "ensure_single_active_tournament(cur=cur)" in source


def test_public_profile_paths_pass_their_cursor_to_shared_services():
    source = (ROOT / "app" / "routes" / "profile.py").read_text(encoding="utf-8")

    assert "_profile_tournament_context(cur=cur)" in source
    assert "_public_user(user_id, cur=cur)" in source
    assert "get_tournament_ranking(context[\"tournament_id\"], cur=cur)" in source
    assert "get_profile_stats(user_id, context[\"tournament_id\"], cur=cur)" in source


def test_telegram_ranking_reuses_tournament_lookup_cursor():
    source = (ROOT / "app" / "services" / "telegram_admin_service.py").read_text(
        encoding="utf-8"
    )

    assert "get_tournament_ranking(tournament[0], cur=cur)" in source
