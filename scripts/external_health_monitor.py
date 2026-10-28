#!/usr/bin/env python3
"""Run outside the VPS. No PostgreSQL connection and no app dependencies."""
import json
import os
import time
from urllib.request import Request, urlopen


def healthy():
    for attempt in range(3):
        try:
            with urlopen("https://totish.ru/health", timeout=10) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return True
        except (OSError, ValueError):
            pass
        if attempt < 2:
            time.sleep(2)
    return False


def main():
    token = os.getenv("TELEGRAM_ERROR_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_ERROR_CHAT_ID", "")
    if healthy():
        print("EXTERNAL HEALTH OK")
        return 0
    if not token or not chat:
        print("EXTERNAL HEALTH FAILED; Telegram alerting unconfigured")
        return 2
    payload = json.dumps({"chat_id": chat, "text": "🚨 ТОТИШ не отвечает снаружи. Три проверки https://totish.ru/health не прошли. Проверьте VPS, DNS и HTTPS."}).encode()
    try:
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            if not json.load(response).get("ok"):
                raise ValueError("delivery_failed")
    except Exception:
        # urllib exceptions may include the bot token in their URL.
        print("EXTERNAL ALERT DELIVERY FAILED")
        return 2
    print("EXTERNAL HEALTH FAILED; Telegram alert delivered")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
