import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BetsSheetUiTests(unittest.TestCase):
    def test_sheet_renders_shared_themable_structure(self):
        partial = (ROOT / "templates" / "partials" / "home" / "_bets_sheet.html").read_text(encoding="utf-8")
        self.assertIn("predictions-sheet", partial)
        self.assertIn("predictions-sheet__header", partial)
        self.assertIn("predictions-sheet__close", partial)
        self.assertIn("data-bets-sheet-content", partial)

    def test_mobile_sheet_accounts_for_bottom_nav_safe_area_and_last_row(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("box-sizing: border-box", css)
        self.assertIn("bottom: calc(12px + 64px + max(2px, env(safe-area-inset-bottom)) + 8px)", css)
        self.assertIn("max-height: min(72dvh, calc(100dvh - 84px - max(2px, env(safe-area-inset-bottom))))", css)
        self.assertIn("body.tournament-wc2026 .bets-sheet", css)
        self.assertIn("padding-bottom: calc(18px + env(safe-area-inset-bottom))", css)
        self.assertIn("scroll-padding-bottom: calc(18px + env(safe-area-inset-bottom))", css)
        self.assertIn(".bets-compact-list {\n            padding-bottom: 10px;", css)
        self.assertIn("overflow-y: auto", css)

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

    def test_finished_rows_sort_by_numeric_points_with_stable_nulls_last(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function parseCompactPoints(row)", template)
        self.assertIn("if (raw === null || raw.trim() === '') return null;", template)
        self.assertIn("if (isFinished) {\n            rowEntries.sort", template)
        self.assertIn("return b.points - a.points || a.originalIndex - b.originalIndex;", template)
        self.assertIn("if (a.points === null) return 1;", template)
        self.assertIn("if (b.points === null) return -1;", template)
        self.assertIn("const rowEntries = rows.map", template)
        self.assertNotIn("leader-gold", template)
        self.assertNotIn("leader-silver", template)
        self.assertNotIn("leader-blue", template)

    def test_points_use_semantic_categories_and_shared_large_geometry(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        match_template = (ROOT / "templates" / "match_predictions.html").read_text(encoding="utf-8")
        for category in (
            "points-exact-major", "points-exact", "points-difference", "points-near",
            "points-outcome", "points-fallback", "points-zero", "points-unavailable",
        ):
            self.assertIn(category, template)
            self.assertIn(".bets-compact-points." + category, css)
        self.assertIn("if (points === 11) return 'points-exact-major';", template)
        self.assertIn("if (points === 10) return 'points-exact';", template)
        self.assertIn("if (points === 7 || points === 8) return 'points-difference';", template)
        self.assertIn("if (points === 5) return 'points-near';", template)
        self.assertIn("if (points === 3) return 'points-outcome';", template)
        self.assertIn("if (points === 2) return 'points-fallback';", template)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 64px 66px", css)
        self.assertIn("width: 66px", css)
        self.assertIn("min-width: 66px", css)
        self.assertIn("height: 40px", css)
        self.assertIn("#f6c945", css)
        self.assertIn("#1769d2", css)
        self.assertIn("#22d88a", css)
        self.assertIn("#07965b", css)
        self.assertIn("#d9782d", css)
        self.assertIn("#9a3f18", css)
        self.assertIn("#169eaa", css)
        self.assertIn("rgba(93,111,142,0.90)", css)
        self.assertIn("color: #2b1b00", css)
        self.assertIn("color: #ffffff", css)
        self.assertIn("data-points=\"{{ p.points if p.points is not none else '' }}\"", match_template)
        self.assertNotIn("data-points=\"{{ p.points or 0 }}\"", match_template)
        self.assertIn(".bets-compact-score {", css)

    def test_five_and_three_point_categories_are_visually_distinct(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        near_start = css.index(".bets-compact-points.points-near")
        outcome_start = css.index(".bets-compact-points.points-outcome")
        near_css = css[near_start:outcome_start]
        outcome_css = css[outcome_start:css.index(".bets-compact-points.points-fallback")]
        self.assertIn("#22d88a", near_css)
        self.assertIn("#07965b", near_css)
        self.assertNotIn("#d9782d", near_css)
        self.assertIn("#d9782d", outcome_css)
        self.assertIn("#9a3f18", outcome_css)
        self.assertNotIn("#21ad68", css)
        self.assertNotIn("#2c944e", css)
        self.assertIn("color: #ffffff", near_css)
        self.assertIn("color: #ffffff", outcome_css)

    def test_unfinished_rows_keep_no_points_layout_and_order(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        self.assertIn("if (!isFinished) {\n                item.classList.add('no-points');", template)
        self.assertIn("if (isFinished) {\n                pointsNode = document.createElement('div');", template)
        self.assertIn(".bets-compact-row.no-points", css)
        self.assertNotIn(".bets-compact-row.leader-", css)

    def test_finished_match_card_visuals_are_scoped_to_finished_modifier(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "partials" / "home" / "_day_block.html").read_text(encoding="utf-8")
        self.assertIn("card_state = 'finished'", template)
        self.assertIn("rpl_class = 'match-card--rpl' if match.is_rpl_category and not is_rcup_match", template)
        self.assertIn("match-card-v2 {{ card_state }}", template)
        self.assertIn(".match-card-v2.finished .team-logo-v2", css)
        self.assertIn("width: 82px", css)
        self.assertIn("height: 82px", css)
        self.assertIn("width: 60px !important", css)
        self.assertIn("height: 60px !important", css)
        self.assertIn(".match-card-v2.finished .final-score", css)
        self.assertIn("font-size: 26px", css)
        self.assertIn(".match-card-v2.finished .status-done", css)
        self.assertIn("min-height: 25px", css)
        self.assertNotIn(".match-card-v2:not(.finished) .team-logo-v2 {\n             width: 82px", css)
        self.assertNotIn(".match-card-v2:not(.finished) .final-score", css)

    def test_rpl_finished_layer_wins_late_theme_logo_rules(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        selector = "body.tournament-rpl .match-card-v2.match-card--rpl.finished"
        layer_start = css.rindex("/* Authoritative RPL completed-card layer")
        layer = css[layer_start:]
        self.assertIn(selector + " .team-logo-v2", layer)
        self.assertIn(selector + " .team-logo-v2 img", layer)
        self.assertIn("width: 50px !important", layer)
        self.assertIn("max-width: 50px !important", layer)
        self.assertIn("object-fit: contain", layer)
        self.assertIn("width: 62px", layer)
        self.assertIn("width: 46px !important", layer)
        self.assertIn(selector + " .final-score", layer)
        self.assertIn("font-size: 32px", layer)
        self.assertIn(selector + " .status-done", layer)
        self.assertIn("background: #4e8b68 !important", layer)
        self.assertIn(selector + " .prediction-box", layer)
        self.assertNotIn("body.tournament-rpl .match-card-v2.match-card--rpl:not(.finished) .team-logo-v2", layer)

    def test_home_css_has_stable_cache_busting_version(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("home.css', v='wc2026-header-broadcast-20260725-rpl-finished'", template)

    def test_rpl_finished_background_is_dark_and_disables_light_overlays(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        marker = "/* Authoritative RPL completed-card layer"
        layer = css[css.rindex(marker):]
        normalized = re.sub(r"\s+", " ", layer)
        selector = "body.tournament-rpl .match-card-v2.match-card--rpl.finished"
        self.assertIn(selector, layer)
        self.assertIn("linear-gradient(145deg, rgba(15,27,58,0.96), rgba(24,42,78,0.94)) !important", normalized)
        self.assertIn("background-color: #0f1b3a !important", normalized)
        self.assertIn(selector + "::before", layer)
        self.assertIn(selector + "::after", layer)
        self.assertIn("content: none !important", normalized)
        self.assertNotIn("radial-gradient(circle at 50% -12%", normalized)
        self.assertNotIn("rgba(255,255,255,0.92)", normalized)
        generic_start = css.index(".match-card-v2 {")
        self.assertLess(generic_start, css.rindex(selector))

    def test_rpl_finished_text_colors_are_scoped_and_contrast_aware(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        marker = "/* Completed RPL text contrast: colors only"
        layer = css[css.rindex(marker):]
        selector = "body.tournament-rpl .match-card-v2.match-card--rpl.finished"
        for child in (
            ".team-name-v2", ".center-sub", ".final-score", ".status-done",
            ".points-v2.points-emerald", ".points-v2.points-cyan", ".points-v2.points-zero",
            ".prediction-label-finished", ".prediction-score-finished", ".bets-link-v2",
        ):
            self.assertIn(selector + " " + child, layer)
        self.assertIn("color: rgba(255,255,255,0.96)", layer)
        self.assertIn("color: rgba(255,255,255,0.72)", layer)
        self.assertIn("color: #0d5c31", layer)
        self.assertIn("color: #075b80", layer)
        self.assertIn("color: #33465d", layer)
        self.assertIn("outline-color: rgba(183,220,255,0.96)", layer)
        self.assertNotIn(".match-card-v2:not(.finished) .team-name-v2", layer)
        self.assertNotIn("body.tournament-rcup", layer)

    def test_bets_link_keeps_existing_markup_and_sheet_behavior(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        finished = (ROOT / "templates" / "partials" / "home" / "_match_finished.html").read_text(encoding="utf-8")
        active = (ROOT / "templates" / "partials" / "home" / "_match_active.html").read_text(encoding="utf-8")
        self.assertIn('class="bets-link-v2"', finished)
        self.assertIn('class="bets-link-v2"', active)
        self.assertIn('data-bets-sheet', finished)
        self.assertIn('data-bets-sheet', active)

    def test_rpl_closed_finished_bets_link_is_compact_and_theme_scoped(self):
        css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")
        marker = "/* RPL closed/completed CTA:"
        layer = css[css.rindex(marker):]
        self.assertIn("body.tournament-rpl .match-card-v2.finished .bets-link-v2", layer)
        self.assertIn("body.tournament-rpl .match-card-v2.closed .bets-link-v2", layer)
        self.assertIn("display: inline-flex", layer)
        self.assertIn("width: auto", layer)
        self.assertIn("max-width: max-content", layer)
        self.assertIn("flex: 0 0 auto", layer)
        self.assertIn("min-height: 38px", layer)
        self.assertIn("padding: 0 16px", layer)
        self.assertIn("background: linear-gradient(180deg, #2f6fd6 0%, #174a9b 100%) !important", layer)
        self.assertIn("color: #ffffff !important", layer)
        self.assertIn(".bets-link-v2:hover", layer)
        self.assertIn(".bets-link-v2:active", layer)
        self.assertIn(".bets-link-v2:focus-visible", layer)
        self.assertIn("min-height: 36px", layer)
        self.assertNotIn("width: 100%", layer)
        self.assertNotIn("flex: 1", layer)


if __name__ == "__main__":
    unittest.main()
