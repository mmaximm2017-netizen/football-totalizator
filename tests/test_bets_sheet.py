import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HOME_CSS_FILES = [
    ROOT / "static" / "css" / "home" / "base.css",
    ROOT / "static" / "css" / "home" / "components" / "bets-sheet-base.css",
    ROOT / "static" / "css" / "home" / "base-responsive.css",
    ROOT / "static" / "css" / "home" / "wc2026.css",
    ROOT / "static" / "css" / "home" / "rpl-base.css",
    ROOT / "static" / "css" / "home" / "match-cards-v2.css",
    ROOT / "static" / "css" / "home" / "components" / "bets-sheet-teams.css",
    ROOT / "static" / "css" / "home" / "match-cards-v2-controls.css",
    ROOT / "static" / "css" / "home" / "components" / "skeleton.css",
    ROOT / "static" / "css" / "home" / "match-cards-v2-wc-state.css",
    ROOT / "static" / "css" / "home" / "components" / "bets-sheet-wc2026.css",
    ROOT / "static" / "css" / "home" / "match-cards-v2-responsive.css",
    ROOT / "static" / "css" / "home" / "final-overrides.css",
    ROOT / "static" / "css" / "home" / "components" / "bets-sheet-mobile.css",
    ROOT / "static" / "css" / "home" / "final-overrides-rpl.css",
    ROOT / "static" / "css" / "home" / "components" / "bets-sheet-rpl.css",
    ROOT / "static" / "css" / "home" / "final-overrides-finished.css",
    ROOT / "static" / "css" / "home" / "rpl-performance.css",


def read_home_css():
    return "".join(path.read_text(encoding="utf-8") for path in HOME_CSS_FILES)


class BetsSheetUiTests(unittest.TestCase):
    def test_sheet_renders_shared_themable_structure(self):
        partial = (ROOT / "templates" / "partials" / "home" / "_bets_sheet.html").read_text(encoding="utf-8")
        self.assertIn("predictions-sheet", partial)
        self.assertIn("predictions-sheet__header", partial)
        self.assertIn("predictions-sheet__close", partial)
        self.assertIn("data-bets-sheet-content", partial)

    def test_mobile_sheet_accounts_for_bottom_nav_safe_area_and_last_row(self):
        css = read_home_css()
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("box-sizing: border-box", css)
        self.assertIn("--bets-sheet-bottom-clearance", css)
        self.assertIn("calc(100dvh - var(--bets-sheet-bottom-clearance, 86px) - 16px)", css)
        self.assertIn("body.tournament-wc2026 .bets-sheet", css)
        self.assertIn("padding-bottom: calc(18px + env(safe-area-inset-bottom))", css)
        self.assertIn("scroll-padding-bottom: calc(18px + env(safe-area-inset-bottom))", css)
        self.assertIn(".bets-compact-list {\n            padding-bottom: 10px;", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("function syncBetsSheetBottomClearance()", template)
        self.assertIn("bottomNav.getBoundingClientRect()", template)
        self.assertIn("window.innerHeight - navRect.top + 8", template)
        self.assertNotIn("window.visualViewport.height + window.visualViewport.offsetTop", template)
        self.assertIn("syncBetsSheetBottomClearance();", template)
        self.assertIn(
            "window.visualViewport.addEventListener('resize', syncBetsSheetBottomClearance)",
            template,
        )

    def test_android_webview_neutralizes_page_transition_fixed_containing_block(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("const isAndroidWebView =", template)
        self.assertNotIn("/Android/i.test(userAgent)", template)
        self.assertIn("document.getElementById('page-transition')", template)
        self.assertIn("function neutralizeBetsSheetContainingBlock()", template)
        self.assertIn("neutralizeBetsSheetContainingBlock();", template)
        self.assertIn(
            "pageTransition.style.setProperty('position', 'static', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('transform', 'none', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('perspective', 'none', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('contain', 'none', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('content-visibility', 'visible', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('will-change', 'auto', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('overflow', 'visible', 'important')",
            template,
        )
        self.assertIn(
            "pageTransition.style.setProperty('isolation', 'auto', 'important')",
            template,
        )

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
        home_css = read_home_css()
        cup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .bets-sheet", home_css)
        self.assertIn("body.tournament-rpl .bets-compact-team-icon", home_css)
        self.assertIn("body.tournament-rcup .bets-sheet", cup_css)
        self.assertIn("body.tournament-rcup .bets-compact-team-icon", cup_css)

    def test_mobile_sheet_has_bounded_logo_and_team_layout(self):
        css = read_home_css()
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
        css = read_home_css()
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
        css = read_home_css()
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
        css = read_home_css()
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
        css = read_home_css()
        self.assertIn("if (!isFinished) {\n                item.classList.add('no-points');", template)
        self.assertIn("if (isFinished) {\n                pointsNode = document.createElement('div');", template)
        self.assertIn(".bets-compact-row.no-points", css)
        self.assertNotIn(".bets-compact-row.leader-", css)

    def test_finished_match_card_visuals_are_scoped_to_finished_modifier(self):
        css = read_home_css()
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

    def test_rpl_team_stack_resets_legacy_name_geometry(self):
        css = read_home_css()
        layer = css[css.rindex("/* Standard RPL matches use a compact broadcast-panel treatment."):]
        stack_selector = "body.tournament-rpl .match-card-v2.match-card-v2--rpl:not(.finished) .team-v2"
        name_selector = "body.tournament-rpl .match-card-v2.match-card-v2--rpl:not(.finished) .team-name-v2"

        self.assertIn(stack_selector, layer)
        self.assertIn("display: flex !important", layer)
        self.assertIn("justify-content: center !important", layer)
        self.assertIn("align-items: center !important", layer)
        self.assertIn("height: 100%", layer)
        self.assertIn("min-height: 0", layer)
        self.assertIn(name_selector, layer)
        name_start = layer.index(name_selector)
        name_block = layer[name_start:layer.index("}", name_start)]
        self.assertIn("position: static", layer)
        self.assertIn("inset: auto", layer)
        self.assertIn("bottom: auto", layer)
        self.assertIn("transform: none", layer)
        self.assertIn("margin: 0", name_block)
        self.assertIn("padding: 0", name_block)
        self.assertIn("align-self: center", layer)
        self.assertIn("place-items: start center", name_block)
        self.assertIn("background: transparent !important", name_block)
        self.assertEqual(name_block.count("background:"), 1)
        self.assertIn("box-shadow: none !important", name_block)
        self.assertIn(".match-card-v2--rpl .team-name-v2::before", layer)
        self.assertIn("content: none", layer)

    def test_rpl_finished_layer_wins_late_theme_logo_rules(self):
        css = read_home_css()
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
        for path in HOME_CSS_FILES:
            relative = path.relative_to(ROOT / "static").as_posix()
            self.assertIn(f"filename='{relative}', v='home-components-20260903'", template)

    def test_rpl_finished_background_is_dark_and_disables_light_overlays(self):
        css = read_home_css()
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
        css = read_home_css()
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
        css = read_home_css()
        finished = (ROOT / "templates" / "partials" / "home" / "_match_finished.html").read_text(encoding="utf-8")
        active = (ROOT / "templates" / "partials" / "home" / "_match_active.html").read_text(encoding="utf-8")
        self.assertIn('class="bets-link-v2"', finished)
        self.assertIn('class="bets-link-v2"', active)
        self.assertIn('data-bets-sheet', finished)
        self.assertIn('data-bets-sheet', active)

    def test_rpl_closed_finished_bets_link_is_compact_and_theme_scoped(self):
        css = read_home_css()
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

    def test_rpl_closed_status_is_a_compact_red_scoped_badge(self):
        css = read_home_css()
        marker = "/* RPL closed status:"
        layer = css[css.rindex(marker):]
        selector = "body.tournament-rpl .match-card-v2.match-card--rpl.closed .status-closed"
        self.assertIn(selector, layer)
        self.assertIn("display: inline-flex", layer)
        self.assertIn("width: auto", layer)
        self.assertIn("max-width: max-content", layer)
        self.assertIn("flex: 0 0 auto", layer)
        self.assertIn("min-height: 30px", layer)
        self.assertIn("padding: 0 14px", layer)
        self.assertIn("background: linear-gradient(180deg, #d64242 0%, #9b2020 100%)", layer)
        self.assertIn("color: #ffffff", layer)
        self.assertIn("opacity: 1", layer)
        self.assertIn("min-height: 28px", layer)
        self.assertIn("padding: 0 12px", layer)
        self.assertIn(":hover", layer)
        self.assertIn(":active", layer)
        self.assertIn(":focus-visible", layer)
        self.assertNotIn("body.tournament-rcup", layer)
        self.assertNotIn("status-done", layer)

    def test_russian_cup_user_cards_have_scoped_state_layer(self):
        home_template = (ROOT / "templates" / "partials" / "home" / "_day_block.html").read_text(encoding="utf-8")
        base_template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        rcup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        layer = rcup_css[rcup_css.rindex("/* User-facing Russian Cup card layer."):]
        layer = layer.split("body.tournament-rcup .admin-russian-cup-page .rc-compact-card--pending", 1)[0]
        self.assertIn("russian_cup_class = 'match-card--russian-cup' if is_rcup_match", home_template)
        self.assertIn("card_state = 'finished'", home_template)
        self.assertIn("card_state = 'closed'", home_template)
        self.assertIn("class=\"match-card {{ russian_cup_class }} match-card-v2 {{ card_state }}", home_template)
        self.assertIn("russian-cup.css', v='20260814-russian-cup-ui'", base_template)
        self.assertIn("body.tournament-rcup .match-card-v2.match-card--russian-cup.finished", layer)
        self.assertIn("body.tournament-rcup .match-card-v2.match-card--russian-cup.closed", layer)
        self.assertIn("linear-gradient(145deg, rgba(45,19,52,0.97), rgba(76,25,46,0.95))", layer)
        self.assertIn("content: none", layer)
        self.assertIn("width: 70px !important", layer)
        self.assertIn("width: 50px !important", layer)
        self.assertIn("width: 62px !important", layer)
        self.assertIn("width: 46px !important", layer)
        self.assertIn("font-size: 32px", layer)
        self.assertIn("font-size: 30px", layer)
        self.assertIn("status-done", layer)
        self.assertIn("display: inline-flex", layer)
        self.assertIn("width: auto", layer)
        self.assertIn("flex: 0 0 auto", layer)
        self.assertIn("background: linear-gradient(180deg, #8f1745 0%, #5b1039 100%)", layer)
        self.assertIn(".bets-link-v2:hover", layer)
        self.assertIn(".bets-link-v2:active", layer)
        self.assertIn(".bets-link-v2:focus-visible", layer)
        self.assertNotIn("width: 100%", layer)
        self.assertNotIn("flex: 1", layer)
        self.assertNotIn("body.tournament-rpl", layer)
        self.assertNotIn("admin-russian-cup-page", layer)

    def test_russian_cup_closed_status_is_a_compact_scoped_badge(self):
        rcup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        marker = "/* User-facing RCup closed status:"
        layer = rcup_css[rcup_css.rindex(marker):]
        selector = "body.tournament-rcup .match-card-v2.match-card--russian-cup.closed .status-closed"
        self.assertIn(selector, layer)
        self.assertIn("display: inline-flex", layer)
        self.assertIn("width: auto", layer)
        self.assertIn("max-width: max-content", layer)
        self.assertIn("flex: 0 0 auto", layer)
        self.assertIn("min-height: 30px", layer)
        self.assertIn("padding: 0 14px", layer)
        self.assertIn("background: linear-gradient(180deg, #d64242 0%, #9b2020 100%)", layer)
        self.assertIn("color: #ffffff", layer)
        self.assertIn("opacity: 1", layer)
        self.assertIn("min-height: 28px", layer)
        self.assertIn("padding: 0 12px", layer)
        self.assertIn(":hover", layer)
        self.assertIn(":active", layer)
        self.assertIn(":focus-visible", layer)
        self.assertNotIn("body.tournament-rpl", layer)
        self.assertNotIn("admin-russian-cup-page", layer)


if __name__ == "__main__":
    unittest.main()
