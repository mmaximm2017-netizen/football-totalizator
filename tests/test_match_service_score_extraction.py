import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "app" / "services" / "match_service.py"


def load_match_service_module():
    fake_app = types.ModuleType("app")
    fake_config = types.ModuleType("app.config")
    fake_db = types.ModuleType("app.db")
    fake_services = types.ModuleType("app.services")
    fake_scoring = types.ModuleType("app.models.scoring")
    fake_sync_history = types.ModuleType("app.services.sync_history_service")
    fake_utils = types.ModuleType("app.utils")
    fake_wc_playoff = types.ModuleType("app.services.wc_playoff_service")

    fake_config.API_KEY = "test"
    fake_config.LEAGUE_IDS = [2000]
    fake_config.WC2026_API_SYNC_ENABLED = False
    fake_db.get_db = lambda: None
    fake_db.close_db = lambda conn, cur=None: None
    fake_sync_history.create_sync_run = lambda summary=None: None
    fake_sync_history.finish_sync_run = lambda *args, **kwargs: None
    fake_sync_history.recover_stale_syncs = lambda: None
    fake_utils.translate_name = lambda value: value
    fake_utils.parse_utc_time = lambda value: value
    fake_utils.utc_now = lambda: None
    fake_wc_playoff.infer_playoff_stage_from_api = lambda match: None
    fake_wc_playoff.is_wc2026_playoff_match = lambda *args, **kwargs: False
    fake_scoring.has_valid_finished_score = lambda status, home, away: (
        status == "FINISHED"
        and isinstance(home, int)
        and not isinstance(home, bool)
        and isinstance(away, int)
        and not isinstance(away, bool)
        and 0 <= home <= 99
        and 0 <= away <= 99
    )

    module_name = "match_service_score_under_test"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)

    with patch.dict(
        sys.modules,
        {
            "app": fake_app,
            "app.config": fake_config,
            "app.db": fake_db,
            "app.services": fake_services,
            "app.models.scoring": fake_scoring,
            "app.services.sync_history_service": fake_sync_history,
            "app.utils": fake_utils,
            "app.services.wc_playoff_service": fake_wc_playoff,
        },
    ):
        spec.loader.exec_module(module)

    return module


service = load_match_service_module()


