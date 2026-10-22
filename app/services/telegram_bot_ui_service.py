"""Guided Telegram analytics, using the same read-only views and scoring filters."""
from datetime import date, datetime, timedelta

from app.gpt_db import short_gpt_read
from app.services.telegram_sql_assistant import (
    Clarify, FINISHED, MAX_ROWS, Question, choose_tournament, execute_question, parse_dates, read,
)
from app.utils import MSK
from scripts.telegram_bot_ui import KINDS, METRICS, TITLES, button, callback, local_screen, nav, parse

PAGE_SIZE = 8


def _pages(blocks, limit=2400):
    pages, current = [], []
    for block in blocks:
        # A single long line must remain available as well.
        pieces = [block[i:i + limit] for i in range(0, len(block), limit)] or [""]
        for piece in pieces:
            if current and (len(current) >= PAGE_SIZE or len("\n".join(current)) + len(piece) + 1 > limit):
                pages.append("\n".join(current))
                current = []
            current.append(piece)
    if current or not pages:
        pages.append("\n".join(current))
    return pages


def _period(value, now):
    if value == "all":
        return [], None, "Весь турнир"
    if value.startswith("r"):
        return [], int(value[1:]), f"Тур {value[1:]}"
    if value in {"today", "yesterday"}:
        day = now.astimezone(MSK).date() - timedelta(days=value == "yesterday")
        return [day.isoformat()], None, day.strftime("%d.%m.%Y")
    start, end = (datetime.strptime(part, "%Y%m%d").date() for part in value[1:].split("-"))
    if not 0 <= (end - start).days <= 366:
        raise ValueError("invalid_period")
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    return dates, None, f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


def _pagination(stage, kind, metric, period, user, page, count):
    buttons = []
    if page:
        buttons.append(button("‹ Предыдущие", callback(stage, kind, metric, period, user, page - 1)))
    if page + 1 < count:
        buttons.append(button("Следующие ›", callback(stage, kind, metric, period, user, page + 1)))
    return [buttons] if buttons else []


def _ranking(cur, tid):
    return read(cur, """
        SELECT user_id, username, points, forecasts, exact, diff, outcome
        FROM gpt_safe.tournament_rankings WHERE tournament_id = %s
        ORDER BY points DESC, exact DESC, diff DESC, outcome DESC, username
    """, (tid,))


