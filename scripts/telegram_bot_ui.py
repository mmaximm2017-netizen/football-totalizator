"""Dependency-free Telegram navigation shared by host and read-only helper."""
import re

TITLES = {"ranking": "🏆 Таблица", "matches": "⚽ Матчи", "predictions": "🎯 Прогнозы", "stats": "📊 Статистика", "player": "👤 Участник"}
METRICS = {"points": "Очки", "round_wins": "Победы в турах", "average": "Средние очки", "record": "Рекорд за матч", "hits": "Точные счета", "streak": "Серии без точного счёта"}
KINDS = {"rpl": "РПЛ", "cup": "Кубок России"}


def button(text, data):
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_too_long")
    return {"text": text, "callback_data": data}


def nav(back="adm:main"):
    return [button("‹ Назад", back), button("🏠 Меню", "adm:main")]


def callback(stage, kind, metric, period="all", user=0, page=0):
    return f"ui:{stage}:{kind}:{metric}:{period}:{user}:{page}"


def parse(data):
    match = re.fullmatch(r"ui:([pvrud]):(rpl|cup):([a-z_]+):(all|today|yesterday|r[1-9][0-9]{0,3}|d[0-9]{8}-[0-9]{8}):([0-9]{1,10}):([0-9]{1,4})", data)
    if not match:
        raise ValueError("invalid_ui_callback")
    stage, kind, metric, period, user, page = match.groups()
    if metric not in {*METRICS, "ranking", "matches", "predictions", "card", "recent"}:
        raise ValueError("invalid_ui_metric")
    return stage, kind, metric, period, int(user), int(page)


def local_screen(data):
    if data == "ui:tools":
        return {"text": "⚙️ Администрирование\n\nВыберите раздел.", "rows": [
            [button("⚽ Сегодня", "adm:today"), button("📅 Календарь", "adm:calendar")],
            [button("🚨 Проблемы", "adm:problems"), button("⚙️ Система", "adm:system")], nav()]}
    if data.startswith("ui:s:") and data[5:] in TITLES:
        section = data[5:]
        return {"text": TITLES[section] + "\n\nВыберите турнир.", "rows": [
            [button(label, f"ui:t:{section}:{kind}") for kind, label in KINDS.items()], nav()]}
    match = re.fullmatch(r"ui:t:(ranking|matches|predictions|stats|player):(rpl|cup)", data)
    if match:
        section, kind = match.groups()
        if section == "stats":
            items = list(METRICS.items())
            return {"text": f"📊 Статистика · {KINDS[kind]}\n\nЧто показать?", "rows": [
                [button(label, callback("p", kind, metric)) for metric, label in items[i:i + 2]] for i in range(0, len(items), 2)
            ] + [nav("ui:s:stats")]}
        if section == "player":
            return {"next": callback("u", kind, "card")}
        if section == "ranking":
            return {"next": callback("v", kind, "ranking")}
        return {"next": callback("p", kind, section)}
    try:
        stage, kind, metric, period, user, page = parse(data)
    except ValueError:
        return None
    if metric == "ranking" and (stage != "v" or period != "all" or user):
        return {"next": callback("v", kind, "ranking", page=page if stage == "v" else 0)}
    if stage == "p":
        section = "stats" if metric in METRICS else metric if metric in TITLES else "player"
        rows = [[button("Сегодня", callback("v", kind, metric, "today", user)), button("Вчера", callback("v", kind, metric, "yesterday", user))],
                [button("Выбрать тур", callback("r", kind, metric, user=user)), button("Свои даты", callback("d", kind, metric, user=user))]]
        if metric not in {"matches", "predictions", "recent"}:
            rows.append([button("Весь турнир", callback("v", kind, metric, user=user))])
        rows.append(nav(callback("v", kind, "card", user=user) if user else f"ui:t:stats:{kind}" if section == "stats" else f"ui:s:{section}"))
        return {"text": f"{METRICS.get(metric, TITLES.get(metric, '👤 Участник'))} · {KINDS[kind]}\n\nВыберите период. Время МСК.", "rows": rows}
    if stage == "d":
        return {"text": f"📅 Свои даты · {KINDS[kind]}\n\nНапишите дату или период:\n17.10.2026\nс 17.10.2026 по 19.10.2026\n\nДаты без года относятся к текущему году. Время МСК.", "rows": [nav(callback("p", kind, metric, user=user))], "expect": data}
    return None
