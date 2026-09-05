#!/usr/bin/env python3
"""Runtime entry point for dry-run and guarded live automatic results."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import auto_result_worker as dry
from auto_result_sources import SourceError

from app.services import auto_result_delivery_service as delivery

logger = logging.getLogger(__name__)


def _source_names(match: dict) -> set[str]:
    if match["scope"] == "rpl":
        return {"livesport_rpl", "sports_rpl"}
    if match["scope"] == "cup":
        return {"livesport_cup", "rfs_cup"}
    if match["scope"] == "national":
        return {"rfs_national", "sportbox_national"}
    return set()


def _source_urls() -> dict[str, str]:
    return {
        "livesport_rpl": dry.LIVE_RPL,
        "sports_rpl": dry.SPORTS_RPL,
        "livesport_cup": dry.LIVE_CUP,
        "rfs_cup": dry.RFS_CUP,
        "rfs_national": dry.RFS_NATIONAL,
        "sportbox_national": dry.SPORTBOX_NATIONAL,
    }


def _observe(cache: dry.PageCache, match: dict) -> tuple[dict, object, object, dict] | tuple[dict, None, None, dict]:
    try:
        first, second = dry.observe_match(cache, match)
        return match, first, second, dry.decide(first, second)
    except SourceError as exc:
        return match, None, None, {"decision": "source_error", "error": str(exc)}


def _reason(first, second, decision, cache, match=None):
    if first is None or second is None:
        failed = [f"{name}: технически недоступен ({value['error']})"
                  for name, value in cache.source_status().items()
                  if not value["ok"] and (match is None or name in _source_names(match))]
        return "; ".join(failed) or "Техническая ошибка проверки; подтверждённых данных нет."
    parts = []
    for observation in (first, second):
        if observation.status == "finished":
            detail = f"подтвердил {observation.home_score}:{observation.away_score}"
        elif observation.status == "not_found":
            detail = "не нашёл матч"
        else:
            detail = "не подтвердил завершение матча"
        parts.append(f"{observation.source}: {detail}")
    if decision["decision"] == "score_conflict":
        parts.append("Источники дали разные счета")
    return "; ".join(parts)


def _expired(match):
    previous = delivery.last_check(match)
    reason = (f"Последняя сохранённая проверка {previous[0].isoformat()}: {previous[1]}"
              if previous else "Нет сохранённых проверок этой версии матча; причина неизвестна.")
    delivery.notify_pending(
        match, f"expired:{delivery.match_identity(match)}",
        f"⚠️ ТОТИШ: {match['home_team']} — {match['away_team']}. "
        f"Окно автоматизации закрыто. На момент проверки результата нет. "
        f"Если он ещё не внесён, нужен ручной результат. {reason}",
    )


def monitor(now):
    enabled = dry._bool_env("AUTO_RESULTS_ENABLED", False)
    live_enabled = enabled and not dry._bool_env("AUTO_RESULTS_DRY_RUN", True)
    since = delivery.enabled_since(live_enabled)
    if not live_enabled:
        return {"monitor": "disabled_or_dry_run"}
    # Catch missed final-notice runs on recovery. Ignore pre-rollout backlog.
    matches = dry._load_matches(now, lookback_minutes=7 * 24 * 60)
    for match in matches:
        end = match["kickoff_time"] + timedelta(minutes=180)
        if end < since:
            continue
        phase = dry.window_state(match["kickoff_time"], now)
        if phase in {"expired", "expired_grace"}:
            _expired(match)
        elif (phase == "active"
              and now >= match["kickoff_time"] + timedelta(minutes=132)):
            previous = delivery.last_check(match)
            if previous is None or now - previous[0] > timedelta(minutes=12):
                delivery.notify_pending(
                    match, f"stalled:{delivery.match_identity(match)}",
                    f"🚨 ТОТИШ: {match['home_team']} — {match['away_team']}. "
                    "Нет свежей сохранённой проверки автозаписи более 12 минут "
                    "в рабочем окне. Проверьте cron/worker; результат ещё не подтверждён.",
                )
    return {"monitor": "checked", "pending_matches": len(matches)}


def run_live(now: datetime, *, state_path: Path, outbox: Path | None) -> dict:
    # DB lock covers callers bypassing the shell flock, including other hosts.
    with delivery.worker_lock() as acquired:
        if not acquired:
            return {"mode": "live", "skipped": "overlapping_run"}
        return _run_live(now, state_path=state_path, outbox=outbox)


def _run_live(now: datetime, *, state_path: Path, outbox: Path | None) -> dict:
    from app.services.auto_result_finalization_service import finalize_auto_result

    matches = dry._load_matches(now)
    cache = dry.PageCache()
    state = dry._load_state(state_path)
    # Old JSON blocks are not authoritative. Every retry uses fresh consensus
    # and the row-locked finalizer; attempts end strictly at +180 minutes.
    state.pop("blocked_matches", None)
    candidates = []
    needed = set()
    records = []
    for match in matches:
        phase = dry.window_state(match["kickoff_time"], now)
        if phase == "too_early":
            continue
        if phase != "active":
            _expired(match)
            continue
        candidates.append(match)
        needed.update(_source_names(match))
    urls = _source_urls()
    cache.load_many({name: urls[name] for name in needed})
    if candidates:
        with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as pool:
            futures = [pool.submit(_observe, cache, match) for match in candidates]
            # A slow cup/national detail must not hold back a ready RPL match.
            for future in as_completed(futures):
                match, first, second, decision = future.result()
                base = {"match_id": match["id"], **decision}
                if first is not None and second is not None:
                    base["sources"] = [asdict(first), asdict(second)]
                detail = _reason(first, second, decision, cache, match)
                delivery.record_check(match, datetime.now(timezone.utc), detail)
                if dry.window_state(match["kickoff_time"], datetime.now(timezone.utc)) != "active":
                    base["decision"] = "window_expired"
                    _expired(match)
                elif decision["decision"] == "would_write":
                    try:
                        outcome = finalize_auto_result(
                            match["id"], *decision["score"],
                            tournament_id=match["tournament_id"], league=match["league"],
                            expected_home_team=match["home_team"],
                            expected_away_team=match["away_team"],
                            expected_kickoff_time=match["kickoff_time"],
                            expected_match_category=match["match_category"],
                        )
                        base["write_outcome"] = outcome
                    except Exception as exc:
                        logger.exception("Auto-result save not acknowledged")
                        # Commit acknowledgement can be lost. Never assert that
                        # the DB did not save: next SELECT/row lock resolves it.
                        base.update(decision="retry", error_type=type(exc).__name__)
                        detail += f"; сохранение не подтверждено ({type(exc).__name__})"
                        delivery.record_check(match, datetime.now(timezone.utc), detail)
                        delivery.notify(
                            f"save-error:{delivery.match_identity(match)}",
                            f"⚠️ ТОТИШ: {match['home_team']} — {match['away_team']}. "
                            "Подтверждение сохранения не получено. Следующий запуск "
                            "перепроверит БД и источники; повторы разрешены только до +180 минут.",
                        )
                    else:
                        if outcome == "window_expired":
                            _expired(match)
                elif decision["decision"] in {"score_conflict", "one_source_confirmed"}:
                    delivery.notify(
                        f"observation:{delivery.match_identity(match)}:{detail}",
                        f"ℹ️ ТОТИШ: {match['home_team']} — {match['away_team']}. {detail}. "
                        "Результат не записан; ждём согласованного подтверждения в рабочем окне.",
                    )
                records.append(base)
    # Only fetch+parser outcomes may transition source health. Notification or
    # JSON failure is never a failed match save and cannot block future writes.
    try:
        dry._update_source_health(state, cache.source_status(), outbox)
        dry._save_state(state_path, state)
    except Exception:
        logger.exception("Auto-result diagnostic file publication failed")
    return {"mode": "live", "now": now.isoformat(), "matches": records,
            "sources": cache.source_status()}


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
    parser.add_argument("--monitor", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    outbox = None if args.no_notify else Path(args.outbox)
    enabled = dry._bool_env("AUTO_RESULTS_ENABLED", False)
    delivery.enabled_since(enabled and not dry._bool_env("AUTO_RESULTS_DRY_RUN", True))
    now = datetime.now(timezone.utc)
    if args.monitor:
        report = monitor(now)
    elif not enabled:
        report = {"enabled": False}
    elif dry._bool_env("AUTO_RESULTS_DRY_RUN", True):
        report = dry.run(now, state_path=state_path, outbox=outbox)
    else:
        report = run_live(now, state_path=state_path, outbox=outbox)
    try:
        delivery.flush_notifications(outbox)
    except Exception:
        # Durable rows remain pending. This does not change a committed result.
        logger.exception("Auto-result Telegram publication deferred")
        report["notification_delivery"] = "deferred"
    print(json.dumps(report, ensure_ascii=False, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
