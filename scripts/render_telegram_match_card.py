#!/usr/bin/env python3
"""Render and optionally test-send a standalone TOTISH Telegram match card."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.team_data import CLUB_LOGOS

try:
    from host_telegram_notifier import load_env
except ModuleNotFoundError:
    from scripts.host_telegram_notifier import load_env


CARD_SIZE = (1000, 490)
LOGO_BOX = 178
CAPTION = "TOTISH Telegram match card prototype"
TOURNAMENT_LOGOS = {
    "Чемпионат России 🇷🇺": ROOT / "static" / "clubs" / "russian-premier-league-footballlogos-org.png",
    "РПЛ": ROOT / "static" / "clubs" / "russian-premier-league-footballlogos-org.png",
    "Кубок России": ROOT / "static" / "clubs" / "Fonbet_Russian_Cup.png",
}


def tournament_display_name(name):
    return "РПЛ" if name == "Чемпионат России 🇷🇺" else name


def _font_path(bold=False):
    configured = os.getenv("TOTISH_CARD_FONT_BOLD" if bold else "TOTISH_CARD_FONT")
    candidates = [
        configured,
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    return next((Path(path) for path in candidates if path and Path(path).is_file()), None)


def _font(size, bold=False):
    path = _font_path(bold)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default(size=size)


def resolve_team_logo(team_name):
    public_path = CLUB_LOGOS.get(team_name)
    if not public_path or not public_path.startswith("/static/"):
        return None
    local_path = ROOT / public_path.removeprefix("/")
    if local_path.suffix.lower() == ".svg":
        raster_fallback = Path(str(local_path) + ".png")
        return raster_fallback if raster_fallback.is_file() else None
    return local_path if local_path.is_file() else None


def resolve_tournament_logo(tournament_name):
    path = TOURNAMENT_LOGOS.get(tournament_name)
    return path if path and path.is_file() else None


def _load_contained(path, box_size):
    if path is None:
        return None
    try:
        image = Image.open(path).convert("RGBA")
    except (OSError, ValueError):
        return None
    image.thumbnail((box_size, box_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((box_size - image.width) // 2, (box_size - image.height) // 2))
    return canvas


def _gradient_background(size):
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            int(12 + 8 * ratio),
            int(25 + 16 * ratio),
            int(52 + 30 * ratio),
        )
        for x in range(width):
            pixels[x, y] = color
    return image


def _fit_font(draw, text, max_width, initial_size, bold=True, minimum=24):
    size = initial_size
    while size > minimum:
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(minimum, bold=bold)


def _draw_centered(draw, center_x, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)


def render_match_card(
    tournament_name,
    home_team,
    away_team,
    display_time,
    predicted_count,
    participant_count,
    output_path,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = _gradient_background(CARD_SIZE)
    glow = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((220, 50, 780, 540), fill=(37, 99, 235, 65))
    image = Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(70)))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((36, 24, 964, 466), radius=38, fill=(13, 26, 49, 225), outline=(72, 105, 154, 130), width=2)
    tournament_logo = _load_contained(resolve_tournament_logo(tournament_name), 58)
    tournament_text = tournament_display_name(tournament_name)
    badge_width = 108 + draw.textlength(tournament_text, font=_font(27, bold=True))
    badge_left = (CARD_SIZE[0] - badge_width) / 2
    draw.rounded_rectangle((badge_left, 42, badge_left + badge_width, 106), radius=23, fill=(23, 48, 84, 240))
    if tournament_logo:
        image.alpha_composite(tournament_logo, (int(badge_left + 16), 45))
    draw.text((badge_left + 82, 59), tournament_text, font=_font(27, bold=True), fill=(235, 243, 255, 255))

    home_logo = _load_contained(resolve_team_logo(home_team), LOGO_BOX)
    away_logo = _load_contained(resolve_team_logo(away_team), LOGO_BOX)
    logo_y = 132
    if home_logo:
        image.alpha_composite(home_logo, (56, logo_y))
    if away_logo:
        image.alpha_composite(away_logo, (766, logo_y))

    home_font = _fit_font(draw, home_team, 220, 46)
    away_font = _fit_font(draw, away_team, 220, 46)
    _draw_centered(draw, 335, 192, home_team, home_font, (255, 255, 255, 255))
    _draw_centered(draw, 665, 192, away_team, away_font, (255, 255, 255, 255))
    _draw_centered(draw, 500, 185, "—", _font(52, bold=True), (121, 155, 204, 255))

    _draw_centered(draw, 500, 310, display_time, _font(44, bold=True), (255, 204, 73, 255))
    prediction_text = f"Прогнозы: {predicted_count}/{participant_count}"
    _draw_centered(draw, 500, 372, prediction_text, _font(33), (183, 205, 235, 255))

    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def send_test_photo(image_path, caption=CAPTION):
    env = load_env()
    token = env.get("TELEGRAM_ERROR_BOT_TOKEN") or os.getenv("TELEGRAM_ERROR_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_ERROR_CHAT_ID") or os.getenv("TELEGRAM_ERROR_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram prototype credentials are not configured")

    boundary = f"----totish-{uuid.uuid4().hex}"
    image_data = Path(image_path).read_bytes()
    chunks = []
    for name, value in (("chat_id", str(chat_id)), ("caption", caption)):
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"totish_match_card.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
        + image_data
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError("Telegram sendPhoto failed")
    return payload.get("result")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tournament", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--time", required=True)
    parser.add_argument("--predicted", required=True, type=int)
    parser.add_argument("--participants", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--send-test", action="store_true")
    args = parser.parse_args(argv)

    output = render_match_card(
        args.tournament,
        args.home,
        args.away,
        args.time,
        args.predicted,
        args.participants,
        args.output,
    )
    print(output)
    if args.send_test:
        send_test_photo(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
