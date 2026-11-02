"""Player identity colors must agree in standings and scorers, without changing data."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from flask import Flask, render_template


@pytest.fixture
def app():
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.add_url_rule("/players/<int:user_id>", "profile.public_profile", lambda user_id: "")
    return app


def render_player(app, username, **extra):
    row = dict(place=1, username=username, points=404, shared=False,
               movement=None, leader_status=None, outsider_status=None, **extra)
    with app.test_request_context("/"):
        return render_template("table_content.html", table=[row],
                               top_scorers=[dict(place=1, username=username, scorer_goals=16)],
                               selected_name="Чемпионат России 🇷🇺", selected_tid=5)


@pytest.mark.parametrize("username,colors", [
    ("Макс Зенит", ["#111111", "#145DCC"]),
    ("Алексей конь", ["#D52B37", "#FFDB18"]),
    ("Byza-Zenit", ["#111111", "#FFFFFF"]),
    ("БЕК125125", ["#D52B37", "#FFFFFF"]),
    ("Bowb", ["#145DCC", "#58C4F2"]),
    ("Денис 05", ["#FFDB18", "#169449"]),
])
def test_colors_precede_name_in_both_tables(app, username, colors):
    html = render_player(app, username, user_id=42)
    assert html.count('class="player-colors"') == 2
    for name_class in ("player-name", "top-scorer-name"):
        match = re.search(r'class="' + name_class + r'">(<svg.*?</svg>)'
                          r'<span class="player-name-text">' + re.escape(username), html, re.S)
        assert match, f"Missing left marker for {username} in {name_class}"
        marker = ET.fromstring(match.group(1))
        assert marker.attrib["aria-hidden"] == "true"
        assert marker.attrib["focusable"] == "false"
        paths = list(marker)
        assert [path.attrib["fill"] for path in paths] == colors
        assert paths[0].attrib["d"] == paths[1].attrib["d"]
        for path in paths:
            if path.attrib["fill"] == "#FFFFFF":
                assert path.attrib.get("stroke")
    assert 'href="/players/42?tid=5"' in html
    assert '<span class="points-number">404</span>' in html
    assert '<span class="top-scorer-goals">⚽ 16</span>' in html


def test_unmapped_player_keeps_escaped_name_without_marker(app):
    html = render_player(app, '<Unknown & "player">')
    assert 'class="player-colors"' not in html
    assert html.count('&lt;Unknown &amp; &#34;player&#34;&gt;') == 2
    assert '<span class="player-name">' in html