class MatchServiceScoreExtractionTests(unittest.TestCase):
    def test_group_regular_uses_full_time(self):
        match = {
            "status": "FINISHED",
            "score": {"duration": "REGULAR", "fullTime": {"home": 2, "away": 1}},
        }

        self.assertEqual(service.extract_api_match_score(match, is_playoff_match=False), (2, 1, "score.fullTime"))

    def test_playoff_regular_uses_full_time(self):
        match = {
            "status": "FINISHED",
            "score": {"duration": "REGULAR", "fullTime": {"home": 1, "away": 0}},
        }

        self.assertEqual(service.extract_api_match_score(match, is_playoff_match=True), (1, 0, "score.fullTime"))

    def test_playoff_extra_time_uses_120_min_score(self):
        match = {
            "status": "FINISHED",
            "score": {
                "duration": "EXTRA_TIME",
                "fullTime": {"home": 2, "away": 1},
                "regularTime": {"home": 1, "away": 1},
                "extraTime": {"home": 1, "away": 0},
            },
        }

        self.assertEqual(service.extract_api_match_score(match, is_playoff_match=True), (2, 1, "score.fullTime.extra_time"))

    def test_playoff_penalty_shootout_uses_regular_plus_extra_time(self):
        match = {
            "status": "FINISHED",
            "score": {
                "duration": "PENALTY_SHOOTOUT",
                "fullTime": {"home": 7, "away": 6},
                "regularTime": {"home": 1, "away": 1},
                "extraTime": {"home": 0, "away": 0},
                "penalties": {"home": 6, "away": 5},
            },
        }

        self.assertEqual(service.extract_api_match_score(match, is_playoff_match=True), (1, 1, "score.regularTime+extraTime"))

    def test_score_extraction_accepts_home_team_away_team_keys(self):
        match = {
            "status": "FINISHED",
            "score": {
                "duration": "PENALTY_SHOOTOUT",
                "regularTime": {"homeTeam": 2, "awayTeam": 2},
                "extraTime": {"homeTeam": 1, "awayTeam": 0},
            },
        }

        self.assertEqual(service.extract_api_match_score(match, is_playoff_match=True), (3, 2, "score.regularTime+extraTime"))

    def test_playoff_penalty_shootout_without_reliable_fields_does_not_return_score(self):
        match = {
            "status": "FINISHED",
            "score": {
                "duration": "PENALTY_SHOOTOUT",
                "fullTime": {"home": 7, "away": 6},
                "penalties": {"home": 6, "away": 5},
            },
        }

        self.assertEqual(
            service.extract_api_match_score(match, is_playoff_match=True),
            (None, None, "penalty_unreliable_120min_score"),
        )

    def test_manual_result_override_blocks_api_score_update(self):
        self.assertEqual(
            service.apply_manual_result_override(
                "FINISHED",
                7,
                6,
                "FINISHED",
                1,
                1,
                manual_result_override=True,
                manual_override_allowed=True,
            ),
            ("FINISHED", 1, 1),
        )

    def test_fetch_matches_skips_wc2026_when_disabled_but_keeps_other_competitions(self):
        calls = []

        class Response:
            status_code = 200

            def json(self):
                return {"matches": [{"id": 11}]}

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(url)
            return Response()

        original_league_ids = service.LEAGUE_IDS
        service.LEAGUE_IDS = [2000, 9999]
        try:
            with patch.object(service.requests, "get", side_effect=fake_get):
                matches = service.fetch_matches()
        finally:
            service.LEAGUE_IDS = original_league_ids

        self.assertEqual(calls, ["https://api.football-data.org/v4/competitions/9999/matches"])
        self.assertEqual(matches, [{"id": 11, "league": "other"}])

    def test_finished_api_match_without_full_score_is_saved_as_scheduled(self):
        inserted = {}

        class Cursor:
            def __init__(self):
                self.fetchone_result = None

            def execute(self, query, params=None):
                normalized = " ".join(query.split())
                if normalized.startswith("SELECT id FROM tournaments WHERE name = %s"):
                    self.fetchone_result = (5,)
                elif normalized.startswith("SELECT id, tournament_id FROM matches WHERE api_match_id = %s"):
                    self.fetchone_result = None
                elif normalized.startswith("INSERT INTO matches"):
                    inserted["params"] = params
                    self.fetchone_result = (42,)

            def fetchone(self):
                return self.fetchone_result

        class Conn:
            def __init__(self):
                self.cur = Cursor()

            def cursor(self):
                return self.cur

            def commit(self):
                pass

            def rollback(self):
                raise AssertionError("rollback should not be called")

        match = {
            "id": "api-1",
            "homeTeam": {"name": "Home"},
            "awayTeam": {"name": "Away"},
            "utcDate": "2026-07-18T17:30:00Z",
            "status": "FINISHED",
            "score": {"fullTime": {"home": None, "away": None}},
            "league": "other",
        }

        with (
            patch.object(service, "fetch_matches", return_value=[match]),
            patch.object(service, "get_db", return_value=Conn()),
            patch.object(service, "close_db"),
            self.assertLogs(service.logger, level="WARNING") as logs,
        ):
            summary = service.update_matches()

        self.assertEqual(inserted["params"][5], "SCHEDULED")
        self.assertEqual(inserted["params"][6:8], (None, None))
        self.assertEqual(summary["changed_finished_match_ids"], [])
        self.assertEqual(summary["matches_skipped_invalid_finished_score"], 1)
        self.assertIn("Skipping incomplete API result", "\n".join(logs.output))

    def test_finished_api_nil_nil_score_is_queued_for_recalculation(self):
        inserted = {}

        class Cursor:
            def __init__(self):
                self.fetchone_result = None

            def execute(self, query, params=None):
                normalized = " ".join(query.split())
                if normalized.startswith("SELECT id FROM tournaments WHERE name = %s"):
                    self.fetchone_result = (5,)
                elif normalized.startswith("SELECT id, tournament_id FROM matches WHERE api_match_id = %s"):
                    self.fetchone_result = None
                elif normalized.startswith("INSERT INTO matches"):
                    inserted["params"] = params
                    self.fetchone_result = (42,)

            def fetchone(self):
                return self.fetchone_result

        class Conn:
            def __init__(self):
                self.cur = Cursor()

            def cursor(self):
                return self.cur

            def commit(self):
                pass

            def rollback(self):
                raise AssertionError("rollback should not be called")

        match = {
            "id": "api-2",
            "homeTeam": {"name": "Home"},
            "awayTeam": {"name": "Away"},
            "utcDate": "2026-07-18T17:30:00Z",
            "status": "FINISHED",
            "score": {"fullTime": {"home": 0, "away": 0}},
            "league": "other",
        }

        with (
            patch.object(service, "fetch_matches", return_value=[match]),
            patch.object(service, "get_db", return_value=Conn()),
            patch.object(service, "close_db"),
        ):
            summary = service.update_matches()

        self.assertEqual(inserted["params"][5:8], ("FINISHED", 0, 0))
        self.assertEqual(summary["changed_finished_match_ids"], [42])


if __name__ == "__main__":
    unittest.main()
