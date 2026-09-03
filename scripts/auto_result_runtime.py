#!/usr/bin/env python3
"""Runtime entry point for dry-run and guarded live automatic results."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import auto_result_worker as dry  # noqa: E402
from auto_result_sources import SourceError  # noqa: E402


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


def run_live(now: datetime, *, state_path: Path, outbox: Path | None) -> dict:
    from app.services.auto_result_finalization_service import finalize_auto_result

    matches = dry._load_matches(now)
    cache = dry.PageCache()
    state = dry._load_state(state_path)
    blocked = state.setdefault("blocked_matches", {})

    candidates = []
    needed: set[str] = set()
    records = []
    for match in matches:
        phase = dry.window_state(match["kickoff_time"], now)
        if phase in {"too_early", "expired"}:
            continue
        base = {
            "match_id": match["id"],
            "scope": match["scope"],
            "home": match["home_team"],
            "away": match["away_team"],
            "date": match["match_date"],
            "phase": phase,
        }
        if str(match["id"]) in blocked:
            base.update({"decision": "blocked", "reason": blocked[str(match["id"])]})
            records.append(base)
            continue
        if phase == "expired_grace":
            base["decision"] = "window_expired"
            dry._event_once(
                state,
                f"match:{match['id']}:window_expired",
                f"⚠️ ТОТИШ: автоматическая проверка завершена без подтверждённого "
                f"результата — {match['home_team']} — {match['away_team']}. "
                "Нужен ручной результат.",
                outbox,
            )
            records.append(base)
            continue
        candidates.append(match)
        needed.update(_source_names(match))

    urls = _source_urls()
    cache.load_many({name: urls[name] for name in needed})
    dry._update_source_health(state, cache.source_status(), outbox)

    observations = []
    if candidates:
        workers = min(6, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_observe, cache, match) for match in candidates]
            for future in as_completed(futures):
                observations.append(future.result())

    for match, first, second, decision in sorted(observations, key=lambda item: item[0]["id"]):
        base = {
            "match_id": match["id"],
            "scope": match["scope"],
            "home": match["home_team"],
            "away": match["away_team"],
            "date": match["match_date"],
            "phase": "active",
        }
        if first is not None and second is not None:
            base.update({"sources": [asdict(first), asdict(second)]})
        base.update(decision)

        if decision["decision"] == "would_write":
            score = decision["score"]
            try:
                outcome = finalize_auto_result(
                    match["id"],
                    score[0],
                    score[1],
                    tournament_id=match["tournament_id"],
                    league=match["league"],
                )
                base["write_outcome"] = outcome
                if outcome == "saved":
                    dry._event_once(
                        state,
                        f"match:{match['id']}:live_success",
                        f"✅ ТОТИШ: {match['home_team']} — {match['away_team']} "
                        f"{score[0]}:{score[1]}. Результат добавлен автоматически, "
                        "очки рассчитаны.",
                        outbox,
                    )
            except Exception as exc:
                blocked[str(match["id"])] = "save_failed"
                base.update({"decision": "save_failed", "error_type": type(exc).__name__})
                dry._event_once(
                    state,
                    f"match:{match['id']}:save_failed",
                    f"🚨 ТОТИШ: {match['home_team']} — {match['away_team']} "
                    f"{score[0]}:{score[1]} подтверждён двумя источниками, но "
                    "сохранить результат не удалось. Автоматические попытки по "
                    "этому матчу остановлены; внесите результат вручную.",
                    outbox,
                )
        elif decision["decision"] == "score_conflict":
            dry._event_once(
                state,
                f"match:{match['id']}:conflict:{decision['first_score']}:{decision['second_score']}",
                f"⚠️ ТОТИШ: источники расходятся по матчу {match['home_team']} — "
                f"{match['away_team']}: {decision['first_score'][0]}:{decision['first_score'][1]} "
                f"и {decision['second_score'][0]}:{decision['second_score'][1]}. "
                "Результат не записан.",
                outbox,
            )
        elif decision["decision"] == "one_source_confirmed":
            score = decision["score"]
            dry._event_once(
                state,
                f"match:{match['id']}:one:{decision['confirmed_source']}:{score}",
                f"ℹ️ ТОТИШ: {decision['confirmed_source']} уже подтвердил "
                f"{match['home_team']} — {match['away_team']} {score[0]}:{score[1]}; "
                "ждём второй источник.",
                outbox,
            )
        records.append(base)

    # Detail fetches may discover a source failure after the base pages loaded.
    dry._update_source_health(state, cache.source_status(), outbox)
    dry._save_state(state_path, state)
    return {
        "mode": "live",
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
    enabled = dry._bool_env("AUTO_RESULTS_ENABLED", False)
    state = dry._load_state(state_path)
    dry._update_enabled_state(state, enabled, outbox)
    dry._save_state(state_path, state)

    if not enabled:
        print(json.dumps({"enabled": False, "reason": "AUTO_RESULTS_ENABLED=false"}, ensure_ascii=False))
        return 0

    now = datetime.now(timezone.utc)
    if dry._bool_env("AUTO_RESULTS_DRY_RUN", True):
        report = dry.run(now, state_path=state_path, outbox=outbox)
    else:
        report = run_live(now, state_path=state_path, outbox=outbox)
    print(json.dumps(report, ensure_ascii=False, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
