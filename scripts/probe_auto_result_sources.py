#!/usr/bin/env python3
"""Read-only network probe for candidate automatic-result sources.

No database imports, writes, cron changes, Telegram messages, or match updates.
Exit code is always zero: this diagnostic reports individual source failures in
JSON so a blocked source does not hide the status of the other sources.
"""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.auto_result_source_probe import (  # noqa: E402
    SourceParseError,
    parse_flashscore_feed,
    parse_rfs_cup_listing,
    parse_rfs_national_listing,
)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TOTISH-ReadOnly-Result-Probe/1.0)",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}
TIMEOUT = (5, 15)


def _get(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    return response


def _probe_flashscore():
    url = "https://www.flashscorekz.com/football/russia/premier-league/results/"
    response = _get(url)
    matches = parse_flashscore_feed(response.text)
    return {
        "ok": True,
        "url": url,
        "final_url": response.url,
        "finished_matches_parsed": len(matches),
    }


def _probe_rfs_cup():
    url = "https://www.rfs.ru/cup/tournament/matches?TournamentMatchesFilter%5Bdate%5D=all"
    response = _get(url)
    matches = parse_rfs_cup_listing(response.text)
    completed = sum(
        item["home_score"] is not None and item["away_score"] is not None
        for item in matches
    )
    return {
        "ok": True,
        "url": url,
        "final_url": response.url,
        "match_cards_parsed": len(matches),
        "cards_with_score": completed,
    }


def _probe_rfs_national():
    url = "https://www.rfs.ru/natteamfriendlies/calendar"
    response = _get(url)
    matches = parse_rfs_national_listing(response.text)
    completed = sum(
        item["home_score"] is not None and item["away_score"] is not None
        for item in matches
    )
    return {
        "ok": True,
        "url": url,
        "final_url": response.url,
        "match_rows_parsed": len(matches),
        "rows_with_score": completed,
    }


def _probe_rpl_official():
    url = "https://premierliga.ru/matches/"
    response = _get(url)
    blocked = "showcaptcha" in response.url or "Вы не робот?" in response.text
    return {
        "ok": not blocked,
        "url": url,
        "final_url": response.url,
        "blocked_by_captcha": blocked,
        "detail": "captcha" if blocked else "page reachable; no scraper implemented",
    }


def _safe_probe(name, callback):
    try:
        result = callback()
    except (requests.RequestException, SourceParseError, ValueError) as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return name, result


def main():
    checks = dict(
        _safe_probe(name, callback)
        for name, callback in (
            ("flashscore", _probe_flashscore),
            ("rfs_cup", _probe_rfs_cup),
            ("rfs_national", _probe_rfs_national),
            ("rpl_official", _probe_rpl_official),
        )
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
