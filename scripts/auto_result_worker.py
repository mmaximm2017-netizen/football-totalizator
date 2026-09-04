#!/usr/bin/env python3
"""TOTISH automatic-result worker — safe dry-run phase.

This implementation is intentionally incapable of updating match results,
statuses, points, or any other production data. The DB session is read-only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from auto_result_sources import (  # noqa: E402
    Observation,
    SourceError,
    STATUS_FINISHED,
    STATUS_NOT_FINISHED,
    absolute_url,
    fetch_text,
    find_livesport_result,
    find_rfs_candidate,
    find_sportbox_candidate,
    find_sports_rpl_result,
    parse_rfs_detail,
    parse_sportbox_game_json,
    rfs_cup_candidates,
    rfs_national_candidates,
    sportbox_national_candidates,
    team_matches,
)

LIVE_RPL = "https://www.livesport.ru/football/rfpl/calendar/"
SPORTS_RPL = "https://www.sports.ru/football/tournament/rfpl/calendar/"
LIVE_CUP = "https://www.livesport.ru/football/rucup/calendar/"
RFS_CUP = "https://www.rfs.ru/cup/tournament/matches?TournamentMatchesFilter%5Bdate%5D=all"
RFS_BASE = "https://www.rfs.ru"
RFS_NATIONAL = "https://www.rfs.ru/natteamfriendlies/calendar"
SPORTBOX_NATIONAL = "https://news.sportbox.ru/Vidy_sporta/Futbol/russian_team"
SPORTBOX_JSON = "https://news.sportbox.ru/stats/game_json/{game_id}"

FIRST_CHECK_MINUTES = 120
WINDOW_END_MINUTES = 180
FINAL_GRACE_MINUTES = 185
FINAL_NOTICE_LOOKBACK_MINUTES = 195
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _bool_env(name: str, default=False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def classify_scope(match: dict) -> str | None:
    if match["tournament_id"] == 6 and match["league"] == "rcup":
        return "cup"
    if match["tournament_id"] == 5 and match["league"] == "rpl":
        if match["match_category"] == "national_team":
            return "national"
        if match["match_category"] in {"", "rpl", None}:
            return "rpl"
    return None


def minutes_since_kickoff(kickoff: datetime, now: datetime) -> float:
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return (now - kickoff.astimezone(timezone.utc)).total_seconds() / 60.0


def window_state(kickoff: datetime, now: datetime) -> str:
    elapsed = minutes_since_kickoff(kickoff, now)
    if elapsed < FIRST_CHECK_MINUTES:
        return "too_early"
    if elapsed <= WINDOW_END_MINUTES:
        return "active"
    if elapsed <= FINAL_GRACE_MINUTES:
        return "expired_grace"
    return "expired"


def decide(first: Observation, second: Observation) -> dict:
    if first.status == STATUS_FINISHED and second.status == STATUS_FINISHED:
        first_score = (first.home_score, first.away_score)
        second_score = (second.home_score, second.away_score)
        if first_score == second_score:
            return {"decision": "would_write", "score": first_score}
        return {
            "decision": "score_conflict",
            "first_score": first_score,
            "second_score": second_score,
        }
    if first.status == STATUS_FINISHED and second.status != STATUS_FINISHED:
        return {
            "decision": "one_source_confirmed",
            "confirmed_source": first.source,
            "score": (first.home_score, first.away_score),
        }
    if second.status == STATUS_FINISHED and first.status != STATUS_FINISHED:
        return {
            "decision": "one_source_confirmed",
            "confirmed_source": second.source,
            "score": (second.home_score, second.away_score),
        }
    return {"decision": "waiting"}


class PageCache:
    def __init__(self):
        self._pages: dict[str, str] = {}
        self._errors: dict[str, str] = {}

    def load_many(self, urls: dict[str, str]) -> None:
        def load(item):
            name, url = item
            try:
                return name, fetch_text(url), None
            except SourceError as exc:
                return name, None, str(exc)

        with ThreadPoolExecutor(max_workers=min(6, max(1, len(urls)))) as pool:
            for name, text, error in pool.map(load, urls.items()):
                if error:
                    self.mark_error(name, error)
                else:
                    self._pages[name] = text
                    self._errors.pop(name, None)

    def mark_error(self, name: str, error: str) -> None:
        self._errors[name] = error

    def page(self, name: str) -> str:
        if name in self._errors:
            raise SourceError(self._errors[name])
        if name not in self._pages:
            raise SourceError("source_not_loaded")
        return self._pages[name]

    def source_status(self) -> dict[str, dict]:
        names = set(self._pages) | set(self._errors)
        return {
            name: (
                {"ok": False, "error": self._errors[name]}
                if name in self._errors
                else {"ok": True}
            )
            for name in sorted(names)
        }


def _fetch_detail(cache: PageCache, source_name: str, url: str) -> str:
    try:
        return fetch_text(url)
    except SourceError as exc:
        cache.mark_error(source_name, str(exc))
        raise


def _guard_observation(cache: PageCache, source_name: str, callback):
    try:
        return callback()
    except SourceError as exc:
        cache.mark_error(source_name, str(exc))
        raise


def _rfs_cup_observation(cache: PageCache, match: dict) -> Observation:
    candidates = rfs_cup_candidates(cache.page("rfs_cup"))
    team_candidates = [
        item
        for item in candidates
        if team_matches(item["home"], match["home_team"])
        and team_matches(item["away"], match["away_team"])
    ]
    if not team_candidates:
        return Observation("rfs", "not_found", detail="match_not_found")

    saw_target_unfinished = False
    for candidate in team_candidates:
        detail = _fetch_detail(
            cache,
            "rfs_cup",
            absolute_url(RFS_BASE, candidate["href"]),
        )
        observation = parse_rfs_detail(detail, score=candidate["score"])
        if observation.match_date != match["match_date"]:
            continue
        if observation.status == STATUS_FINISHED:
            return observation
        saw_target_unfinished = True

    return Observation(
        "rfs",
        STATUS_NOT_FINISHED if saw_target_unfinished else "not_found",
        match_date=match["match_date"] if saw_target_unfinished else None,
        detail="target_date_not_finished_or_not_found",
    )


def _rfs_national_observation(cache: PageCache, match: dict) -> Observation:
    candidates = rfs_national_candidates(cache.page("rfs_national"))
    candidate = find_rfs_candidate(
        candidates,
        home=match["home_team"],
        away=match["away_team"],
        match_date=match["match_date"],
    )
    if not candidate:
        return Observation("rfs", "not_found", detail="match_not_found")

    detail = _fetch_detail(
        cache,
        "rfs_national",
        absolute_url(RFS_BASE, candidate["href"]),
    )
    observation = parse_rfs_detail(detail, score=candidate["score"])
    if observation.match_date != match["match_date"]:
        return Observation("rfs", "not_found", detail="date_mismatch")
    return observation


def _sportbox_national_observation(cache: PageCache, match: dict) -> Observation:
    candidates = sportbox_national_candidates(cache.page("sportbox_national"))
    candidate = find_sportbox_candidate(
        candidates,
        home=match["home_team"],
        away=match["away_team"],
        match_date=match["match_date"],
    )
    if not candidate:
        return Observation("sportbox", "not_found", detail="match_not_found")
    raw = _fetch_detail(
        cache,
        "sportbox_national",
        SPORTBOX_JSON.format(game_id=candidate["game_id"]),
    )
    return parse_sportbox_game_json(raw, match_date=match["match_date"])


def observe_match(cache: PageCache, match: dict) -> tuple[Observation, Observation]:
    scope = match["scope"]
    if scope == "rpl":
        return (
            _guard_observation(
                cache,
                "livesport_rpl",
                lambda: find_livesport_result(
                    cache.page("livesport_rpl"),
                    home=match["home_team"],
                    away=match["away_team"],
                    match_date=match["match_date"],
                ),
            ),
            _guard_observation(
                cache,
                "sports_rpl",
                lambda: find_sports_rpl_result(
                    cache.page("sports_rpl"),
                    home=match["home_team"],
                    away=match["away_team"],
                    match_date=match["match_date"],
                ),
            ),
        )
    if scope == "cup":
        return (
            _guard_observation(
                cache,
                "livesport_cup",
                lambda: find_livesport_result(
                    cache.page("livesport_cup"),
                    home=match["home_team"],
                    away=match["away_team"],
                    match_date=match["match_date"],
                ),
            ),
            _guard_observation(
                cache, "rfs_cup", lambda: _rfs_cup_observation(cache, match)
            ),
        )
    if scope == "national":
        return (
            _guard_observation(
                cache,
                "sportbox_national",
                lambda: _sportbox_national_observation(cache, match),
            ),
            _guard_observation(
                cache,
                "rfs_national",
                lambda: _rfs_national_observation(cache, match),
            ),
        )
    raise SourceError("unsupported_scope")


def _load_matches(now: datetime) -> list[dict]:
    # Delayed import keeps unit tests independent of Flask production config.
    from app.db import close_db, get_db

    conn = get_db()
    cur = None
    try:
        conn.rollback()
        conn.set_session(readonly=True, autocommit=False)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tournament_id, league, COALESCE(match_category, ''),
                   home_team, away_team, kickoff_time, status
            FROM matches
            WHERE status IN ('SCHEDULED', 'TIMED', 'LIVE')
              AND home_score IS NULL AND away_score IS NULL
              AND kickoff_time IS NOT NULL
              AND kickoff_time <= %s
              AND kickoff_time >= %s
              AND (
                    (tournament_id = 5 AND league = 'rpl'
                     AND COALESCE(match_category, 'rpl') IN ('rpl', 'national_team'))
                    OR (tournament_id = 6 AND league = 'rcup')
                  )
            ORDER BY kickoff_time, id
            """,
            (now, now.replace(microsecond=0) - timedelta(minutes=FINAL_NOTICE_LOOKBACK_MINUTES)),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            match = {
                "id": row[0],
                "tournament_id": row[1],
                "league": row[2],
                "match_category": row[3],
                "home_team": row[4],
                "away_team": row[5],
                "kickoff_time": row[6],
                "status": row[7],
            }
            scope = classify_scope(match)
            if scope:
                match["scope"] = scope
                match["match_date"] = row[6].astimezone(MOSCOW_TZ).date().isoformat()
                result.append(match)
        return result
    finally:
        try:
            conn.rollback()
        finally:
            close_db(conn, cur)


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _queue_message(outbox: Path | None, message: str) -> None:
    if outbox is None:
        return
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / f"auto-results-{int(time.time())}-{uuid.uuid4().hex}.msg"
    target.write_text(message, encoding="utf-8")


