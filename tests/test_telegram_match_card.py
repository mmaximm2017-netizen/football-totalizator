import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.services import telegram_match_card_service as renderer
from scripts import render_telegram_match_card as card


def render(tmp_path, home="Акрон", away="ЦСКА"):
    output = tmp_path / "card.png"
    card.render_match_card(
        "Чемпионат России 🇷🇺",
        home,
        away,
        "18:00",
        6,
        6,
        output,
    )
    return output


def test_generates_telegram_sized_png(tmp_path):
    output = render(tmp_path)

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == card.CARD_SIZE
        assert image.mode == "RGB"


def test_unknown_team_logo_falls_back_to_text_without_crash(tmp_path):
    output = render(tmp_path, home="Неизвестный клуб")

    assert output.is_file()
    assert card.resolve_team_logo("Неизвестный клуб") is None


def test_known_club_and_tournament_logos_are_local_files():
    for team in ("Акрон", "ЦСКА", "Зенит", "Родина"):
        path = card.resolve_team_logo(team)
        assert path is not None
        assert path.is_file()
        assert card.ROOT in path.parents

    assert card.resolve_tournament_logo("Чемпионат России 🇷🇺").is_file()
    assert card.resolve_tournament_logo("Кубок России").is_file()


def test_rendering_never_uses_network(tmp_path):
    with patch.object(card, "urlopen", side_effect=AssertionError("network not allowed")):
        output = render(tmp_path)

    assert output.is_file()


def test_cli_generates_preview_without_sending(tmp_path):
    output = tmp_path / "preview.png"
    with patch.object(card, "send_test_photo") as send:
        result = card.main(
            [
                "--tournament",
                "Кубок России",
                "--home",
                "Акрон",
                "--away",
                "ЦСКА",
                "--time",
                "18:00",
                "--predicted",
                "6",
                "--participants",
                "6",
                "--output",
                str(output),
            ]
        )

    assert result == 0
    assert output.is_file()
    send.assert_not_called()


def test_test_send_requires_explicit_flag(tmp_path):
    output = tmp_path / "send.png"
    with patch.object(card, "send_test_photo") as send:
        card.main(
            [
                "--tournament",
                "РПЛ",
                "--home",
                "Акрон",
                "--away",
                "ЦСКА",
                "--time",
                "18:00",
                "--predicted",
                "6",
                "--participants",
                "6",
                "--output",
                str(output),
                "--send-test",
            ]
        )

    send.assert_called_once_with(Path(output))


def test_today_renderer_uses_minimal_dynamic_pages_and_local_assets(tmp_path):
    matches = []
    for index in range(4):
        matches.append(
            {
                "tournament_name": "Чемпионат России 🇷🇺",
                "home_team": "Акрон",
                "away_team": "ЦСКА",
                "kickoff_time": f"2026-08-28T{12 + index:02d}:00:00+00:00",
                "deadline_open": True,
                "participants": [{"username": "Игрок", "has_prediction": True}],
                "predictions": [],
            }
        )

    with patch.object(renderer, "urlopen", create=True, side_effect=AssertionError("network not allowed")):
        pages = renderer.render_today_cards(matches, tmp_path)

    assert len(pages) == 2
    for page in pages:
        with Image.open(page) as image:
            assert image.format == "PNG"
            assert image.width == renderer.TODAY_WIDTH
            assert image.height < 1800


def test_today_renderer_unknown_logo_is_safe(tmp_path):
    matches = [
        {
            "tournament_name": "Неизвестный турнир",
            "home_team": "Неизвестный клуб",
            "away_team": "ЦСКА",
            "kickoff_time": "2026-08-28T15:00:00+00:00",
            "deadline_open": False,
            "participants": [],
            "predictions": [{"username": "Игрок", "home_goals": None, "away_goals": None}],
        }
    ]

    pages = renderer.render_today_cards(matches, tmp_path)

    assert len(pages) == 1
    assert pages[0].is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not enforceable on Windows")
def test_today_renderer_sets_host_read_and_cleanup_permissions(tmp_path):
    output_dir = tmp_path / "admin-cards"
    matches = [
        {
            "tournament_name": "РПЛ",
            "home_team": "Акрон",
            "away_team": "ЦСКА",
            "kickoff_time": "2026-08-28T15:00:00+00:00",
            "deadline_open": True,
            "participants": [],
            "predictions": [],
        }
    ]

    page = renderer.render_today_cards(matches, output_dir)[0]

    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o733
    assert stat.S_IMODE(page.stat().st_mode) == 0o644
