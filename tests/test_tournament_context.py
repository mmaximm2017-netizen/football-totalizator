import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SERVICE_PATH = ROOT / "app" / "services" / "tournament_context_service.py"


def load_context_service_module():
    fake_app = types.ModuleType("app")
    fake_db = types.ModuleType("app.db")
    fake_services = types.ModuleType("app.services")
    fake_tournament_service = types.ModuleType("app.services.tournament_service")

    fake_db.close_db = lambda conn, cur=None: None
    fake_db.get_db = lambda: None
    fake_tournament_service.get_active_tournament = lambda: None
    fake_tournament_service.get_active_tournament_id = lambda: None
    fake_tournament_service.get_tournament_by_id = lambda tournament_id: None

    module_name = "tournament_context_service_under_test"
    spec = importlib.util.spec_from_file_location(module_name, CONTEXT_SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)

    with patch.dict(
        sys.modules,
        {
            "app": fake_app,
            "app.db": fake_db,
            "app.services": fake_services,
            "app.services.tournament_service": fake_tournament_service,
        },
    ):
        spec.loader.exec_module(module)

    return module


context_service = load_context_service_module()


class MatchCategoryNormalizationTests(unittest.TestCase):
    def test_supercup_category_is_normalized_for_ui(self):
        from app.routes.main import normalize_match_category

        self.assertEqual(normalize_match_category("supercup"), "supercup")
        self.assertEqual(normalize_match_category(" SuperCup "), "supercup")
        self.assertEqual(normalize_match_category("super_cup"), "supercup")
        self.assertEqual(normalize_match_category("super cup"), "supercup")

    def test_russia_category_is_normalized_for_ui(self):
        from app.routes.main import normalize_match_category

        self.assertEqual(normalize_match_category("russia"), "national_team")
        self.assertEqual(normalize_match_category(" national_team "), "national_team")


class FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        self.executed = []
        self.closed = False

    def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))
        return None

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None

    def fetchall(self):
        if self.fetchall_results:
            return self.fetchall_results.pop(0)
        return []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def fake_render_template(template_name, **context):
    from flask import jsonify

    return jsonify(
        {
            "template": template_name,
            "current_tournament_id": context.get("current_tournament_id"),
            "current_tournament_name": context.get("current_tournament_name"),
            "selected_tid": context.get("selected_tid"),
            "selected_name": context.get("selected_name"),
        }
    )


