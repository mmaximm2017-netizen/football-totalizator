from pathlib import Path
from unittest.mock import patch

from PIL import Image

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
