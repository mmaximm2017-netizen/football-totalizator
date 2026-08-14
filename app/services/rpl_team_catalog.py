import re


RPL_TEAM_ALIASES = {
    "Спартак": ("Спартак", "Спартак Москва", "ФК Спартак Москва"),
    "Динамо": ("Динамо", "Динамо Москва", "ФК Динамо Москва"),
    "ЦСКА": ("ЦСКА", "ЦСКА Москва", "ПФК ЦСКА"),
    "Зенит": ("Зенит", "Зенит Санкт-Петербург", "ФК Зенит"),
    "Локомотив": ("Локомотив", "Локомотив Москва", "ФК Локомотив Москва"),
    "Краснодар": ("Краснодар", "ФК Краснодар"),
    "Ахмат": ("Ахмат", "Ахмат Грозный", "ФК Ахмат"),
    "Ростов": ("Ростов", "ФК Ростов"),
    "Рубин": ("Рубин", "Рубин Казань", "ФК Рубин"),
    "Крылья Советов": ("Крылья Советов", "Крылья Советов Самара", "ПФК Крылья Советов"),
    "Пари НН": ("Пари НН", "Пари Нижний Новгород", "Нижний Новгород", "ФК Пари НН"),
    "Оренбург": ("Оренбург", "ФК Оренбург"),
    "Балтика": ("Балтика", "Балтика Калининград", "ФК Балтика"),
    "Сочи": ("Сочи", "ФК Сочи"),
    "Динамо Мх": ("Динамо Мх", "Динамо Махачкала", "ФК Динамо Махачкала"),
    "Акрон": ("Акрон", "Акрон Тольятти", "ФК Акрон"),
}


def normalize_team_text(value):
    value = str(value or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"[‐‑‒–—−-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:фк|пфк)\s+", "", value)
    return value.strip(" .,:;|[]()")


def _build_index():
    index = {}
    for canonical, aliases in RPL_TEAM_ALIASES.items():
        for alias in aliases:
            index.setdefault(normalize_team_text(alias), set()).add(canonical)
    return index


_TEAM_INDEX = _build_index()
RPL_CANONICAL_TEAMS = tuple(RPL_TEAM_ALIASES)


def match_rpl_team(value):
    normalized = normalize_team_text(value)
    matches = _TEAM_INDEX.get(normalized, set())
    if len(matches) == 1:
        return next(iter(matches)), "ready"
    if len(matches) > 1:
        return None, "needs_review"
    return None, "needs_review"
