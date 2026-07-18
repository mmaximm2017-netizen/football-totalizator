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
        self.assertIn("bets-compact-team-icon", template)
        self.assertIn("bets-compact-team-name", template)
        self.assertIn("predictions-sheet__name", template)
        self.assertIn("predictions-sheet__vs", template)
        self.assertIn("vsNode.textContent = score && score !== 'VS' ? score : '—'", template)
        self.assertNotIn("homeName.textContent + 'Vs'", template)

    def test_long_name_modifier_is_added_after_base_match_class(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        base_class = "matchLine.className = 'bets-compact-match predictions-sheet__teams';"
        long_class = "matchLine.classList.add('has-long-team-name');"
        self.assertLess(template.index(base_class), template.index(long_class))
        self.assertIn("is-long-team-name", template)
        self.assertIn("is-very-long-team-name", template)
        self.assertNotIn("matchLine.className = 'bets-compact-match predictions-sheet__teams';\n        matchLine.appendChild", template)

    def test_rpl_and_russian_cup_have_scoped_sheet_modifiers(self):
        home_css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        cup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .bets-sheet", home_css)
        self.assertIn("body.tournament-rpl .bets-compact-team-icon", home_css)
        self.assertIn("body.tournament-rcup .bets-sheet", cup_css)
        self.assertIn("body.tournament-rcup .bets-compact-team-icon", cup_css)

    def test_mobile_sheet_has_bounded_logo_and_team_layout(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: var(--bets-team-logo-size) minmax(0, 1fr) minmax(18px, max-content) minmax(0, 1fr) var(--bets-team-logo-size)", css)
        self.assertIn("--bets-team-logo-size: clamp(44px, 12.3vw, 50px)", css)
        self.assertNotIn("grid-template-columns: 30px minmax(0, 1fr) 20px minmax(0, 1fr) 30px", css)
        self.assertNotIn("width: 30px; height: 30px; min-width: 30px", css)
        self.assertNotIn("width: 23px !important; height: 23px !important", css)
        self.assertIn("min-height: 78px", css)
        self.assertIn("width: 42px !important", css)
        self.assertIn("width: clamp(36px, 10.25vw, 40px) !important", css)
        self.assertIn("font-size: 16px; line-height: 1.08", css)
        self.assertIn("-webkit-line-clamp: 2", css)
        self.assertIn("line-clamp: 2", css)
        self.assertIn("text-wrap: balance", css)
        self.assertIn(".bets-compact-team-name.is-long-team-name", css)
        self.assertIn(".bets-compact-team-name.is-very-long-team-name", css)
        self.assertIn(".bets-compact-vs {", css)
        self.assertIn("object-fit: contain !important", css)
        self.assertIn("overflow-wrap: break-word", css)

    def test_team_geometry_preserves_tournament_colors_and_player_rows(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        cup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .bets-compact-team-name { color: #fff; }", css)
        self.assertIn("body.tournament-rpl .bets-compact-match", css)
        self.assertIn("body.tournament-rcup .bets-compact-team-name", cup_css)
        self.assertIn("body.tournament-rcup .bets-compact-match", cup_css)
        self.assertIn("className = 'bets-compact-row'", template)
        self.assertIn("className = 'bets-compact-score'", template)
        self.assertNotIn("style.width", template)
        self.assertNotIn("style.height", template)


if __name__ == "__main__":
    unittest.main()
