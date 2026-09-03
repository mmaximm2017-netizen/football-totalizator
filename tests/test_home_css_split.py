from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HOME_CSS_FILES = [
    "static/css/home/base.css",
    "static/css/home/components/bets-sheet-base.css",
    "static/css/home/base-responsive.css",
    "static/css/home/wc2026.css",
    "static/css/home/rpl-base.css",
    "static/css/home/match-cards-v2.css",
    "static/css/home/components/bets-sheet-teams.css",
    "static/css/home/match-cards-v2-controls.css",
    "static/css/home/components/skeleton.css",
    "static/css/home/match-cards-v2-wc-state.css",
    "static/css/home/components/bets-sheet-wc2026.css",
    "static/css/home/match-cards-v2-responsive.css",
    "static/css/home/final-overrides.css",
    "static/css/home/components/bets-sheet-mobile.css",
    "static/css/home/final-overrides-rpl.css",
    "static/css/home/components/bets-sheet-rpl.css",
    "static/css/home/final-overrides-finished.css",
    "static/css/home/rpl-performance.css",


def test_home_css_split_preserves_full_source_length_and_order():
    combined = "".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in HOME_CSS_FILES
    )

    assert len(combined.splitlines()) == 7325

    markers = [
        "WC2026 V2 FINAL HOME CARD LAYER",
        "RPL tournament background: page layer only",
        "Match Card Layout System V2",
        "Shared RPL card metrics for the Russian Cup card.",
        "RPL scroll performance hardening",
    ]
    positions = [combined.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_home_template_loads_split_css_in_source_order():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    template_paths = [path.removeprefix("static/") for path in HOME_CSS_FILES]
    positions = [template.index(path) for path in template_paths]
    assert positions == sorted(positions)
    assert "css/home.css" not in template
