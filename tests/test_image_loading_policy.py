import unittest
from pathlib import Path

from app.utils import get_club_logo, get_flag


ROOT = Path(__file__).resolve().parents[1]


class ImageLoadingPolicyTests(unittest.TestCase):
    def test_login_does_not_reference_tournament_or_team_logos(self):
        login = (ROOT / "templates" / "login.html").read_text(encoding="utf-8")
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertNotIn("WorldCup.png", login)
        self.assertNotIn("Fonbet_Russian_Cup.png", login)
        self.assertNotIn("WorldCup.png", base)
        self.assertIn('src="/static/icon-192-new.png"', base)

    def test_team_images_use_low_priority_lazy_loading(self):
        for image in (get_club_logo("Родина"), get_flag("Россия")):
            self.assertIn('loading="lazy"', image)
            self.assertIn('decoding="async"', image)
            self.assertIn('fetchpriority="low"', image)

    def test_match_tournament_and_cup_logos_are_lazy(self):
        league = (ROOT / "templates" / "partials" / "home" / "_match_league.html").read_text(encoding="utf-8")
        day_block = (ROOT / "templates" / "partials" / "home" / "_day_block.html").read_text(encoding="utf-8")

        for template in (league, day_block):
            self.assertIn('loading="lazy"', template)
            self.assertIn('decoding="async"', template)
            self.assertIn('fetchpriority="low"', template)
        self.assertIn('width="42" height="42"', day_block)

    def test_cup_logo_is_not_eager_per_match_card(self):
        day_block = (ROOT / "templates" / "partials" / "home" / "_day_block.html").read_text(encoding="utf-8")

        self.assertEqual(day_block.count("Fonbet_Russian_Cup.png"), 1)
        self.assertIn('alt="Кубок России" width="42" height="42" loading="lazy"', day_block)

    def test_only_header_icon_is_eager(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertEqual(base.count('loading="eager"'), 1)
        self.assertIn('class="app-title-icon" width="30" height="30" loading="eager"', base)

    def test_closed_months_keep_cards_but_defer_their_images(self):
        month_block = (ROOT / "templates" / "partials" / "home" / "_month_block.html").read_text(encoding="utf-8")
        day_block = (ROOT / "templates" / "partials" / "home" / "_day_block.html").read_text(encoding="utf-8")

        self.assertIn("display: {% if month_is_open %}block{% else %}none{% endif %}", month_block)
        self.assertIn("loading=\"lazy\"", day_block)


if __name__ == "__main__":
    unittest.main()
