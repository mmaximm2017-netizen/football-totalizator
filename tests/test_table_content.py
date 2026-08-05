import unittest
from pathlib import Path

from flask import Flask, render_template

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

    def test_russian_cup_points_and_podium_only_highlight_the_champion(self):
        points_rule = self.template.split("body.tournament-rcup .points-number {", 1)[1].split("}", 1)[0]
        leader_rule = self.template.split("body.tournament-rcup .standings-table tbody tr.top-1 .points-number {", 1)[1].split("}", 1)[0]

        self.assertIn("background: #310027;", points_rule)
        self.assertIn("border-color: rgba(232, 0, 36, 0.75);", points_rule)
        self.assertIn("color: #ffffff !important;", points_rule)
        self.assertIn("background: #E80024;", leader_rule)
        self.assertIn("border-color: #E80024;", leader_rule)
        self.assertIn("color: #ffffff !important;", leader_rule)
        non_champion_points = self.template.split("body.tournament-rcup .standings-table tbody tr:not(.top-1) .points-number {", 1)[1].split("}", 1)[0]
        self.assertIn("background: #310027;", non_champion_points)
        self.assertIn("border-color: rgba(232, 0, 36, 0.75);", non_champion_points)
        self.assertIn("color: #ffffff !important;", non_champion_points)
        self.assertNotIn("body.tournament-rcup .standings-table tbody tr.top-2 .points-number", self.template)
        self.assertNotIn("body.tournament-rcup .standings-table tbody tr.top-3 .points-number", self.template)
        self.assertNotIn("#C5CAD3", self.template)
        self.assertNotIn("#B86A3C", self.template)
        self.assertIn(".rank-movement.up", self.template)

    def test_russian_cup_uses_a_trophy_only_for_first_place(self):
        self.assertIn("{% if selected_name == 'Кубок России' %}", self.template)
        self.assertIn("{% if row.place == 1 %}<span class=\"place-emoji\">🏆</span>", self.template)
        self.assertIn("{% else %}{{ row.place }}{% endif %}", self.template)
        self.assertIn("{% elif row.place == 1 %}<span class=\"place-emoji\">🥇</span>", self.template)
        self.assertIn("{% elif row.place == 2 %}<span class=\"place-emoji\">🥈</span>", self.template)
        self.assertIn("{% elif row.place == 3 %}<span class=\"place-emoji\">🥉</span>", self.template)
        self.assertIn("body.tournament-rcup .standings-table tbody tr:not(.top-1) {", self.template)
        self.assertIn("body.tournament-rcup .standings-table tbody tr:not(.top-1) .place-box {", self.template)
        self.assertIn("box-shadow: none;", self.template)

    def test_russian_cup_non_champion_places_use_one_complete_geometry_rule(self):
        place_rule = self.template.split("body.tournament-rcup .standings-table tbody tr:not(.top-1) .place-box {", 1)[1].split("}", 1)[0]
        mobile_rule = self.template.split("@media (max-width: 430px) {\n        body.tournament-rcup .standings-table tbody tr:not(.top-1) .place-box {", 1)[1].split("}\n    }", 1)[0]

        for declaration in (
            "display: inline-flex;",
            "align-items: center;",
            "justify-content: center;",
            "width: auto;",
            "min-width: 34px;",
            "height: 34px;",
            "padding: 0;",
            "border-radius: 13px;",
            "background: rgba(255,255,255,0.52);",
            "font-size: 16px !important;",
            "font-weight: 1000 !important;",
        ):
            self.assertIn(declaration, place_rule)
        for declaration in ("min-width: 31px;", "height: 31px;", "border-radius: 12px;", "font-size: 15px !important;"):
            self.assertIn(declaration, mobile_rule)
        self.assertNotIn("body.tournament-rcup .standings-table tbody tr.top-2 .place-box", self.template)
        self.assertNotIn("body.tournament-rcup .standings-table tbody tr.top-3 .place-box", self.template)

    def test_russian_cup_renders_no_medals_after_first_place(self):
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        rows = [
            {"place": place, "shared": False, "username": "user", "movement": None,
             "leader_status": None, "outsider_status": None, "points": 0}
            for place in (1, 2, 3)
        ]
        with app.test_request_context("/"):
            cup_html = render_template("table_content.html", table=rows, selected_name="Кубок России", selected_tid=None, top_scorers=[])
            rpl_html = render_template("table_content.html", table=rows, selected_name="Чемпионат России 🇷🇺", selected_tid=None, top_scorers=[])

        self.assertIn("🏆", cup_html)
        self.assertNotIn("🥇", cup_html)
        self.assertNotIn("🥈", cup_html)
        self.assertNotIn("🥉", cup_html)
        self.assertIn("🥇", rpl_html)
        self.assertIn("🥈", rpl_html)
        self.assertIn("🥉", rpl_html)


if __name__ == "__main__":
    unittest.main()
