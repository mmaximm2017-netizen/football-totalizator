#!/usr/bin/env python3
"""Render and optionally test-send a standalone TOTISH Telegram match card."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import telegram_match_card_service as renderer

CARD_SIZE = renderer.CARD_SIZE
LOGO_BOX = renderer.LOGO_BOX
render_match_card = renderer.render_match_card
resolve_team_logo = renderer.resolve_team_logo
resolve_tournament_logo = renderer.resolve_tournament_logo
tournament_display_name = renderer.tournament_display_name

try:
    from host_telegram_notifier import load_env
except ModuleNotFoundError:
    from scripts.host_telegram_notifier import load_env


CAPTION = "TOTISH Telegram match card prototype"


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