def _event_once(state: dict, key: str, message: str, outbox: Path | None) -> None:
    sent = state.setdefault("events", {})
    if sent.get(key):
        return
    sent[key] = int(time.time())
    _queue_message(outbox, message)


def _update_enabled_state(
    state: dict,
    enabled: bool,
    outbox: Path | None,
) -> None:
    previous = state.get("automation_enabled")
    if previous is True and not enabled:
        _queue_message(
            outbox,
            "⏸️ ТОТИШ: автоматическая проверка результатов отключена "
            "(AUTO_RESULTS_ENABLED=false).",
        )
    elif previous is False and enabled:
        _queue_message(
            outbox,
            "▶️ ТОТИШ: автоматическая проверка результатов снова включена.",
        )
    state["automation_enabled"] = enabled


def _update_source_health(
    state: dict,
    statuses: dict,
    outbox: Path | None,
) -> None:
    health = state.setdefault("source_health", {})
    labels = {
        "livesport_rpl": "LiveSport РПЛ",
        "sports_rpl": "Sports.ru РПЛ",
        "livesport_cup": "LiveSport Кубок",
        "rfs_cup": "РФС Кубок",
        "rfs_national": "РФС сборная",
        "sportbox_national": "Sportbox сборная",
    }
    for name, status in statuses.items():
        current = bool(status["ok"])
        previous = health.get(name)
        if not current and previous is not False:
            _queue_message(
                outbox,
                f"⚠️ ТОТИШ: источник результатов недоступен — "
                f"{labels.get(name, name)}. Автоматическая запись по затронутым "
                "матчам невозможна.",
            )
        elif current and previous is False:
            _queue_message(
                outbox,
                f"✅ ТОТИШ: источник результатов восстановился — "
                f"{labels.get(name, name)}.",
            )
        health[name] = current


