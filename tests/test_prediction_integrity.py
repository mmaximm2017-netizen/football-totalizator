import inspect
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from app import db
from app.routes import predictions as prediction_routes
from app.routes import profile
from app.routes.main import main_bp


class IntegrityCursor:
    def __init__(self, preflight):
        self.preflight = preflight
        self.queries = []
        self.preflight_read = False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        if not self.preflight_read:
            self.preflight_read = True
            return self.preflight
        return None

    def fetchall(self):
        return []


class PredictionIntegrityMigrationTests(unittest.TestCase):
    def test_clean_preflight_adds_only_additive_no_action_constraints(self):
        prediction_snapshot = [(1, 10, 5, 2, 1, 7), (2, 11, 6, 0, 0, 10)]
        cur = IntegrityCursor((0, 0, 0, 0, 0, 0))

        db.ensure_prediction_integrity_constraints(cur)

        self.assertEqual(prediction_snapshot, [(1, 10, 5, 2, 1, 7), (2, 11, 6, 0, 0, 10)])
        sql = "\n".join(query for query, _ in cur.queries)
        self.assertIn("LOCK TABLE predictions", sql)
        self.assertIn("NOT VALID", sql)
        self.assertIn("VALIDATE CONSTRAINT predictions_user_fk", sql)
        self.assertIn("idx_predictions_match_tournament", sql)
        self.assertNotIn("ON DELETE CASCADE", sql)
        self.assertNotIn("DELETE FROM predictions", sql)
        self.assertNotIn("UPDATE predictions", sql)

    def test_invalid_preflight_stops_before_constraint_ddl_and_preserves_rows(self):
        prediction_snapshot = [(1, 10, 5, 2, 1, 7), (1, 10, 6, 2, 1, 7)]
        cur = IntegrityCursor((0, 0, 0, 0, 0, 1))

        with self.assertRaisesRegex(RuntimeError, "tournament_mismatches=1"):
            db.ensure_prediction_integrity_constraints(cur)

        self.assertEqual(prediction_snapshot, [(1, 10, 5, 2, 1, 7), (1, 10, 6, 2, 1, 7)])
        sql = "\n".join(query for query, _ in cur.queries)
        self.assertNotIn("ALTER TABLE predictions", sql)
        self.assertNotIn("CREATE INDEX", sql)

    def test_controlled_migration_commits_clean_preflight_without_changing_predictions(self):
        prediction_snapshot = [(1, 10, 5, 2, 1, 7)]
        conn = Mock()
        cur = IntegrityCursor((0, 0, 0, 0, 0, 0))
        conn.cursor.return_value = cur

        with patch.object(db, "get_db", return_value=conn), patch.object(db, "close_db"):
            db.migrate_prediction_integrity()

        self.assertEqual(prediction_snapshot, [(1, 10, 5, 2, 1, 7)])
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()


class PredictionWriteValidationTests(unittest.TestCase):
    def test_post_rejects_match_from_another_tournament_without_upsert(self):
        class Cursor:
            def __init__(self):
                self.queries = []

            def execute(self, query, params=None):
                self.queries.append((query, params))

            def fetchone(self):
                return (10, "Home", "Away", None, None, "SCHEDULED", 6)

        class Connection:
            def __init__(self):
                self.cursor_value = Cursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.commits += 1

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")
        conn = Connection()

        with (  # noqa: SIM117 - client lifecycle must remain inside patched application dependencies.
            patch("app.routes.main.get_db", return_value=conn),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[]),
            patch("app.routes.main.get_selected_tournament_id", return_value=5),
        ):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session["user_id"] = 1
                    session["selected_tournament_id"] = 5
                response = client.post(
                    "/",
                    data={"match_id": "10", "home_goals": "1", "away_goals": "0"},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.is_json)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(any("INSERT INTO predictions" in query for query, _ in conn.cursor_value.queries))


class PredictionScopedJoinTests(unittest.TestCase):
    def test_prediction_match_joins_include_tournament_key(self):
        profile_source = inspect.getsource(profile)
        self.assertIn("m.status = 'FINISHED'", profile_source)
        self.assertIn("m.tournament_id = p.tournament_id", profile_source)
        self.assertIn("AND p.tournament_id = m.tournament_id", inspect.getsource(prediction_routes))

        from app.services import scoring_recalculation_service

        source = inspect.getsource(scoring_recalculation_service)
        self.assertIn("AND p.tournament_id = m.tournament_id", source)

    def test_delete_routes_protect_predictions_instead_of_deleting_them(self):
        from app.routes import admin_matches, admin_tournaments

        source = inspect.getsource(admin_matches)
        self.assertIn("Нельзя удалить матч: существуют связанные прогнозы", source)
        self.assertNotIn("DELETE FROM predictions\n            WHERE match_id", source)
        self.assertIn("Нельзя удалить турнир: существуют связанные прогнозы", inspect.getsource(admin_tournaments))


if __name__ == "__main__":
    unittest.main()
