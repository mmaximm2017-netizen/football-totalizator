"""Local-asset Pillow renderers for Telegram football cards."""

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.models.team_data import CLUB_LOGOS

ROOT = Path(__file__).resolve().parents[2]
MSK = ZoneInfo("Europe/Moscow")
CARD_SIZE = (1000, 490)
LOGO_BOX = 178
TODAY_WIDTH = 1000
TODAY_MATCHES_PER_PAGE = 3
TOURNAMENT_LOGOS = {
    "Чемпионат России 🇷🇺": ROOT / "static" / "clubs" / "russian-premier-league-footballlogos-org.png",
    "РПЛ": ROOT / "static" / "clubs" / "russian-premier-league-footballlogos-org.png",
    "Кубок России": ROOT / "static" / "clubs" / "Fonbet_Russian_Cup.png",
}
RU_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


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
        color = (int(12 + 8 * ratio), int(25 + 16 * ratio), int(52 + 30 * ratio))
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


def render_match_card(tournament_name, home_team, away_team, display_time, predicted_count, participant_count, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = _gradient_background(CARD_SIZE)
    glow = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((220, 50, 780, 540), fill=(37, 99, 235, 65))
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
    if home_logo:
        image.alpha_composite(home_logo, (56, 132))
    if away_logo:
        image.alpha_composite(away_logo, (766, 132))
    _draw_centered(draw, 335, 192, home_team, _fit_font(draw, home_team, 220, 46), (255, 255, 255, 255))
    _draw_centered(draw, 665, 192, away_team, _fit_font(draw, away_team, 220, 46), (255, 255, 255, 255))
    _draw_centered(draw, 500, 185, "—", _font(52, bold=True), (121, 155, 204, 255))
    _draw_centered(draw, 500, 310, display_time, _font(44, bold=True), (255, 204, 73, 255))
    _draw_centered(draw, 500, 372, f"Прогнозы: {predicted_count}/{participant_count}", _font(33), (183, 205, 235, 255))
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def _match_block_height(match):
    return 300 if match.get("deadline_open") else 270 + max(1, len(match.get("predictions", []))) * 36


def _draw_today_match(image, draw, match, top):
    bottom = top + _match_block_height(match) - 18
    draw.rounded_rectangle((38, top, 962, bottom), radius=30, fill=(13, 26, 49, 232), outline=(64, 96, 143, 150), width=2)
    tournament_logo = _load_contained(resolve_tournament_logo(match["tournament_name"]), 44)
    if tournament_logo:
        image.alpha_composite(tournament_logo, (68, top + 20))
    draw.text((122, top + 28), tournament_display_name(match["tournament_name"]), font=_font(24, bold=True), fill=(220, 233, 250, 255))
    home_logo = _load_contained(resolve_team_logo(match["home_team"]), 104)
    away_logo = _load_contained(resolve_team_logo(match["away_team"]), 104)
    if home_logo:
        image.alpha_composite(home_logo, (62, top + 78))
    if away_logo:
        image.alpha_composite(away_logo, (834, top + 78))
    _draw_centered(draw, 350, top + 108, match["home_team"], _fit_font(draw, match["home_team"], 250, 38), (255, 255, 255, 255))
    _draw_centered(draw, 650, top + 108, match["away_team"], _fit_font(draw, match["away_team"], 250, 38), (255, 255, 255, 255))
    _draw_centered(draw, 500, top + 104, "—", _font(42, bold=True), (121, 155, 204, 255))
    kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK)
    _draw_centered(draw, 500, top + 166, kickoff.strftime("%H:%M"), _font(34, bold=True), (255, 204, 73, 255))
    participants = match.get("participants", [])
    placed = sum(1 for item in participants if item.get("has_prediction"))
    if match.get("deadline_open"):
        _draw_centered(draw, 500, top + 214, "🔒 Прогнозы закрыты", _font(25, bold=True), (179, 199, 225, 255))
        _draw_centered(draw, 500, top + 252, f"Поставили: {placed}/{len(participants)}", _font(24), (179, 199, 225, 255))
    else:
        draw.text((90, top + 214), "🔓 Прогнозы открыты", font=_font(24, bold=True), fill=(87, 220, 159, 255))
        y = top + 254
        for prediction in match.get("predictions", []):
            score = "нет прогноза" if prediction["home_goals"] is None or prediction["away_goals"] is None else f"{prediction['home_goals']}:{prediction['away_goals']}"
            draw.text((92, y), f"{prediction['username']} — {score}", font=_font(22), fill=(222, 232, 247, 255))
            y += 36


def render_today_cards(matches, output_dir, date_value=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o733)
    os.chmod(output_dir, 0o733)
    cutoff = time.time() - 3600
    for stale in output_dir.glob("today-*.png"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass
    date_value = date_value or datetime.now(MSK).date()
    pages = []
    for page_index in range(0, len(matches), TODAY_MATCHES_PER_PAGE):
        page_matches = matches[page_index : page_index + TODAY_MATCHES_PER_PAGE]
        height = 126 + sum(_match_block_height(match) for match in page_matches) + 24
        image = _gradient_background((TODAY_WIDTH, height)).convert("RGBA")
        draw = ImageDraw.Draw(image)
        draw.text((52, 34), "⚽ СЕГОДНЯ", font=_font(38, bold=True), fill=(255, 255, 255, 255))
        draw.text((52, 80), f"{date_value.day} {RU_MONTHS[date_value.month]}", font=_font(24), fill=(174, 198, 231, 255))
        top = 126
        for match in page_matches:
            _draw_today_match(image, draw, match, top)
            top += _match_block_height(match)
        output = output_dir / f"today-{uuid.uuid4().hex}.png"
        image.convert("RGB").save(output, format="PNG", optimize=True)
        os.chmod(output, 0o644)
        pages.append(output)
    return pages
