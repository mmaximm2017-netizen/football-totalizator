from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_home_css_structure_markers_stay_in_expected_order():
    css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")

    markers = [
        "TOTISH HOME CSS — STRUCTURE MAP",
        "COMPONENT: BETS SHEET — BASE LAYER",
        "THEME: WORLD CUP 2026 — PRIMARY HOME LAYER",
        "THEME: RPL — BASE LAYER",
        "COMPONENT: MATCH CARD V2 — SHARED LAYOUT SYSTEM",
        "COMPONENT: SKELETON LOADER",
        "COMPONENT: BETS SHEET — WORLD CUP 2026 THEME",
        "FINAL OVERRIDES — SHARED RPL / RUSSIAN CUP METRICS",
        "STATE: RPL FINISHED / CLOSED — AUTHORITATIVE LAYER",
        "COMPONENT: BETS SHEET — RPL FINAL THEME",
        "PERFORMANCE: RPL SCROLL HARDENING",
    ]

    positions = [css.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_home_page_still_uses_single_home_stylesheet():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "css/home.css" in template
    assert "css/home/base.css" not in template
    assert "css/home/match-cards-v2.css" not in template



def test_world_cup_card_overlay_has_single_authoritative_definition():
    css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")

    assert css.count("body.tournament-wc2026 .match-card-v2::before {") == 1
    assert css.count("body.tournament-wc2026 .match-card-v2 > * {") == 1



def test_world_cup_bets_link_has_single_active_rule():
    css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")

    assert css.count("body.tournament-wc2026 .match-card-v2 .bets-link-v2:active {") == 1
