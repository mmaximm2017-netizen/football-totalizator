import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "app" / "services" / "wc_playoff_service.py"


def load_service_module():
    spec = importlib.util.spec_from_file_location("wc_playoff_under_test", SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = load_service_module()


class WcPlayoffServiceTests(unittest.TestCase):
    def test_wc2026_playoff_match_is_allowed(self):
        self.assertTrue(
            service.is_wc2026_playoff_match(
                "ЧМ-2026",
                "wc2026",
                datetime(2026, 6, 28, 16, 0, tzinfo=timezone.utc),
            )
        )

    def test_wc2026_group_match_is_not_allowed(self):
        self.assertFalse(
            service.is_wc2026_playoff_match(
                "ЧМ-2026",
                "wc2026",
                datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc),
            )
        )

    def test_non_wc_match_is_not_allowed(self):
        self.assertFalse(
            service.is_wc2026_playoff_match(
                "РПЛ 2026/27",
                "rpl",
                datetime(2026, 6, 28, 16, 0, tzinfo=timezone.utc),
            )
        )

    def test_wrong_tournament_name_blocks_wc_league_fallback(self):
        self.assertFalse(
            service.is_wc2026_playoff_match(
                "Другой турнир",
                "wc2026",
                datetime(2026, 6, 28, 16, 0, tzinfo=timezone.utc),
            )
        )

    def test_wc_league_fallback_allowed_without_tournament_name(self):
        self.assertTrue(
            service.is_wc2026_playoff_match(
                None,
                "wc2026",
                datetime(2026, 6, 28, 16, 0, tzinfo=timezone.utc),
            )
        )


if __name__ == "__main__":
    unittest.main()