class SelectedTournamentHelperTests(unittest.TestCase):
    def test_valid_tid_wins(self):
        with (
            patch.object(context_service, "get_tournament_by_id", return_value={"id": 7}),
            patch.object(context_service, "get_nearest_upcoming_tournament_id") as nearest,
            patch.object(context_service, "get_first_active_tournament_id") as first_active,
            patch.object(context_service, "get_latest_tournament_id") as latest,
        ):
            self.assertEqual(context_service.get_selected_tournament_id(7), 7)
            nearest.assert_not_called()
            first_active.assert_not_called()
            latest.assert_not_called()

    def test_invalid_tid_falls_back_to_nearest_upcoming(self):
        with (
            patch.object(context_service, "get_tournament_by_id", return_value=None),
            patch.object(context_service, "get_nearest_upcoming_tournament_id", return_value=11),
            patch.object(context_service, "get_first_active_tournament_id", return_value=22),
            patch.object(context_service, "get_latest_tournament_id", return_value=33),
        ):
            self.assertEqual(context_service.get_selected_tournament_id(999), 11)

    def test_no_tid_uses_nearest_upcoming(self):
        with (
            patch.object(context_service, "get_tournament_by_id") as get_tournament,
            patch.object(context_service, "get_nearest_upcoming_tournament_id", return_value=2),
            patch.object(context_service, "get_first_active_tournament_id", return_value=3),
            patch.object(context_service, "get_latest_tournament_id", return_value=4),
        ):
            self.assertEqual(context_service.get_selected_tournament_id(None), 2)
            get_tournament.assert_not_called()

    def test_no_upcoming_uses_first_active(self):
        with (
            patch.object(context_service, "get_tournament_by_id") as get_tournament,
            patch.object(context_service, "get_nearest_upcoming_tournament_id", return_value=None),
            patch.object(context_service, "get_first_active_tournament_id", return_value=3),
            patch.object(context_service, "get_latest_tournament_id", return_value=4),
        ):
            self.assertEqual(context_service.get_selected_tournament_id(None), 3)
            get_tournament.assert_not_called()

    def test_no_active_uses_latest_tournament(self):
        with (
            patch.object(context_service, "get_tournament_by_id") as get_tournament,
            patch.object(context_service, "get_nearest_upcoming_tournament_id", return_value=None),
            patch.object(context_service, "get_first_active_tournament_id", return_value=None),
            patch.object(context_service, "get_latest_tournament_id", return_value=4),
        ):
            self.assertEqual(context_service.get_selected_tournament_id(None), 4)
            get_tournament.assert_not_called()

    def test_no_tournaments_returns_none(self):
        with (
            patch.object(context_service, "get_tournament_by_id") as get_tournament,
            patch.object(context_service, "get_nearest_upcoming_tournament_id", return_value=None),
            patch.object(context_service, "get_first_active_tournament_id", return_value=None),
            patch.object(context_service, "get_latest_tournament_id", return_value=None),
        ):
            self.assertIsNone(context_service.get_selected_tournament_id(None))
            get_tournament.assert_not_called()


class TournamentStateFlagsTests(unittest.TestCase):
    def test_normal_active_season(self):
        flags = context_service.get_tournament_state_flags(
            [{"id": 1, "is_active": 1}, {"id": 2, "is_active": 0}]
        )

        self.assertEqual(
            flags,
            {
                "has_any_tournament": True,
                "has_active_tournament": True,
                "is_offseason": False,
            },
        )

    def test_offseason(self):
        flags = context_service.get_tournament_state_flags(
            [{"id": 1, "is_active": 0}, {"id": 2, "is_active": False}]
        )

        self.assertEqual(
            flags,
            {
                "has_any_tournament": True,
                "has_active_tournament": False,
                "is_offseason": True,
            },
        )

    def test_no_tournaments(self):
        flags = context_service.get_tournament_state_flags([])

        self.assertEqual(
            flags,
            {
                "has_any_tournament": False,
                "has_active_tournament": False,
                "is_offseason": False,
            },
        )


try:
    from flask import Flask

    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    Flask = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this runtime")
