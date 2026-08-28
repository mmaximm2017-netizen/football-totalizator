"""Read-only data collection and rendering for the daily admin digest."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from app.db import close_db, get_db
from app.models.scoring import calculate_points, has_valid_finished_score
from app.utils import RU_MONTHS_GENITIVE

MSK = ZoneInfo("Europe/Moscow")
OUTBOX_STALE_SECONDS = 15 * 60
WORKER_STALE_SECONDS = 15 * 60
PUBLIC_HEALTH_URL = "https://totish.ru/health"
FRIENDLY_TOURNAMENT_NAMES = {
    "Чемпионат России 🇷🇺": "РПЛ",
    "Кубок России": "Кубок России",
}


def _as_msk(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK)


def _today_msk(now=None):
    now = now or datetime.now(MSK)
    return _as_msk(now).date()


def _outbox_dir():
    return Path(os.getenv("TELEGRAM_ERROR_OUTBOX_DIR", "/app/runtime/telegram-outbox"))


def _fetch_json(url, timeout=5):
    with urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def fetch_schedule_matches(cur, today):
    tomorrow = today + timedelta(days=1)
    cur.execute(
        """
        SELECT m.id, t.name, m.home_team, m.away_team, m.kickoff_time, m.status
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE (m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date IN (%s, %s)
        ORDER BY t.name ASC NULLS LAST, m.kickoff_time ASC NULLS LAST, m.id ASC
        """,
        (today.isoformat(), tomorrow.isoformat()),
    )
    today_matches = []
    tomorrow_matches = []
    for match_id, tournament_name, home_team, away_team, kickoff_time, status in cur.fetchall():
        if kickoff_time is None:
            continue
        item = {
            "match_id": match_id,
            "tournament_name": tournament_name or "Турнир не указан",
            "home_team": home_team or "Команда не указана",
            "away_team": away_team or "Команда не указана",
            "kickoff_time": kickoff_time,
            "status": status,
        }
        if _as_msk(kickoff_time).date() == today:
            today_matches.append(item)
        else:
            tomorrow_matches.append(item)
    return today_matches, tomorrow_matches


def find_finished_matches_without_result(cur):
    cur.execute(
        """
        SELECT id, home_team, away_team, status, home_score, away_score
        FROM matches
        WHERE status = 'FINISHED'
        ORDER BY kickoff_time ASC NULLS LAST, id ASC
        """
    )
    invalid = []
    for match_id, home_team, away_team, status, home_score, away_score in cur.fetchall():
        if not has_valid_finished_score(status, home_score, away_score):
            invalid.append(
                {
                    "match_id": match_id,
                    "home_team": home_team or "Команда не указана",
                    "away_team": away_team or "Команда не указана",
                }
            )
    return invalid


def count_points_mismatches(cur):
    cur.execute(
        """
        SELECT m.id, m.status, m.home_score, m.away_score,
               p.home_goals, p.away_goals, p.points
        FROM matches m
        JOIN predictions p
          ON p.match_id = m.id
         AND p.tournament_id = m.tournament_id
        WHERE m.status = 'FINISHED'
        """
    )
    mismatches = 0
    for _, status, home_score, away_score, predicted_home, predicted_away, stored_points in cur.fetchall():
        if not has_valid_finished_score(status, home_score, away_score):
            continue
        expected_points = calculate_points(home_score, away_score, predicted_home, predicted_away)
        if stored_points != expected_points:
            mismatches += 1
    return mismatches


def _summary_has_errors(summary):
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return False
    if not isinstance(summary, dict):
        return False
    return bool(summary.get("errors") or (summary.get("sync") or {}).get("errors"))


def latest_sync_has_problem(cur):
    cur.execute(
        """
        SELECT status, errors_count, summary_json
        FROM sync_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return False
    status, errors_count, summary_json = row
    return status != "success" or int(errors_count or 0) > 0 or _summary_has_errors(summary_json)


def collect_outbox_issues(now=None, outbox_dir=None):
    now = now or datetime.now(timezone.utc)
    outbox_dir = Path(outbox_dir) if outbox_dir is not None else _outbox_dir()
    try:
        stale = [
            path
            for path in outbox_dir.glob("*.msg")
            if now.timestamp() - path.stat().st_mtime >= OUTBOX_STALE_SECONDS
        ]
    except OSError:
        return ["🟠 Не удалось проверить Telegram outbox."]
    if not stale:
        return []
    return [
        f"🟠 Telegram outbox: {len(stale)} сообщений ждут отправки более 15 минут."
    ]


def _parse_epoch(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def collect_worker_issues(now_epoch, deadline_mtime, result_mtime):
    now = _parse_epoch(now_epoch)
    if now is None:
        now = int(datetime.now(timezone.utc).timestamp())

    return [item["issue"] for item in worker_heartbeat_statuses(now, deadline_mtime, result_mtime) if item["issue"]]


def worker_heartbeat_statuses(now_epoch, deadline_mtime, result_mtime):
    now = _parse_epoch(now_epoch)
    if now is None:
        now = int(datetime.now(timezone.utc).timestamp())
    workers = (
        ("deadline", deadline_mtime, "Worker дедлайнов", "Нет данных о запуске worker дедлайнов."),
        ("result", result_mtime, "Worker обработки результатов", "Нет данных о запуске worker обработки результатов."),
    )
    statuses = []
    for key, mtime, label, unavailable in workers:
        heartbeat = _parse_epoch(mtime)
        if heartbeat is None:
            statuses.append({"key": key, "label": label, "state": "unavailable", "minutes": None, "issue": f"🔴 {unavailable}"})
            continue
        minutes = max(0, now - heartbeat) // 60
        if minutes >= WORKER_STALE_SECONDS // 60:
            statuses.append({"key": key, "label": label, "state": "stale", "minutes": minutes, "issue": f"🟠 {label} не запускался {minutes} минут."})
        else:
            statuses.append({"key": key, "label": label, "state": "ok", "minutes": minutes, "issue": None})
    return statuses


def collect_health_issues(container_state=None, local_health=None, db_health=None, fetch_json=_fetch_json):
    issues = []
    if container_state and container_state != "running|true|false":
        issues.append("🔴 Контейнер приложения не работает.")

    if not isinstance(local_health, dict):
        issues.append("🔴 Внутренний health-check приложения недоступен.")
    elif local_health.get("status") != "ok":
        issues.append("🔴 Внутренний health-check приложения не в норме.")

    if not isinstance(db_health, dict):
        issues.append("🔴 Проверка базы данных недоступна.")
    else:
        for field, message in (
            ("db", "🔴 Проверка базы данных не в норме."),
            ("active_tournament", "🟠 Активный турнир определяется некорректно."),
            ("ranking", "🟠 Турнирная таблица не рассчитывается."),
        ):
            if db_health.get(field) != "ok":
                issues.append(message)

    try:
        health = fetch_json(PUBLIC_HEALTH_URL)
        if health.get("status") != "ok":
            issues.append("🔴 Сайт недоступен пользователям из интернета.")
    except Exception:  # noqa: BLE001 - public health failures are rendered as unavailable.
        issues.append("🔴 Публичный health-check сайта недоступен.")
    return issues


def collect_digest(
    now=None,
    container_state=None,
    local_health=None,
    db_health=None,
    host_now_epoch=None,
    deadline_worker_mtime=None,
    result_worker_mtime=None,
    fetch_json=_fetch_json,
    outbox_dir=None,
):
    today = _today_msk(now)
    issues = collect_health_issues(container_state, local_health, db_health, fetch_json)
    issues.extend(
        collect_worker_issues(
            host_now_epoch,
            deadline_worker_mtime,
            result_worker_mtime,
        )
    )
    conn = cur = None
    today_matches = []
    tomorrow_matches = []
    try:
        conn = get_db()
        cur = conn.cursor()
        today_matches, tomorrow_matches = fetch_schedule_matches(cur, today)
        for match in find_finished_matches_without_result(cur):
            issues.append(
                f"🔴 {match['home_team']} — {match['away_team']} завершён, но результат не внесён."
            )
        mismatches = count_points_mismatches(cur)
        if mismatches:
            issues.append(f"🟠 Обнаружено расхождение в начисленных очках: {mismatches} прогнозов.")
        if latest_sync_has_problem(cur):
            issues.append("🟠 Последняя синхронизация матчей завершилась с ошибками.")
    except Exception:  # noqa: BLE001 - read-only digest must render database unavailability.
        issues.append("🔴 Не удалось получить данные ТОТИШ из базы данных.")
    finally:
        if conn is not None and cur is not None:
            close_db(conn, cur)

    issues.extend(collect_outbox_issues(now=now, outbox_dir=outbox_dir))
    return {
        "date": today,
        "issues": issues,
        "today_matches": today_matches,
        "tomorrow_matches": tomorrow_matches,
    }


def _friendly_tournament_name(name):
    return FRIENDLY_TOURNAMENT_NAMES.get(name, name)


def _render_matches(matches):
    grouped = {}
    for match in matches:
        grouped.setdefault(_friendly_tournament_name(match["tournament_name"]), []).append(match)

    lines = []
    for tournament_name in sorted(grouped):
        lines.append(tournament_name)
        for match in sorted(grouped[tournament_name], key=lambda item: item["kickoff_time"]):
            kickoff = _as_msk(match["kickoff_time"]).strftime("%H:%M")
            lines.append(f"{kickoff} — {match['home_team']} — {match['away_team']}")
    return lines


def render_digest(data):
    digest_date = data["date"]
    lines = [
        "☀️ ТОТИШ — утренний отчёт",
        f"{digest_date.day} {RU_MONTHS_GENITIVE[digest_date.month]}",
        "",
    ]
    if data["issues"]:
        lines.extend(["🚨 Требует внимания", "", *data["issues"]])
    else:
        lines.append("✅ Всё работает штатно.")

    lines.extend(["", "⚽ Сегодня", ""])
    if data["today_matches"]:
        lines.extend(_render_matches(data["today_matches"]))
    else:
        lines.append("Сегодня матчей нет.")
        if data["tomorrow_matches"]:
            lines.extend(["", "⚽ Завтра", "", *_render_matches(data["tomorrow_matches"])])
    return "\n".join(lines)
