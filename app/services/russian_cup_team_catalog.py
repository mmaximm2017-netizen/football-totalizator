"""Explicit Russian Cup team catalog.

Names are project-confirmed canonical values; aliases are only variants already
present in team_data/logo data or existing shared team mappings.
"""
from app.services.rpl_team_catalog import normalize_team_text

RUSSIAN_CUP_TEAM_ALIASES = {
    "Спартак": ("Спартак", "Спартак Москва", "ФК Спартак Москва"),
    "Динамо": ("Динамо", "Динамо Москва", "ФК Динамо Москва"),
    "Динамо Мх": ("Динамо Мх", "Динамо Махачкала", "ФК Динамо Махачкала"),
    "ЦСКА": ("ЦСКА", "ЦСКА Москва", "ПФК ЦСКА"),
    "Зенит": ("Зенит", "Зенит Санкт-Петербург", "ФК Зенит"),
    "Локомотив": ("Локомотив", "Локомотив Москва", "ФК Локомотив Москва"),
    "Краснодар": ("Краснодар", "ФК Краснодар"),
    "Ахмат": ("Ахмат", "Ахмат Грозный", "ФК Ахмат"),
    "Ростов": ("Ростов", "ФК Ростов"),
    "Рубин": ("Рубин", "Рубин Казань", "ФК Рубин"),
    "Крылья Советов": ("Крылья Советов", "Крылья Советов Самара", "ПФК Крылья Советов"),
    "Оренбург": ("Оренбург", "ФК Оренбург"),
    "Балтика": ("Балтика", "Балтика Калининград", "ФК Балтика"),
    "Акрон": ("Акрон", "Акрон Тольятти", "ФК Акрон"),
    "Родина": ("Родина", "Родина Москва"),
    "Факел": ("Факел", "Факел Воронеж"),
}

def match_russian_cup_team(value):
    key = normalize_team_text(value)
    matches = {canonical for canonical, aliases in RUSSIAN_CUP_TEAM_ALIASES.items()
               if key in {normalize_team_text(alias) for alias in aliases}}
    return (next(iter(matches)), "ready") if len(matches) == 1 else (None, "needs_review")

RUSSIAN_CUP_CANONICAL_TEAMS = tuple(RUSSIAN_CUP_TEAM_ALIASES)
RUSSIAN_CUP_ALIAS_TOKENS = {token for aliases in RUSSIAN_CUP_TEAM_ALIASES.values()
                             for alias in aliases for token in normalize_team_text(alias).split()}
