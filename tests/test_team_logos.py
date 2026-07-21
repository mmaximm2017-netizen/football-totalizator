import unittest
from pathlib import Path

from app.models.team_data import CLUB_LOGOS
from app.utils import get_club_logo


ROOT = Path(__file__).resolve().parents[1]
RODINA_LOGO = "/static/clubs/FC_Rodina_Logo.svg"


class TeamLogoTests(unittest.TestCase):
    def test_rodina_aliases_use_existing_svg(self):
        self.assertEqual(CLUB_LOGOS["Родина"], RODINA_LOGO)
        self.assertEqual(CLUB_LOGOS["Родина Москва"], RODINA_LOGO)

    def test_rodina_svg_exists_and_is_valid(self):
        svg = ROOT / "static" / "clubs" / "FC_Rodina_Logo.svg"
        content = svg.read_text(encoding="utf-8")
        self.assertGreater(svg.stat().st_size, 0)
        self.assertTrue(content.lstrip().startswith("<svg"))

    def test_get_club_logo_uses_rodina_svg_for_both_aliases(self):
        self.assertIn(f'src="{RODINA_LOGO}"', get_club_logo("Родина"))
        self.assertIn(f'src="{RODINA_LOGO}"', get_club_logo("Родина Москва"))
        self.assertEqual(get_club_logo("Неизвестная команда"), "")

    def test_old_rodina_path_is_not_in_project_sources(self):
        old_path = "FC_Rodina_Logo.svg" + ".png"
        for path in (ROOT / "app", ROOT / "templates", ROOT / "tests"):
            for source in path.rglob("*"):
                if source.is_file() and source.suffix in {".py", ".html", ".css", ".js", ".json"}:
                    self.assertNotIn(old_path, source.read_text(encoding="utf-8"), str(source))


if __name__ == "__main__":
    unittest.main()
