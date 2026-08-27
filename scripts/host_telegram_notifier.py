#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
OUTBOX_DIR = ROOT / "runtime" / "telegram-outbox"


def load_env():
    values = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key.strip()] = value
    return values


def send_message(message):
    env = load_env()
    token = env.get("TELEGRAM_ERROR_BOT_TOKEN") or os.getenv("TELEGRAM_ERROR_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_ERROR_CHAT_ID") or os.getenv("TELEGRAM_ERROR_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram error monitor credentials are not configured")

    body = urlencode({
        "chat_id": chat_id,
        "text": message[:3900],
    }).encode("utf-8")

    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )

    with urlopen(request, timeout=8) as response:
        response.read()


def drain_outbox():
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    sent = 0
    for path in sorted(OUTBOX_DIR.glob("*.msg")):
        try:
            message = path.read_text(encoding="utf-8")
            send_message(message)
            path.unlink()
            sent += 1
        except Exception as exc:
            print(f"OUTBOX ERROR {path.name}: {exc}")
            break

    return sent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message")
    args = parser.parse_args()

    if args.message is not None:
        send_message(args.message)
        print("SEND: OK")
        return

    count = drain_outbox()
    print(f"OUTBOX SENT: {count}")


if __name__ == "__main__":
    main()
