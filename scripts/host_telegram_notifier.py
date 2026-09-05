#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
OUTBOX_DIR = ROOT / "runtime" / "telegram-outbox"
STATE_DIR = Path.home() / ".local" / "state" / "totish"
DEDUPE_FILE = STATE_DIR / "telegram-relay-dedupe.json"
DEDUPE_SECONDS = 300


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


def _load_dedupe_state():
    try:
        data = json.loads(DEDUPE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_dedupe_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEDUPE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(DEDUPE_FILE)


def _message_fingerprint(message):
    return hashlib.sha256(
        message.encode("utf-8", errors="replace")
    ).hexdigest()


def should_send_message(message):
    now = int(time.time())
    state = _load_dedupe_state()
    previous = int(state.get(_message_fingerprint(message), 0) or 0)
    return not previous or now - previous >= DEDUPE_SECONDS


def mark_message_sent(message):
    now = int(time.time())
    state = _load_dedupe_state()
    state[_message_fingerprint(message)] = now

    cutoff = now - 3600
    state = {k: v for k, v in state.items() if int(v or 0) >= cutoff}
    _save_dedupe_state(state)


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
    # Independent relay invocations (cron, worker, monitor) share one lock.
    # Never unlink it: waiters must continue to refer to the same inode.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "telegram-relay.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            return _drain_outbox_locked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _drain_outbox_locked():
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    sent = 0
    for path in sorted(OUTBOX_DIR.glob("*.msg")):
        try:
            message = path.read_text(encoding="utf-8")
            if should_send_message(message):
                send_message(message)
                mark_message_sent(message)
                sent += 1
            else:
                print(f"OUTBOX DEDUPED: {path.name}")
            path.unlink()
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
