"""Russian rule-based analytics. Fixed SELECTs only; no model/API/SQL input."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from app.gpt_db import short_gpt_read
from app.models.scoring import FINISHED_STATUSES_SQL
from app.utils import MSK, RU_MONTHS_GENITIVE, parse_datetime

HELP = """Вопросы ТОТИШу (без ИИ API):
• Кто сейчас на первом месте в РПЛ?
• Покажи таблицу РПЛ.
• Сколько очков набрал Бек в РПЛ?
• Кто сколько набрал за 17, 18 и 19 октября в РПЛ?
• Кто поставил на Локомотив — Краснодар в РПЛ?
• Как поставили на сегодняшние матчи РПЛ?
• Кто угадал точный счёт сегодня в РПЛ?
• Когда Макс Зенит последний раз получал 10 очков в РПЛ?
• Очки игроков в девятом туре РПЛ.
• Какие матчи сегодня в РПЛ?
• Кто не сделал прогноз сегодня в РПЛ?
• Результат Бека с 17 октября по 19 октября 2026 в РПЛ.

Уточнения: «Кубок России», «за вчера», имя или номер из списка.
Даты без года: текущий год. Время МСК. /reset сбрасывает контекст.
До дедлайна прогнозы и их отсутствие закрыты."""
UNSUPPORTED = "Этот запрос пока не поддерживается. Попробуйте: таблица РПЛ; очки Бека в РПЛ; матчи сегодня в РПЛ. /help — примеры."
MAX_ROWS = 200
TTL = 900
SESSIONS = {}
DATE_SQL = "(m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date"
FINISHED = f"UPPER(m.status) IN {FINISHED_STATUSES_SQL} AND m.kickoff_time <= CURRENT_TIMESTAMP"


def normal(value):
    return re.sub(r"[^а-яa-z0-9]+", " ", value.lower().replace("ё", "е")).strip()


def corrected(value):
    aliases = {"ачков": "очков", "очковв": "очков", "таблцу": "таблицу", "таблиццу": "таблицу", "севодня": "сегодня", "сегодя": "сегодня"}
    return " ".join(aliases.get(w, w) for w in normal(value).split())


class Clarify(ValueError):
    pass


@dataclass
class Question:
    intent: str | None = None
    tournament: str | None = None
    dates: list[str] = field(default_factory=list)
    round_number: int | None = None
    player: str | None = None
    match_id: int | None = None
    teams: list[str] = field(default_factory=list)
    points: int = 10
    original: str = ""


def parse_dates(text, now):
    text = text.lower().replace("ё", "е")
    for typo in ("севодня", "сегодя"):
        text = re.sub(rf"\b{typo}\b", "сегодня", text)
    # A shared month in «с 17 по 19 октября» applies to both boundaries.
    months_pattern = "|".join(RU_MONTHS_GENITIVE.values())
    text = re.sub(rf"\bс\s+(\d{{1,2}})\s+по\s+(\d{{1,2}})\s+({months_pattern})(?:\s+(\d{{4}}))?", lambda m: f"с {m[1]} {m[3]} {m[4] or now.year} по {m[2]} {m[3]} {m[4] or now.year}", text)
    today = now.astimezone(MSK).date()
    relative = []
    for pattern, offset in ((r"\bпозавчера\b", -2), (r"\bсегодня\b|\bсегодняшн\w*", 0), (r"\bвчера\b", -1), (r"\bзавтра\b", 1)):
        if re.search(pattern, text):
            relative.append((today + timedelta(days=offset)).isoformat())
    if relative:
        if re.search(rf"\d{{4}}-\d{{2}}|\d{{1,2}}\.\d{{1,2}}|\d+\s+(?:{months_pattern})", text):
            raise Clarify("Не смешивайте относительные и числовые даты. Укажите все нужные дни с месяцем и годом.")
        return sorted(set(relative))
    values = []
    try:
        iso_pattern = r"\b\d{4}-\d{2}-\d{2}\b"
        numeric_pattern = r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b"
        iso = re.findall(iso_pattern, text)
        numeric = re.findall(numeric_pattern, text)
        if iso:
            values = [date.fromisoformat(v) for v in iso]
            remainder = re.sub(iso_pattern, "", text)
        elif numeric:
            values = [date(int(y or today.year), int(m), int(d)) for d, m, y in numeric]
            remainder = re.sub(numeric_pattern, "", text)
        else:
            months = "|".join(RU_MONTHS_GENITIVE.values())
            month_pattern = rf"((?:\d{{1,2}}\s*(?:,|и|-|–)\s*)*\d{{1,2}})\s+({months})(?:\s+(\d{{4}}))?"
            for days, month, year in re.findall(month_pattern, text):
                month_num = next(n for n, name in RU_MONTHS_GENITIVE.items() if name == month)
                numbers = [int(v) for v in re.findall(r"\d+", days)]
                if re.search(r"[-–]", days):
                    numbers = list(range(numbers[0], numbers[-1] + 1))
                    if not numbers:
                        raise ValueError
                values.extend(date(int(year or today.year), month_num, n) for n in numbers)
            remainder = re.sub(month_pattern, "", text)
        if not values:
            if re.search(r"\b(за|с|по)\s+\d|недел|месяц|период|год|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр", text):
                raise Clarify("Укажите даты: 17, 18 и 19 октября 2026 или с 17.10.2026 по 19.10.2026.")
            return []
        # Never use only the parseable part of a mixed/unsupported period.
        without_metrics = re.sub(r"\b\d+\s*(?:й\s+тур\w*|тур\w*|очк\w*)", "", remainder)
        if re.search(rf"{months_pattern}|недел|месяц|\d{{4}}-\d{{2}}|\.\d+|\b(?:и|по)\s+\d{{1,2}}\b|\b\d+\b", without_metrics):
            raise Clarify("Не все даты распознаны. Укажите весь список в одном формате, например: 17, 18 и 19 октября 2026.")
        if re.search(r"\bдо\s+\d|\b(?:с|по)\s+\d", text) and not (re.search(r"\bс\b.+\bпо\b", text) and len(values) == 2):
            raise Clarify("Укажите обе границы периода: с 17.10.2026 по 19.10.2026.")
        if re.search(r"\bс\b.+\bпо\b", text) and len(values) == 2:
            if values[0] > values[1] or (values[1] - values[0]).days > 366:
                raise ValueError
            values = [values[0] + timedelta(days=n) for n in range((values[1] - values[0]).days + 1)]
        if len(values) > 367:
            raise ValueError
        return sorted({v.isoformat() for v in values})
    except Clarify:
        raise
    except ValueError:
        raise Clarify("Некорректные даты. Укажите период до 366 дней, например с 17.10.2026 по 19.10.2026.") from None


def parse_question(text, previous=None, now=None):
    n = corrected(text)
    intent = None
    if re.search(r"на первом|на 1 месте|первый|лидер", n):
        intent = "leader"
    elif "таблиц" in n or "рейтинг" in n:
        intent = "ranking"
    elif "последн" in n and re.search(r"очк|очков", n):
        intent = "last"
    elif "точн" in n and ("счет" in n or "угад" in n):
        intent = "exact"
    elif re.search(r"не (сделал|поставил|дал)|без прогноза", n):
        intent = "missing"
    elif re.search(r"постав|прогноз", n):
        intent = "predictions"
    elif re.search(r"очк|очков|набрал|результат", n):
        intent = "points"
    elif "матч" in n:
        intent = "matches"
    q = Question(tournament=previous.tournament if previous else None) if intent else Question(**vars(previous)) if previous else Question()
    q.intent = intent or q.intent
    if intent:
        q.original = text
    tournaments = []
    if re.search(r"\bрпл\b|чемпионат\w* росс", n):
        tournaments.append("рпл")
    if re.search(r"куб\w* росс|\bкубок\b", n):
        tournaments.append("кубок россии")
    if re.search(r"\bчм\b|чемпионат\w* мира", n):
        tournaments.append("чм")
    if len(tournaments) > 1:
        raise Clarify("Выберите один турнир: РПЛ, Кубок России или ЧМ.")
    if tournaments:
        q.tournament = tournaments[0]
    try:
        dates = parse_dates(text, now or datetime.now(MSK))
    except Clarify as exc:
        exc.question = q
        raise
    if dates:
        q.dates, q.round_number, q.match_id, q.teams = dates, None, None, []
    words = {"первом": 1, "втором": 2, "третьем": 3, "четвертом": 4, "пятом": 5, "шестом": 6, "седьмом": 7, "восьмом": 8, "девятом": 9, "десятом": 10}
    rnd = re.search(r"\b(\d{1,2})(?:\s*[йм])?\s+тур", n)
    word_round = next((v for k, v in words.items() if re.search(rf"\b{k}\s+тур", n)), None)
    if rnd or word_round:
        q.round_number = int(rnd[1]) if rnd else word_round
        q.dates, q.match_id, q.teams = dates, None, []
    elif "тур" in n and not tournaments and "турнир" not in n:
        raise Clarify("Укажите номер тура, например: в 9 туре РПЛ.")
    pts = re.search(r"\b(\d{1,2})\s+очк", n)
    if pts:
        q.points = int(pts[1])
    return q


def candidates(text, choices):
    """Only literal names select; fuzzy names require confirmation."""
    n = normal(text)
    exact = [v for v in choices if f" {normal(v)} " in f" {n} "]
    exact = [v for v in exact if not any(normal(v) != normal(w) and normal(v) in normal(w) for w in exact)]
    if exact:
        return exact, True
    words = n.split()
    scored = []
    for value in choices:
        target = normal(value)
        width = len(target.split())
        score = max((SequenceMatcher(None, " ".join(words[i:i + width]), target).ratio() for i in range(len(words))), default=0)
        if score >= .72:
            scored.append((score, value))
    return [v for _, v in sorted(scored, reverse=True)[:5]], False


def read(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def choose_tournament(q, tournaments):
    aliases = {"рпл": "чемпионат россии", "кубок россии": "кубок россии", "чм": "чемпионат мира"}
    wanted = aliases.get(q.tournament, normal(q.tournament or ""))
    found = [r for r in tournaments if wanted and (normal(r[1]) == wanted or normal(r[1]).startswith(wanted + " ") or str(r[0]) == wanted)]
    if len(found) != 1:
        raise Clarify("Уточните турнир (название или ID):\n" + "\n".join(f"{r[0]}: {r[1]}" for r in tournaments))
    return found[0]


def match_filters(q, tid):
    clauses, params = ["m.tournament_id = %s"], [tid]
    if q.dates:
        clauses.append(f"{DATE_SQL} = ANY(%s::date[])")
        params.append(q.dates)
    if q.round_number:
        clauses.append("m.round_number = %s")
        params.append(q.round_number)
    if len(q.teams) == 1:
        clauses.append("(m.home_team = %s OR m.away_team = %s)")
        params.extend(q.teams * 2)
    elif q.teams:
        clauses.append("((m.home_team = %s AND m.away_team = %s) OR (m.home_team = %s AND m.away_team = %s))")
        params.extend(q.teams + q.teams[::-1])
    if q.match_id:
        clauses.append("m.match_id = %s")
        params.append(q.match_id)
    return " AND ".join(clauses), params


def resolve_player(cur, text, q, pending):
    if pending:
        kind, choices = pending
        selected = None
        if text.strip().isdigit() and 1 <= int(text.strip()) <= len(choices):
            selected = choices[int(text.strip()) - 1]
        else:
            matched, exact = candidates(text, choices)
            if exact and len(matched) == 1:
                selected = matched[0]
        if selected is None:
            return pending
        if kind == "player":
            q.player = selected
        else:
            q.match_id = int(selected.split(" · ")[-1])
        return None
    if q.intent in {"points", "last"}:
        users = [r[0] for r in read(cur, "SELECT username FROM gpt_safe.users ORDER BY username")]
        # A new explicit name in a short follow-up replaces the previous player.
        found, exact = candidates(text, users)
        if q.player and not exact:
            return None
        if not found:
            found, exact = candidates(q.original, users)
        if len(found) == 1 and exact:
            q.player = found[0]
        elif found:
            return "player", found
        elif q.intent == "last" or not re.search(r"кто сколько|игрок|участник|все", normal(q.original)):
            return "player", users
    return None


def execute_question(cur, q, tid, now):
    where, params = match_filters(q, tid)
    if q.intent in {"leader", "ranking", "points"}:
        # Same tie breakers as ranking_service, using safe participant views.
        rows = read(cur, f"""
            SELECT u.username, COALESCE(SUM(p.points), 0), COUNT(p.match_id),
                   COUNT(*) FILTER (WHERE p.points >= 10) AS exact,
                   COUNT(*) FILTER (WHERE p.points BETWEEN 7 AND 8) AS diff,
                   COUNT(*) FILTER (WHERE p.points = 3) AS outcome
            FROM gpt_safe.users u
            LEFT JOIN (
                SELECT p.* FROM gpt_safe.predictions p
                JOIN gpt_safe.matches m ON m.match_id = p.match_id AND m.tournament_id = p.tournament_id
                WHERE {where} AND {FINISHED}
            ) p ON p.user_id = u.user_id
            WHERE (%s::text IS NULL OR u.username = %s)
            GROUP BY u.user_id, u.username
            ORDER BY 2 DESC, exact DESC, diff DESC, outcome DESC, u.username
            LIMIT %s
        """, tuple(params + [q.player, q.player, MAX_ROWS + 1]))
        if q.intent == "leader" and not any(r[2] for r in rows):
            return ["Нет завершённых прогнозов. Лидер по этим данным не определён."], None
        if q.intent == "leader":
            rows = rows[:1]
        lines = [f"{i}. {r[0]}: {r[1]} очк. ({r[2]} прогнозов)" for i, r in enumerate(rows[:MAX_ROWS], 1)]
        if not rows or not any(r[2] for r in rows):
            lines.insert(0, "Нет завершённых прогнозов за выбранные условия; нули не означают сыгранные матчи.")
        if len(rows) > MAX_ROWS:
            lines.append("Показаны первые 200 участников. Уточните игрока.")
        return lines, None
    if q.intent in {"last", "exact"}:
        extra = "p.points = %s AND p.username = %s" if q.intent == "last" else "p.predicted_home = m.home_score AND p.predicted_away = m.away_score"
        extra_params = [q.points, q.player] if q.intent == "last" else []
        rows = read(cur, f"""
            SELECT p.username, m.home_team, m.away_team, m.kickoff_time, p.points
            FROM gpt_safe.predictions p
            JOIN gpt_safe.matches m ON m.match_id = p.match_id AND m.tournament_id = p.tournament_id
            WHERE {where} AND {FINISHED} AND {extra}
            ORDER BY m.kickoff_time DESC, m.match_id DESC, p.username LIMIT %s
        """, tuple(params + extra_params + [1 if q.intent == "last" else MAX_ROWS + 1]))
        lines = [f"{r[0]} · {r[3].astimezone(MSK):%d.%m.%Y}\n{r[1]} — {r[2]}: {r[4]} очк." for r in rows[:MAX_ROWS]]
        if len(rows) > MAX_ROWS:
            lines.append("Показаны первые 200 строк. Уточните период.")
        return lines or ["Подходящих завершённых прогнозов нет."], None
    if q.intent in {"predictions", "missing"} and not (q.dates or q.round_number or q.teams or q.match_id):
        raise Clarify("За какой матч или день? Например: сегодня или Локомотив — Краснодар.")
    rows = read(cur, f"""
        SELECT m.match_id, m.home_team, m.away_team, m.kickoff_time, m.deadline, m.status
        FROM gpt_safe.matches m WHERE {where}
        ORDER BY m.kickoff_time, m.match_id LIMIT %s
    """, tuple(params + [MAX_ROWS + 1]))
    if len(rows) > MAX_ROWS:
        raise Clarify("Слишком много матчей. Уточните день или тур.")
    if q.teams and not q.match_id and len(rows) > 1:
        choices = [f"{r[1]} — {r[2]} · {r[3].astimezone(MSK):%d.%m.%Y} · {r[0]}" for r in rows]
        return ["Выберите номер матча:"] + [f"{i}. {v}" for i, v in enumerate(choices, 1)], ("match", choices)
    lines = []
    for mid, home, away, kickoff, deadline, status in rows:
        lines.append(f"{home} — {away}\n{kickoff.astimezone(MSK):%d.%m.%Y %H:%M} МСК · {status or 'нет статуса'}")
        if q.intent == "matches":
            continue
        dt = parse_datetime(deadline)
        if dt is None or dt > now:
            lines.append("🔒 Прогнозы и их отсутствие закрыты до дедлайна.")
            continue
        scores = read(cur, """
            SELECT u.username, p.predicted_home, p.predicted_away
            FROM gpt_safe.users u
            LEFT JOIN gpt_safe.predictions p ON p.user_id = u.user_id
                AND p.match_id = %s AND p.tournament_id = %s
            ORDER BY u.username LIMIT %s
        """, (mid, tid, MAX_ROWS + 1))
        for name, ph, pa in scores[:MAX_ROWS]:
            if q.intent == "missing":
                if ph is None or pa is None:
                    lines.append(f"{name}: нет прогноза")
            else:
                lines.append(f"{name}: {ph}:{pa}" if ph is not None and pa is not None else f"{name}: нет прогноза")
        if q.intent == "missing" and scores and all(ph is not None and pa is not None for _, ph, pa in scores):
            lines.append("Все сделали прогноз.")
        if len(scores) > MAX_ROWS:
            lines.append("Показаны первые 200 участников.")
    return lines or ["Матчей по выбранным условиям нет."], None


def save_session(scope, clock, q, pending):
    if len(SESSIONS) >= 32 and scope not in SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    SESSIONS[scope] = (clock, q, pending)


def answer(text, scope, now=None):
    """Authenticated host supplies scope; store filters only, with finite lifetime."""
    now = now or datetime.now(MSK)
    clock = time.monotonic()
    for key in list(SESSIONS):
        if clock - SESSIONS[key][0] > TTL:
            del SESSIONS[key]
    if not isinstance(text, str) or len(text) > 1000 or not isinstance(scope, str) or len(scope) > 100:
        return {"text": "Слишком длинный запрос. Максимум 1000 символов."}
    if text.strip().lower() in {"/help", "помощь", "справка"}:
        return {"text": HELP}
    if text.strip() == "/reset":
        SESSIONS.pop(scope, None)
        return {"text": "Контекст сброшен. " + HELP}
    previous = SESSIONS.get(scope)
    q = None
    pending = previous[2] if previous else None
    try:
        if re.search(r"\b(select|with|insert|update|delete|drop|alter|sql|удали\w*|измени\w*|исправ\w*)\b", normal(text)):
            return {"text": UNSUPPORTED}
        q = parse_question(text, previous[1] if previous else None, now)
        if not q.intent:
            return {"text": UNSUPPORTED}
        followup = previous is not None and q.original != text
        if followup and not pending and q == previous[1]:
            # Only a name/team may still change the filters. Arbitrary chatter
            # must not replay the previous query and masquerade as an answer.
            if not re.fullmatch(r"[а-яА-ЯёЁa-zA-Z0-9 -]{1,80}", text.strip()):
                return {"text": UNSUPPORTED}
        if text.strip().isdigit() and previous and not pending:
            q.tournament = text.strip()
        if not q.tournament:
            save_session(scope, clock, q, pending)
            return {"text": "Какой турнир: РПЛ, Кубок России или ЧМ?"}
        with short_gpt_read() as cur:
            tournaments = read(cur, "SELECT tournament_id, tournament_name FROM gpt_safe.tournaments ORDER BY tournament_id")
            tid, name = choose_tournament(q, tournaments)
            # Fresh questions cancel unresolved choices from earlier questions.
            if previous and q.original == text and q.original != previous[1].original:
                pending = None
            old_player = q.player
            pending = resolve_player(cur, text, q, pending)
            if pending:
                lines = ["Уточните выбор (имя или номер):"] + [f"{i}. {v}" for i, v in enumerate(pending[1], 1)]
            else:
                teams = [r[0] for r in read(cur, "SELECT home_team FROM gpt_safe.matches WHERE tournament_id = %s UNION SELECT away_team FROM gpt_safe.matches WHERE tournament_id = %s", (tid, tid)) if r[0]]
                found, exact = candidates(q.original + " " + text, teams)
                if exact and found and not q.player:
                    if len(found) > 2:
                        raise Clarify("Укажите ровно две команды или конкретную дату.")
                    q.teams = found
                elif not exact and found and q.intent in {"predictions", "missing"} and not q.dates and not q.teams and not q.match_id:
                    raise Clarify("Уточните команды: " + ", ".join(found))
                if q.intent == "matches" and not (q.dates or q.round_number or q.teams):
                    raise Clarify("За какой день или тур показать матчи? Например: сегодня.")
                if followup and not pending and q == previous[1] and q.player == old_player:
                    return {"text": UNSUPPORTED}
                lines, pending = execute_question(cur, q, tid, now)
        label = ", ".join(datetime.fromisoformat(d).strftime("%d.%m.%Y") for d in q.dates)
        if len(label) > 100:
            label = f"{q.dates[0]} … {q.dates[-1]} ({len(q.dates)} дней)"
        detail = f"\nДаты МСК: {label}" if q.dates else "" if q.round_number else "\nПериод: всё время турнира"
        if q.round_number:
            detail += f"\nТур {q.round_number}"
        if q.intent == "last":
            detail += f"\nПоследний прогноз: ровно {q.points} очк."
        save_session(scope, clock, q, pending)
        return {"text": f"🏆 {name}{detail}\nУчастники: открытый список ТОТИШа.\n\n" + "\n".join(lines)}
    except Clarify as exc:
        q = q or getattr(exc, "question", None)
        if q:
            save_session(scope, clock, q, pending)
        return {"text": str(exc)}
