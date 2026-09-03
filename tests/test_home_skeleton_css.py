from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skeleton_pulse_styles_are_shared_once():
    css = (ROOT / "static" / "css" / "home.css").read_text(encoding="utf-8")

    shared = """    .skeleton-header .skeleton-icon,
    .skeleton-flag,
    .skeleton-input,
    .skeleton-divider,
    .skeleton-text,
    .skeleton-button {
        background: #c0c0c0;
        animation: pulse 1.5s ease-in-out infinite;
    }"""

    assert shared in css
    assert css.count("animation: pulse 1.5s ease-in-out infinite;") == 1
    assert css.count("background: #c0c0c0;") == 1