def render(data, text=None, now=None, with_players=False, paginate=True):
    now = now or datetime.now(MSK)
    stage, kind, metric, period, user, page = parse(data)
    local = local_screen(data)
    if local and "next" in local:
        return render(local["next"], now=now, with_players=with_players, paginate=paginate)
    if text is not None:
        if stage != "d" or not isinstance(text, str) or len(text) > 1000:
            raise ValueError("invalid_ui_input")
        try:
            dates = parse_dates(text, now)
            if not dates:
                raise Clarify("Укажите дату или обе границы периода.")
            # Lists with gaps must not silently become a continuous interval.
            if (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 1 != len(dates):
                raise Clarify("Выберите один день или непрерывный период с … по … .")
        except Clarify as exc:
            return {"text": str(exc), "rows": [nav(callback("p", kind, metric, user=user))], "expect": data}
        period = f"d{dates[0].replace('-', '')}-{dates[-1].replace('-', '')}"
        stage, page = "v", 0
        data = callback(stage, kind, metric, period, user, page)
    local = local_screen(data)
    if local:
        return local
    dates, rnd, label = _period(period, now)
    with short_gpt_read() as cur:
        tid, _name = choose_tournament(Question(tournament="рпл" if kind == "rpl" else "кубок россии"), read(cur, "SELECT tournament_id, tournament_name FROM gpt_safe.tournaments ORDER BY tournament_id"))
        if stage == "r":
            rounds = read(cur, """
                SELECT r.round_number, MIN(m.kickoff_time), MAX(m.kickoff_time)
                FROM gpt_safe.calendar_rounds r JOIN gpt_safe.matches m
                  ON m.match_id = r.match_id AND m.tournament_id = r.tournament_id
                WHERE r.tournament_id = %s GROUP BY r.round_number ORDER BY r.round_number DESC
            """, (tid,))
            count = max(1, (len(rounds) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(page, count - 1)
            rows = [[button(f"{number} тур · {start.astimezone(MSK):%d.%m}–{end.astimezone(MSK):%d.%m.%Y}", callback("v", kind, metric, f"r{number}", user))] for number, start, end in rounds[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
            return {"text": f"📅 Туры · {KINDS[kind]}\n\nВыберите тур." + ("\nТуров пока нет." if not rounds else "") + "\nСборная и Суперкубок вне туров.", "rows": rows + _pagination(stage, kind, metric, period, user, page, count) + [nav(callback("p", kind, metric, user=user))]}
        if stage == "u":
            users = read(cur, "SELECT user_id, username FROM gpt_safe.tournament_rankings WHERE tournament_id = %s ORDER BY username", (tid,))
            count = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(page, count - 1)
            rows = [[button(name[:50], callback("v", kind, metric, period, uid))] for uid, name in users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
            return {"text": f"👤 Участник · {KINDS[kind]}\n\nВыберите участника." + ("\nСписок пока пуст." if not users else ""), "rows": rows + _pagination(stage, kind, metric, period, user, page, count) + [nav("ui:s:player")]}
        if stage != "v":
            raise ValueError("invalid_ui_stage")
        player = None
        if user:
            users = read(cur, "SELECT username FROM gpt_safe.tournament_rankings WHERE user_id = %s AND tournament_id = %s", (user, tid))
            if not users:
                return {"text": "Участник больше недоступен. Выберите другого.", "rows": [nav(callback("u", kind, "card"))]}
            player = users[0][0]
        if metric == "card":
            ranking = _ranking(cur, tid)
            index = next((i for i, row in enumerate(ranking) if row[0] == user), None)
            if index is None:
                return {"text": "Выберите участника.", "rows": [nav(callback("u", kind, "card"))]}
            _, name, points, forecasts, exact, *_ = ranking[index]
            lines = [f"👤 {name} · {KINDS[kind]}", "Весь турнир", "", f"🏆 Место: {index + 1} из {len(ranking)}", f"Очки: {points}", f"До лидера: {ranking[0][2] - points} очк.", f"Прогнозов с очками: {forecasts}", f"Точных счетов: {exact}"]
            if not any(row[3] for row in ranking):
                lines = [f"👤 {name} · {KINDS[kind]}", "", "Завершённых прогнозов пока нет. Место не определено."]
            rows = [[button("Очки за период", callback("p", kind, "points", user=user)), button("Последние матчи", callback("v", kind, "recent", user=user))],
                    [button("Победы в турах", callback("v", kind, "round_wins", user=user)), button("Серия без точного", callback("v", kind, "streak", user=user))],
                    [button("Прогнозы", callback("p", kind, "predictions", user=user))], nav(callback("u", kind, "card"))]
            active_users = read(cur, "SELECT user_id FROM gpt_safe.users WHERE user_id = %s", (user,))
            if not active_users:
                lines.append("Архивный участник. Доступна итоговая таблица; подробные прогнозы закрыты.")
                rows = [nav(callback("u", kind, "card"))]
            return {"text": "\n".join(lines), "rows": rows}
        participants = None
        if metric == "ranking" and with_players:
            ranking = _ranking(cur, tid)
            participants = [{"place": i, "user_id": row[0], "name": row[1]} for i, row in enumerate(ranking[:MAX_ROWS], 1)]
            lines = [f"{i}. {row[1]}: {row[2]} очк." for i, row in enumerate(ranking[:MAX_ROWS], 1)]
            if not ranking or not any(row[3] for row in ranking):
                lines.insert(0, "Нет завершённых прогнозов за выбранные условия; нули не означают сыгранные матчи.")
            if len(ranking) > MAX_ROWS:
                lines.append("Показаны первые 200 участников. Уточните игрока.")
        elif metric == "recent":
            if not player:
                raise ValueError("player_required")
            recent = read(cur, f"""
                SELECT m.home_team, m.away_team, m.kickoff_time, p.points
                FROM gpt_safe.predictions p JOIN gpt_safe.matches m
                  ON m.match_id = p.match_id AND m.tournament_id = p.tournament_id
                WHERE m.tournament_id = %s AND p.user_id = %s AND {FINISHED}
                ORDER BY m.kickoff_time DESC, m.match_id DESC LIMIT 5
            """, (tid, user))
            lines = [f"{start.astimezone(MSK):%d.%m.%Y} · {home} — {away}\n{points if points is not None else 'ещё не начислено'} очк." for home, away, start, points in recent] or ["Завершённых прогнозов пока нет."]
        else:
            q = Question(intent=metric, tournament=kind, dates=dates, round_number=rnd, player=player, points=-1 if metric == "hits" else 10)
            lines, _pending = execute_question(cur, q, tid, now)
        if rnd:
            bounds = read(cur, """SELECT MIN(m.kickoff_time), MAX(m.kickoff_time)
                FROM gpt_safe.calendar_rounds r JOIN gpt_safe.matches m
                  ON m.match_id = r.match_id AND m.tournament_id = r.tournament_id
                WHERE r.tournament_id = %s AND r.round_number = %s""", (tid, rnd))
            if bounds and bounds[0][0]:
                label += f" · {bounds[0][0].astimezone(MSK):%d.%m}–{bounds[0][1].astimezone(MSK):%d.%m.%Y}"
    # Telegram messages have a length limit; the app can show the whole table.
    # Explanatory lines must not push participants onto a second app page.
    pages = _pages(lines) if paginate else ["\n".join(lines)]
    page = min(page, len(pages) - 1)
    title = METRICS.get(metric, TITLES.get(metric, "Последние матчи"))
    heading = f"{title} · {KINDS[kind]}\n{label} · МСК" + (f"\n👤 {player}" if player else "")
    if metric == "ranking":
        heading = f"{title} · {KINDS[kind]}"
    if metric in {"ranking", "points"} and period != "all":
        heading += "\nСостав за период: действующие участники ТОТИШа."
    if len(pages) > 1:
        heading += f"\nСтраница {page + 1}/{len(pages)}"
    rows = _pagination("v", kind, metric, period, user, page, len(pages))
    actions = [button("↻ Обновить", callback("v", kind, metric, period, user, page))]
    if metric not in {"recent", "ranking"}:
        actions.append(button("Другой период", callback("p", kind, metric, user=user)))
    rows += [actions]
    if user:
        rows += [nav(callback("v", kind, "card", user=user))]
    elif metric == "ranking":
        rows += [nav("ui:s:ranking")]
    else:
        rows += [nav(callback("p", kind, metric))]
    result = {"text": heading + "\n\n" + pages[page], "rows": rows}
    if participants is not None:
        result["participants"] = participants
    return result
