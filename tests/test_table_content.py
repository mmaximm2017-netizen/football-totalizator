import unittest
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[1]


class TopScorerPlayerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.players = []
        self._stack = []
        self._player = None
        self._capturing_name = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        parent = self._stack[-1] if self._stack else None

        if tag == "div" and "top-scorer-player" in classes:
            self._player = {"direct_children": [], "username": "", "photos": 0}
        elif self._player and parent == "div" and tag == "span":
            if "top-scorer-name" in classes:
                self._player["direct_children"].append("top-scorer-name")
                self._capturing_name = True
            elif "top-scorer-photos" in classes:
                self._player["direct_children"].append("top-scorer-photos")
        elif self._player and "top-scorer-photo" in classes and tag == "img":
            self._player["photos"] += 1

        self._stack.append(tag)

    def handle_data(self, data):
        if self._capturing_name and self._player:
            self._player["username"] += data

    def handle_endtag(self, tag):
        if tag == "span" and self._capturing_name:
            self._capturing_name = False
        if tag == "div" and self._player and self._stack and self._stack[-1] == "div":
            self.players.append(self._player)
            self._player = None
        if self._stack:
            self._stack.pop()


class TableContentUiTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "templates" / "table_content.html").read_text(encoding="utf-8")

    def test_all_leader_and_outsider_statuses_remain_in_markup(self):
        for text in (
            "Лидер",
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

    def test_rpl_top_scorers_render_mapped_photos_and_two_for_bek(self):
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        rows = [
            {"place": place, "shared": False, "username": "user", "movement": None,
             "leader_status": None, "outsider_status": None, "points": 0}
            for place in (1, 2, 3)
        ]
        scorers = [
            {"place": 1, "username": "Bowb", "scorer_goals": 3},
            {"place": 2, "username": "БЕК125125", "scorer_goals": 3},
            {"place": 3, "username": "Byza-Zenit", "scorer_goals": 2},
            {"place": 4, "username": "Макс Зенит", "scorer_goals": 2},
            {"place": 5, "username": "Алексей конь", "scorer_goals": 1},
            {"place": 6, "username": "Денис 05", "scorer_goals": 1},
            {"place": 7, "username": "Без фото", "scorer_goals": 1},
        ]

        with app.test_request_context("/"):
            rpl_html = render_template(
                "table_content.html",
                table=rows,
                selected_name="Чемпионат России 🇷🇺",
                selected_tid=5,
                top_scorers=scorers,
            )
            cup_html = render_template(
                "table_content.html",
                table=rows,
                selected_name="Кубок России",
                selected_tid=6,
                top_scorers=scorers,
            )

        parser = TopScorerPlayerParser()
        parser.feed(rpl_html)
        players = {player["username"]: player for player in parser.players}
        self.assertEqual(players["Bowb"]["photos"], 1)
        self.assertEqual(players["БЕК125125"]["photos"], 2)
        self.assertEqual(players["БЕК125125"]["direct_children"], ["top-scorer-name", "top-scorer-photos"])
        self.assertEqual(sum(player["photos"] for player in parser.players), 7)
        self.assertEqual(rpl_html.count('class="top-scorer-photo"'), 7)
        for photo in (
            "bowb.webp",
            "bek125125-1.webp",
            "bek125125-2.webp",
            "byza-zenit.webp",
            "max-zenit.webp",
            "aleksey-kon.webp",
            "denis-05.webp",
        ):
            self.assertEqual(rpl_html.count(f"/static/scorers/{photo}"), 1)
        self.assertIn(".top-scorer-photo {\n        display: block;\n        width: 64px;\n        height: 64px;", rpl_html)
        self.assertNotIn('Без фото</span>\n                            <span class="top-scorer-photos"', rpl_html)

        cup_parser = TopScorerPlayerParser()
        cup_parser.feed(cup_html)
        cup_players = {player["username"]: player for player in cup_parser.players}
        self.assertEqual(cup_players["Bowb"]["photos"], 1)
        self.assertEqual(cup_players["БЕК125125"]["photos"], 2)
        self.assertEqual(sum(player["photos"] for player in cup_parser.players), 7)
        self.assertEqual(cup_html.count('class="top-scorer-photo"'), 7)

        with app.test_request_context("/"):
            wc_html = render_template(
                "table_content.html",
                table=rows,
                selected_name="ЧМ-2026",
                selected_tid=7,
                top_scorers=scorers,
            )
        self.assertNotIn('class="top-scorer-photo"', wc_html)

    def test_rpl_scorer_name_and_photos_stay_inline_on_mobile(self):
        css = self.template
        self.assertIn(".top-scorer-name {\n        flex: 0 1 auto;", css)
        self.assertIn(".top-scorer-player {\n        display: flex;", css)
        self.assertIn("        flex-direction: row;", css)
        self.assertIn("        flex-wrap: nowrap;", css)
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-player {\n            gap: 4px;\n            flex-wrap: nowrap;", css)
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-name {\n            display: block;", css)
        self.assertIn("            white-space: nowrap;", css)
        self.assertIn("            text-overflow: ellipsis;", css)
        self.assertIn(":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-photos {\n            gap: 4px;", css)

    def test_russian_cup_scorers_share_rpl_geometry_but_keep_scoped_palette(self):
        for selector in (
            ":is(body.tournament-rpl, body.tournament-rcup) .top-scorers-card",
            ":is(body.tournament-rpl, body.tournament-rcup) .top-scorers-table",
            ":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-place",
            ":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-name",
            ":is(body.tournament-rpl, body.tournament-rcup) .top-scorer-goals",
        ):
            self.assertIn(selector, self.template)
        self.assertIn("border-spacing: 0 7px;", self.template)
        self.assertNotIn("body.tournament-rcup .top-scorers-table {\n        border-spacing: 0;", self.template)
        self.assertIn("body.tournament-rcup .top-scorers-card {\n            width: calc(100% + 4px);", self.template)
        self.assertIn("font-weight: 700;", self.template)
        self.assertIn("background: #3A0A32;", self.template)
        self.assertIn("background: #E80024;", self.template)

    def test_rpl_scorer_markup_uses_inner_wrapper_inside_table_cell(self):
        self.assertIn(
            '<td>\n                            <div class="top-scorer-player">',
            self.template,
        )
        self.assertNotIn('<td class="top-scorer-player">', self.template)


if __name__ == "__main__":
    unittest.main()
