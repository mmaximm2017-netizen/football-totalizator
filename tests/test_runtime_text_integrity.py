import unittest
from pathlib import Path

from app.services.admin_view_service import normalize_league_key


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTextIntegrityTests(unittest.TestCase):
    def test_runtime_python_contains_no_unicode_replacement_characters(self):
        bad = []
        for path in (ROOT / "app").rglob("*.py"):
            text = path.read_text(encoding="utf-8-sig")
            if "\ufffd" in text:
                bad.append(str(path.relative_to(ROOT)))
        self.assertEqual(bad, [])

    def test_league_normalization_uses_supported_canonical_aliases_only(self):
        self.assertEqual(normalize_league_key("rpl"), "rpl")
        self.assertEqual(normalize_league_key("РПЛ"), "rpl")
        self.assertEqual(normalize_league_key("ЧМ-2026"), "wc2026")
        self.assertEqual(normalize_league_key("Кубок России"), "rcup")
        self.assertEqual(normalize_league_key("unknown legacy value"), "other")

    def test_match_service_has_no_legacy_tournament_name_guessing(self):
        text = (ROOT / "app/services/match_service.py").read_text(encoding="utf-8")
        self.assertNotIn("Fallback for legacy mojibake", text)
        self.assertNotIn("name ILIKE '%2026%'", text)
        self.assertNotIn("name ILIKE '%матч%'", text)


if __name__ == "__main__":
    unittest.main()
