import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MUTATION_SQL = re.compile(r"\b(?:INSERT\s+INTO|UPDATE\s+[a-z_]+|DELETE\s+FROM)\b", re.IGNORECASE)
RISKY_ROUTES = (
    "app/routes/main.py",
    "app/routes/auth.py",
    "app/routes/admin_actions.py",
    "app/routes/admin_tournaments.py",
)


def test_risky_routes_do_not_embed_mutation_sql():
    for relative_path in RISKY_ROUTES:
        text = (ROOT / relative_path).read_text(encoding="utf-8-sig")
        assert not MUTATION_SQL.search(text), relative_path


def test_prediction_deadline_write_lives_in_service():
    text = (ROOT / "app/services/prediction_write_service.py").read_text(encoding="utf-8")
    assert "INSERT INTO predictions" in text
    assert "CURRENT_TIMESTAMP < m.deadline" in text
    assert "ON CONFLICT (user_id, match_id, tournament_id)" in text


def test_auth_and_admin_mutations_live_in_services():
    auth = (ROOT / "app/services/auth_user_service.py").read_text(encoding="utf-8")
    titles = (ROOT / "app/services/admin_title_service.py").read_text(encoding="utf-8")
    tournaments = (ROOT / "app/services/admin_tournament_mutation_service.py").read_text(encoding="utf-8")
    assert "INSERT INTO users" in auth
    assert "UPDATE users" in auth
    assert "INSERT INTO user_titles" in titles
    assert "DELETE FROM user_titles" in titles
    assert "UPDATE tournaments" in tournaments
    assert "DELETE FROM tournaments" in tournaments
