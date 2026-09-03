from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_all(path, replacements):
    text = path.read_text(encoding="utf-8-sig")
    original = text
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f"Expected text not found in {path}: {old!r}")
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")


replace_all(ROOT / "app/routes/admin_common.py", {
    'flash("������ ��������", "error")': 'flash("Доступ запрещён", "error")',
})

replace_all(ROOT / "app/routes/admin_actions.py", {
    'flash("����������� ��������", "error")': 'flash("Неизвестное действие", "error")',
})

replace_all(ROOT / "app/routes/predictions.py", {
    '# ������ �����: ������� ������� � deadline': '# Прогнозы открываются для просмотра только после дедлайна.',
})

replace_all(ROOT / "app/routes/admin_tournaments.py", {
    'flash("���� �� ������", "error")': 'flash("Матч не указан", "error")',
    'return "���� �� ������", 404': 'return "Матч не найден", 404',
    '            ���� #{match[0]}:\n            ���� {match[1]}:{match[2]}\n            (��������� {updated} �������)': '            Матч #{match[0]}:\n            счёт {match[1]}:{match[2]}\n            (пересчитано {updated} прогнозов)',
    '<th>�����</th>': '<th>Игрок</th>',
    '<th>�������</th>': '<th>Прогноз</th>',
    '<th>����</th>': '<th>Очки</th>',
    'result += "}</table>"': 'result += "</table>"',
    'flash(f"������ ���������: {e}", "error")': 'flash(f"Ошибка пересчёта: {e}", "error")',
    'f"���������� {updated} ������"': 'f"Обновлено матчей: {updated}"',
    'flash(f"������ ��������: {e}", "error")': 'flash(f"Ошибка перевода: {e}", "error")',
    'flash("������� �������� �������", "error")': 'flash("Укажите название турнира", "error")',
    'flash("������ � ����� ��������� ��� ����������", "error")': 'flash("Турнир с таким названием уже существует", "error")',
    'f"������ �{name}� ������"': 'f"Турнир «{name}» создан"',
    'flash(f"������ �������� �������: {e}", "error")': 'flash(f"Ошибка создания турнира: {e}", "error")',
    'flash("������ �� ������", "error")': 'flash("Турнир не указан", "error")',
    'flash("������ ������� �������� ������", "error")': 'flash("Нельзя удалить активный турнир", "error")',
    'flash(f"������ #{tid} �����", "success")': 'flash(f"Турнир #{tid} удалён", "success")',
    'flash(f"������: {e}", "error")': 'flash(f"Ошибка: {e}", "error")',
})

replace_all(ROOT / "app/routes/admin_matches.py", {
    'flash("����� ���� ��� ����������", "error")': 'flash("Такой матч уже существует", "error")',
    'f"���� {home} � {away} ��������"': 'f"Матч {home} — {away} добавлен"',
    'flash(f"������: {e}", "error")': 'flash(f"Ошибка: {e}", "error")',
    'flash("������������ ����", "error")': 'flash("Некорректный счёт", "error")',
    'flash("���� �� ������", "error")': 'flash("Матч не найден", "error")',
    '"��������� �����, ���� �����������"': '"Результат сохранён, очки пересчитаны"',
    'flash("������������ ������ �����", "error")': 'flash("Некорректные данные матча", "error")',
    'f"���� #{match_id} ��������: {h}:{a}"': 'f"Матч #{match_id} завершён: {h}:{a}"',
    'f"��������� �������: {home_score}:{away_score}"': 'f"Результат исправлен: {home_score}:{away_score}"',
    'flash("��������� ��� ����", "error")': 'flash("Заполните все поля", "error")',
    'flash("������������ ���� ��� �����", "error")': 'flash("Некорректные дата или время", "error")',
    'flash("��� FINISHED ����� ��������� kickoff/deadline ��������� ��� ������������", "error")': 'flash("Для FINISHED нельзя менять дату или дедлайн без корректного результата", "error")',
    'f"���� #{match_id} �������"': 'f"Матч #{match_id} обновлён"',
    'flash("�� ������ match_id", "error")': 'flash("Не указан match_id", "error")',
    'f"���� #{match_id} �����"': 'f"Матч #{match_id} удалён"',
    'flash(f"������ ��������: {e}", "error")': 'flash(f"Ошибка удаления: {e}", "error")',
})

match_service = ROOT / "app/services/match_service.py"
text = match_service.read_text(encoding="utf-8-sig")
pattern = re.compile(
    r'def get_tournament_id_by_name\(cur, name\):\n.*?(?=def should_update\(\):)',
    re.S,
)
replacement = '''def get_tournament_id_by_name(cur, name):\n    cur.execute(\n        "SELECT id FROM tournaments WHERE name = %s ORDER BY id DESC LIMIT 1",\n        (name,),\n    )\n    row = cur.fetchone()\n    return row[0] if row else None\n\n'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise RuntimeError("Could not replace legacy tournament-name fallback")
match_service.write_text(text, encoding="utf-8")

admin_view = ROOT / "app/services/admin_view_service.py"
text = admin_view.read_text(encoding="utf-8-sig")
pattern = re.compile(
    r'def normalize_league_key\(raw_value\):\n.*?\n\ndef prepare_admin_view_data\(cur\):',
    re.S,
)
replacement = '''def normalize_league_key(raw_value):\n    """Normalize supported league aliases into stable internal keys."""\n    if raw_value is None:\n        return "other"\n\n    lowered = str(raw_value).strip().lower()\n    alias_map = {\n        "rpl": "rpl",\n        "rfpl": "rpl",\n        "рпл": "rpl",\n        "рпл 2026": "rpl",\n        "wc2026": "wc2026",\n        "wc-2026": "wc2026",\n        "чм-2026": "wc2026",\n        "чм 2026": "wc2026",\n        "rcup": "rcup",\n        "кубок россии": "rcup",\n        "other": "other",\n        "россия": "other",\n    }\n    return alias_map.get(lowered, "other")\n\n\ndef prepare_admin_view_data(cur):'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise RuntimeError("Could not replace legacy league normalization")
admin_view.write_text(text, encoding="utf-8")

remaining = []
for path in (ROOT / "app").rglob("*.py"):
    text = path.read_text(encoding="utf-8-sig")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "\ufffd" in line:
            remaining.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")

if remaining:
    raise RuntimeError("Replacement characters remain:\n" + "\n".join(remaining))

print("Runtime mojibake cleanup complete; no U+FFFD remains in app/*.py")
