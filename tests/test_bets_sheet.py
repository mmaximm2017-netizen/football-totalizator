import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BetsSheetUiTests(unittest.TestCase):
    def test_sheet_renders_shared_themable_structure(self):
        partial = (ROOT / "templates" / "partials" / "home" / "_bets_sheet.html").read_text(encoding="utf-8")
        self.assertIn("predictions-sheet", partial)
        self.assertIn("predictions-sheet__header", partial)
        self.assertIn("predictions-sheet__close", partial)

    def test_compact_match_uses_separate_team_nodes_and_long_dash(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("buildTeamIcon", template)
        self.assertIn("predictions-sheet__name", template)
        self.assertIn("predictions-sheet__vs", template)
        self.assertIn("vsNode.textContent = score && score !== 'VS' ? score : '—'", template)
        self.assertNotIn("homeName.textContent + 'Vs'", template)

    def test_rpl_and_russian_cup_have_scoped_sheet_modifiers(self):
        home_css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        cup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .bets-sheet", home_css)
        self.assertIn("body.tournament-rpl .bets-compact-team-icon", home_css)
        self.assertIn("body.tournament-rcup .bets-sheet", cup_css)
        self.assertIn("body.tournament-rcup .bets-compact-team-icon", cup_css)

    def test_mobile_sheet_has_bounded_logo_and_team_layout(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: 30px minmax(0, 1fr) 20px minmax(0, 1fr) 30px", css)
        self.assertIn("object-fit: contain !important", css)
        self.assertIn("overflow-wrap: anywhere", css)


if __name__ == "__main__":
    unittest.main()
