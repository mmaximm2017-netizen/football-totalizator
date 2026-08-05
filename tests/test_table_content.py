import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TableContentUiTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "templates" / "table_content.html").read_text(encoding="utf-8")

    def test_all_leader_and_outsider_statuses_remain_in_markup(self):
        for text in (
            "Единоличный лидер",
            "Уверенный лидер",
            "Явный лидер",
            "Безоговорочный лидер",
            "Аутсайдер",
            "Уверенный аутсайдер",
            "Явный аутсайдер",
            "Безоговорочный аутсайдер",
        ):
            self.assertIn(text, self.template)
        for modifier in ("confident", "dominant", "absolute", "sole-leader", "outsider-badge"):
            self.assertIn(modifier, self.template)

    def test_badges_are_single_line_and_name_is_only_shrinkable_item(self):
        css = self.template
        self.assertIn("width: 100%;", css)
        self.assertIn("white-space: nowrap;", css)
        self.assertIn("flex: 1 1 auto;", css)
        self.assertIn("text-overflow: ellipsis;", css)
        self.assertIn("flex: 0 0 auto;", css)
        self.assertIn("width: max-content;", css)
        self.assertIn("max-width: none;", css)
        self.assertIn("overflow: visible;", css)
        self.assertIn("text-overflow: clip;", css)
        self.assertIn("font-size: 12px !important;", css)
        self.assertIn("padding: 5px 9px !important;", css)
        self.assertNotIn(".top-1 .leader-badge {\n            align-self: flex-start;\n            flex-basis: 100%;", css)
        self.assertNotIn("body.tournament-rcup .player-line {\n        display: flex;\n        align-items: center;\n        gap: 6px;\n        min-width: 0;\n        flex-wrap: wrap;", css)

    def test_long_statuses_have_readable_compact_sizes(self):
        self.assertIn(".leader-badge.confident,", self.template)
        self.assertIn(".leader-badge.dominant,", self.template)
        self.assertIn("font-size: 11.5px !important;", self.template)
        self.assertIn(".leader-badge.absolute {", self.template)
        self.assertIn("font-size: 11px !important;", self.template)
        self.assertNotIn("font-size: 9px !important;", self.template)

    def test_tournament_styles_and_points_column_are_preserved(self):
        self.assertIn("body.tournament-rpl .leader-badge", self.template)
        self.assertIn("body.tournament-wc2026 .leader-badge", self.template)
        self.assertIn("body.tournament-wc2026 .standings-table tbody tr.wc-eliminated", self.template)
        self.assertIn("body.tournament-rcup .leader-badge", self.template)
        self.assertIn("width: 54px;", self.template)
        self.assertIn("width: 56px;", self.template)

    def test_russian_cup_uses_the_shared_rpl_ranking_geometry(self):
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .player-name", self.template)
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .table-shell", self.template)
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .points-number", self.template)
        self.assertIn("body.tournament-rcup .leader-badge", self.template)
        self.assertIn("background: #E80024;", self.template)

        for conflicting_rule in (
            "body.tournament-rcup .standings-table .player-row {",
            "grid-template-columns: 52px minmax(0, 1fr) 60px;",
            "body.tournament-rcup .standings-table .player-row td {",
            "body.tournament-rcup .points-cell {",
            "body.tournament-rcup .leader-badge.dominating {",
        ):
            self.assertNotIn(conflicting_rule, self.template)


if __name__ == "__main__":
    unittest.main()
