from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HOME_CSS_FILES = [
    "static/css/home/base.css",
    "static/css/home/wc2026.css",
    "static/css/home/rpl-base.css",
    "static/css/home/match-cards-v2.css",
    "static/css/home/final-overrides.css",
    "static/css/home/rpl-performance.css",
]


def test_home_css_split_preserves_full_source_length_and_order():
    combined = "".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in HOME_CSS_FILES
    )

    assert len(combined) == 246650

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

    positions = [template.index(path) for path in HOME_CSS_FILES]
    assert positions == sorted(positions)
    assert "css/home.css" not in template