def run(now: datetime, *, state_path: Path, outbox: Path | None) -> dict:
    matches = _load_matches(now)
    cache = PageCache()
    needed = set()
    for match in matches:
        if window_state(match["kickoff_time"], now) == "active":
            if match["scope"] == "rpl":
                needed.update({"livesport_rpl", "sports_rpl"})
            elif match["scope"] == "cup":
                needed.update({"livesport_cup", "rfs_cup"})
            elif match["scope"] == "national":
                needed.update({"rfs_national", "sportbox_national"})

    urls = {
        "livesport_rpl": LIVE_RPL,
        "sports_rpl": SPORTS_RPL,
        "livesport_cup": LIVE_CUP,
        "rfs_cup": RFS_CUP,
        "rfs_national": RFS_NATIONAL,
        "sportbox_national": SPORTBOX_NATIONAL,
    }
    cache.load_many({name: urls[name] for name in needed})

    state = _load_state(state_path)
    _update_source_health(state, cache.source_status(), outbox)
    records = []

    for match in matches:
        phase = window_state(match["kickoff_time"], now)
        if phase == "too_early":
            continue
        base = {
            "match_id": match["id"],
            "scope": match["scope"],
            "home": match["home_team"],
            "away": match["away_team"],
            "date": match["match_date"],
            "phase": phase,
        }
        if phase in {"expired_grace", "expired"}:
            base["decision"] = "window_expired"
            _event_once(
                state,
                f"match:{match['id']}:window_expired",
                f"⚠️ ТОТИШ: автоматическая проверка завершена без "
                f"подтверждённого результата — {match['home_team']} — "
                f"{match['away_team']}. Нужен ручной результат.",
                outbox,
            )
            records.append(base)
            continue

        try:
            first, second = observe_match(cache, match)
            decision = decide(first, second)
            base.update({"sources": [asdict(first), asdict(second)], **decision})
            if decision["decision"] == "would_write":
                score = decision["score"]
                _event_once(
                    state,
                    f"match:{match['id']}:dryrun_success",
                    f"🧪 ТОТИШ dry-run: {match['home_team']} — "
                    f"{match['away_team']} {score[0]}:{score[1]}. Два источника "
                    "совпали; в боевом режиме результат был бы добавлен автоматически.",
                    outbox,
                )
            elif decision["decision"] == "score_conflict":
                _event_once(
                    state,
                    f"match:{match['id']}:conflict:"
                    f"{decision['first_score']}:{decision['second_score']}",
                    f"⚠️ ТОТИШ dry-run: источники расходятся по матчу "
                    f"{match['home_team']} — {match['away_team']}: "
                    f"{decision['first_score'][0]}:{decision['first_score'][1]} и "
                    f"{decision['second_score'][0]}:{decision['second_score'][1]}. "
                    "Ничего не записано.",
                    outbox,
                )
            elif decision["decision"] == "one_source_confirmed":
                score = decision["score"]
                _event_once(
                    state,
                    f"match:{match['id']}:one:{decision['confirmed_source']}:{score}",
                    f"ℹ️ ТОТИШ dry-run: {decision['confirmed_source']} уже подтвердил "
                    f"{match['home_team']} — {match['away_team']} "
                    f"{score[0]}:{score[1]}; ждём второй источник.",
                    outbox,
                )
        except SourceError as exc:
            base.update({"decision": "source_error", "error": str(exc)})
        records.append(base)

    # Detail-page failures can be discovered after the initial base-page check.
    _update_source_health(state, cache.source_status(), outbox)
    _save_state(state_path, state)
    return {
        "mode": "dry_run",
        "now": now.isoformat(),
        "matches": records,
        "sources": cache.source_status(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-file",
        default=os.getenv(
            "AUTO_RESULTS_STATE_FILE",
            "/app/runtime/telegram-outbox/.auto-results-state.json",
        ),
    )
    parser.add_argument(
        "--outbox",
        default=os.getenv("AUTO_RESULTS_OUTBOX", "/app/runtime/telegram-outbox"),
    )
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    outbox = None if args.no_notify else Path(args.outbox)
    enabled = _bool_env("AUTO_RESULTS_ENABLED", False)
    state = _load_state(state_path)
    _update_enabled_state(state, enabled, outbox)
    _save_state(state_path, state)

    if not enabled:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "enabled": False,
                    "reason": "AUTO_RESULTS_ENABLED=false",
                },
                ensure_ascii=False,
            )
        )
        return 0

    # Hard safety gate: this dry-run build has no live write path.
    if not _bool_env("AUTO_RESULTS_DRY_RUN", True):
        print(
            json.dumps(
                {"mode": "refused", "error": "live_write_not_implemented"},
                ensure_ascii=False,
            )
        )
        return 2

    report = run(datetime.now(timezone.utc), state_path=state_path, outbox=outbox)
    print(json.dumps(report, ensure_ascii=False, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