class BaseTournamentThemeTests(unittest.TestCase):
    def make_template_app(self):
        app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        app.secret_key = "test-secret"
        return app

    def render_base(self, tournament_name):
        from flask import render_template

        app = self.make_template_app()
        with app.test_request_context("/"):
            return render_template(
                "base.html",
                current_tournament_name=tournament_name,
                current_tournament_id=42,
                tournaments=[],
                active_tournaments=[],
                csrf_token="test-csrf",
            )

    def test_russian_cup_uses_rcup_body_class_and_stylesheet(self):
        html = self.render_base("Кубок России")

        self.assertIn('class="tournament-rcup', html)
        self.assertIn("css/tournaments/russian-cup.css", html)
        self.assertNotIn('class="tournament-cup', html)

    def test_rpl_body_class_is_unchanged(self):
        html = self.render_base("Чемпионат России 🇷🇺")

        self.assertIn('class="tournament-rpl', html)
        self.assertNotIn('class="tournament-rcup', html)

    def test_navigation_uses_canonical_tournament_id_without_current_tid_alias(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn('href="/{% if current_tournament_id %}?tid={{ current_tournament_id }}', source)
        self.assertIn('href="/table{% if current_tournament_id %}?tid={{ current_tournament_id }}', source)
        self.assertIn('href="/profile{% if current_tournament_id %}?tid={{ current_tournament_id }}', source)
        self.assertIn("profile_subject_username|urlencode", source)
        self.assertIn("is_own_profile|default(true)", source)
        self.assertIn("request.path.startswith('/profile') and (is_own_profile|default(false))", source)
        self.assertNotIn('{% set current_tid', source)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this runtime")
class TournamentRouteSmokeTests(unittest.TestCase):
    def make_test_app(self, *blueprints):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        for blueprint in blueprints:
            app.register_blueprint(blueprint)
        return app

    def test_home_uses_selected_tournament_context(self):
        from app.routes.main import main_bp

        app = self.make_test_app(main_bp)
        cursor = FakeCursor(fetchall_results=[[]])
        selected = Mock(return_value=42)

        with (
            app.test_client() as client,
            patch("app.routes.main.get_db", return_value=FakeConnection(cursor)),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[{"id": 42, "name": "Selected", "is_active": 1}]),
            patch("app.routes.main.get_selected_tournament_id", selected),
            patch("app.routes.main.get_tournament_by_id", return_value={"id": 42, "name": "Selected"}),
            patch("app.routes.main.render_template", side_effect=fake_render_template),
        ):
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            response = client.get("/?tid=42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["current_tournament_id"], 42)
        self.assertEqual(response.get_json()["current_tournament_name"], "Selected")
        selected.assert_called_once_with(42, cur=cursor)

    def test_table_uses_selected_tournament_context(self):
        from app.routes.table import table_bp

        app = self.make_test_app(table_bp)
        cursor = FakeCursor(
            fetchone_results=[("Selected", 1, "2026-06-01")],
            fetchall_results=[[(42, "Selected", 1, "2026-06-01")]],
        )
        selected = Mock(return_value=42)

        with (
            app.test_client() as client,
            patch("app.routes.table.get_db", return_value=FakeConnection(cursor)),
            patch("app.routes.table.close_db"),
            patch("app.routes.table.get_selected_tournament_id", selected),
            patch("app.routes.table.get_tournament_status", return_value="current"),
            patch("app.routes.table.get_tournament_ranking", return_value=[]),
            patch("app.routes.table.render_template", side_effect=fake_render_template),
            patch("app.services.top_scorer_service.get_db", return_value=FakeConnection(cursor)),
            patch("app.services.top_scorer_service.close_db"),
        ):
            response = client.get("/table?tid=42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["current_tournament_id"], 42)
        self.assertEqual(response.get_json()["selected_tid"], 42)
        self.assertEqual(response.get_json()["selected_name"], "Selected")
        selected.assert_called_once_with(42, cur=cursor)

    def test_profile_uses_selected_tournament_context(self):
        from app.routes.profile import profile_bp
        from app.routes.table import table_bp

        app = self.make_test_app(profile_bp, table_bp)
        cursor = FakeCursor(
            fetchone_results=[
                ("alice",),
                (1,),
                (0, 0, 0, 0, 0, 0, 0, 0),
            ],
            fetchall_results=[[], []],
        )
        selected = Mock(return_value=42)

        with (
            app.test_client() as client,
            patch("app.routes.profile.get_db", return_value=FakeConnection(cursor)),
            patch("app.routes.profile.close_db"),
            patch("app.routes.profile.get_all_tournaments", return_value=[{"id": 42, "name": "Selected", "is_active": 1}]),
            patch("app.routes.profile.get_selected_tournament_id", selected),
            patch("app.routes.profile.get_tournament_by_id", return_value={"id": 42, "name": "Selected"}),
            patch("app.routes.profile.get_tournament_ranking", return_value=[{"user_id": 1, "place": 1}]),
            patch("app.routes.profile.render_template", side_effect=fake_render_template),
        ):
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            response = client.get("/profile?tid=42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["current_tournament_id"], 42)
        self.assertEqual(response.get_json()["current_tournament_name"], "Selected")
        selected.assert_called_once_with(42, cur=cursor)

    def test_profile_has_place_and_titles_without_race_cue(self):
        profile = (ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
        table = (ROOT / "templates" / "table_content.html").read_text(encoding="utf-8")
        for text in ("Вы ведёте " + "турнирную гонку", "Вы в " + "зоне лидеров", "Борьба за " + "подъём в таблице"):
            self.assertNotIn(text, profile)
        self.assertNotIn("profile-" + "race-cue", profile)
        self.assertIn("🥇 1 место", profile)
        self.assertIn("🥈 2 место", profile)
        self.assertIn("🥉 3 место", profile)
        self.assertIn("{{ current_place }} место", profile)
        self.assertIn("profile-position-row", profile)
        self.assertIn("position_metric", profile)
        self.assertIn("{{ position_metric.label }}", profile)
        self.assertIn("{{ position_metric.points_text }}", profile)
        self.assertIn("Мой профиль", profile)
        self.assertIn("Профиль участника", profile)
        self.assertIn("{% if titles %}", profile)
        self.assertIn("Пока без титулов", profile)
        self.assertIn("Лидер", table)
        self.assertIn("Аутсайдер", table)

    def test_profile_metric_uses_canonical_ranking_and_russian_plural(self):
        from app.routes.profile import build_profile_position_metric, format_profile_points

        ranking = [
            {"user_id": 1, "place": 1, "points": 42},
            {"user_id": 2, "place": 2, "points": 34},
        ]
        self.assertEqual(build_profile_position_metric(ranking, 2), {
            "kind": "gap", "label": "До лидера", "points": 8, "points_text": "8 очков",
        })
        self.assertEqual(build_profile_position_metric(ranking, 1), {
            "kind": "lead", "label": "Преимущество", "points": 8, "points_text": "8 очков",
        })
        self.assertEqual(build_profile_position_metric([{**ranking[0]}, {"user_id": 2, "place": 2, "points": 42}], 2)["points"], 0)
        self.assertIsNone(build_profile_position_metric(ranking[:1], 1))
        self.assertIsNone(build_profile_position_metric([], 1))
        self.assertIsNone(build_profile_position_metric(ranking, 99))
        self.assertIsNone(build_profile_position_metric([{"user_id": 1, "place": 1, "points": None}], 1))
        for value, expected in ((0, "0 очков"), (1, "1 очко"), (2, "2 очка"), (4, "4 очка"), (5, "5 очков"), (11, "11 очков"), (14, "14 очков"), (21, "21 очко"), (22, "22 очка"), (25, "25 очков")):
            self.assertEqual(format_profile_points(value), expected)

    def test_legacy_foreign_profile_redirects_to_stable_public_user_id(self):
        from app.routes.profile import profile_bp

        app = self.make_test_app(profile_bp)
        cursor = FakeCursor(
            fetchone_results=[
                (2, "Other Name", 0),
                (2,),
                (0, 0, 0, 0, 0, 0, 0, 0),
            ],
            fetchall_results=[[], []],
        )
        with (
            app.test_client() as client,
            patch("app.routes.profile.get_db", return_value=FakeConnection(cursor)),
            patch("app.routes.profile.close_db"),
            patch("app.routes.profile.get_all_tournaments", return_value=[{"id": 42, "name": "Selected", "is_active": 1}]),
            patch("app.routes.profile.get_selected_tournament_id", return_value=42),
            patch("app.routes.profile.get_tournament_by_id", return_value={"id": 42, "name": "Selected"}),
            patch("app.routes.profile.get_tournament_ranking", return_value=[
                {"user_id": 1, "place": 1, "points": 42},
                {"user_id": 2, "place": 2, "points": 34},
            ]),
        ):
            with client.session_transaction() as sess:
                sess["user_id"] = 1
            response = client.get("/profile?username=Other%20Name&tid=42")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/profile/2?tid=42"))


if __name__ == "__main__":
    unittest.main()
