import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_HELPERS = (
    "get_requested_or_current_tournament_id",
    "get_table_tournament_id",
    "get_profile_tournament_id",
    "get_active_context_tournament_id",
)
USER_FACING_ROUTES = (
    "app/routes/main.py",
    "app/routes/table.py",
    "app/routes/profile.py",
    "app/routes/predictions.py",
)


class TournamentSelectionContractTests(unittest.TestCase):
    def test_user_facing_routes_use_canonical_selected_tournament_helper(self):
        for relative_path in USER_FACING_ROUTES:
            text = (ROOT / relative_path).read_text(encoding="utf-8-sig")
            with self.subTest(path=relative_path):
                self.assertIn("get_selected_tournament_id", text)
                for helper in LEGACY_HELPERS:
                    self.assertNotIn(helper, text)

    def test_legacy_route_specific_helpers_are_removed_from_service(self):
        text = (ROOT / "app/services/tournament_context_service.py").read_text(encoding="utf-8")
        for helper in LEGACY_HELPERS:
            with self.subTest(helper=helper):
                self.assertNotIn(f"def {helper}", text)


if __name__ == "__main__":
    unittest.main()
